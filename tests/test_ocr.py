"""Story text: STORY_OCR's reading of stored frames, its search index, and where the feed server shows it."""

import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from instadroid import config, ocr
from PIL import Image, ImageDraw, ImageFont
from shared import sqlrows
from shared.sqlrows import SqlValue

from tests.feedclient import make_app
from tests.support import fake_run, insert_story


def _story(con: sqlite3.Connection, story_id: str, scraped_at: str, username: str = "alice") -> Path:
    """A stored story with a frame on disk; its path."""
    media: str = f"stories/{story_id}.png"
    path: Path = config.MEDIA_DIR / media
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (10, 10), "white").save(path)
    insert_story(con, story_id, scraped_at, username=username, media_file=media)
    return path


def _rows(con: sqlite3.Connection, sql: str) -> list[tuple[SqlValue, ...]]:
    return [sqlrows.values(r) for r in sqlrows.fetch_all(con.execute(sql))]


def _texts(con: sqlite3.Connection) -> dict[SqlValue, SqlValue]:
    return {row[0]: row[1] for row in _rows(con, "SELECT id, text FROM stories")}


def test_clean_drops_noise_lines_and_extra_spaces() -> None:
    raw: str = "  Doors   open 7pm \n\n| ~ .\nA\n  free entry!  \n"
    assert ocr.clean(raw) == "Doors open 7pm\nfree entry!"
    assert ocr.clean("") == ""


def test_nothing_is_read_while_story_ocr_is_off(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _story(con, "s1", "2026-11-20T10:00:00+00:00")
    monkeypatch.setattr(ocr, "read_text", pytest.fail)
    assert ocr.read_stories(con) == 0
    assert _texts(con) == {"s1": None}


def test_unread_stories_are_read_newest_first_up_to_the_limit(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "STORY_OCR", True)
    _story(con, "old", "2026-11-19T10:00:00+00:00")
    _story(con, "new", "2026-11-20T10:00:00+00:00")
    _story(con, "gone", "2026-11-20T09:00:00+00:00").unlink()  # its frame was pruned or never written
    seen: list[str] = []

    def fake(path: Path) -> str:
        seen.append(path.stem)
        return f"text of {path.stem}"

    monkeypatch.setattr(ocr, "read_text", fake)
    assert ocr.read_stories(con, limit=2) == 2
    assert seen == ["new"]
    assert _texts(con) == {"old": None, "new": "text of new", "gone": ""}
    assert ocr.read_stories(con) == 1  # the backlog drains on later runs
    assert _texts(con)["old"] == "text of old"
    assert ocr.read_stories(con) == 0


def test_a_failing_tesseract_stops_the_pass_and_leaves_stories_unread(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "STORY_OCR", True)
    _story(con, "s1", "2026-11-20T10:00:00+00:00")
    _story(con, "s2", "2026-11-20T11:00:00+00:00")
    calls: list[Path] = []

    def failing(path: Path) -> None:
        calls.append(path)

    monkeypatch.setattr(ocr, "read_text", failing)
    assert ocr.read_stories(con) == 0
    assert len(calls) == 1
    assert _texts(con) == {"s1": None, "s2": None}


def test_read_text_without_tesseract_is_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))  # no tesseract on it
    assert ocr.read_text(tmp_path / "frame.png") is None


def test_read_text_cleans_tesseract_s_output_with_one_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[list[str]] = fake_run(monkeypatch, stdout="  Doors   open \n| ~\n")
    monkeypatch.setattr(config, "STORY_OCR_LANG", "eng+deu")
    assert ocr.read_text(tmp_path / "frame.png") == "Doors open"
    assert commands == [["tesseract", str(tmp_path / "frame.png"), "stdout", "-l", "eng+deu"]]


@pytest.mark.parametrize(
    "raises",
    [PermissionError("tesseract: Permission denied"), subprocess.TimeoutExpired("tesseract", ocr.TIMEOUT)],
)
def test_read_text_that_can_t_run_or_hangs_is_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], raises: Exception
) -> None:
    _ = fake_run(monkeypatch, raises=raises)
    assert ocr.read_text(tmp_path / "frame.png") is None
    assert "story OCR could not run" in capsys.readouterr().out


def test_read_text_that_tesseract_fails_on_is_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _ = fake_run(monkeypatch, returncode=1, stderr="Error, cannot read input file\n")
    assert ocr.read_text(tmp_path / "frame.png") is None
    assert "story OCR failed on frame.png: Error, cannot read input file" in capsys.readouterr().out


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract isn't installed")
def test_read_text_reads_a_rendered_frame(tmp_path: Path) -> None:
    img: Image.Image = Image.new("RGB", (1080, 400), "white")
    ImageDraw.Draw(img).text(
        (40, 150), "Gallery opening tonight", fill="black", font=ImageFont.load_default(size=60)
    )
    path: Path = tmp_path / "frame.png"
    img.save(path)
    assert ocr.read_text(path) == "Gallery opening tonight"


def test_search_index_follows_story_text(con: sqlite3.Connection) -> None:
    _story(con, "s1", "2026-11-20T10:00:00+00:00")
    _story(con, "s2", "2026-11-20T11:00:00+00:00")
    con.execute("UPDATE stories SET text = 'Gallery opening tonight' WHERE id = 's1'")
    con.execute("UPDATE stories SET text = '' WHERE id = 's2'")  # read, nothing found: not indexed
    assert _rows(con, "SELECT story_id, text FROM stories_fts") == [("s1", "Gallery opening tonight")]
    con.execute("UPDATE stories SET text = 'Closed today' WHERE id = 's1'")
    assert _rows(con, "SELECT text FROM stories_fts WHERE stories_fts MATCH 'closed'") == [("Closed today",)]
    con.execute("DELETE FROM stories WHERE id = 's1'")
    assert _rows(con, "SELECT COUNT(*) FROM stories_fts") == [(0,)]


@pytest.fixture
def client(con: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """The feed server over conftest's database (the scraper's schema), with two read stories."""
    _story(con, "s1", "2026-11-20T10:00:00+00:00", username="alice")
    _story(con, "s2", "2026-11-20T11:00:00+00:00", username="bob")
    con.execute("UPDATE stories SET text = 'Gallery opening <tonight>\nfree entry' WHERE id = 's1'")
    con.execute("UPDATE stories SET text = 'Gallery closed' WHERE id = 's2'")
    con.commit()
    return make_app(tmp_path, monkeypatch, seed="")


def test_stories_feed_shows_the_text_escaped(client: TestClient) -> None:
    body: str = client.get("/stories.json").text
    assert "Gallery opening &lt;tonight&gt;<br>free entry" in body


def test_stories_feed_etag_moves_when_a_story_is_read(client: TestClient, con: sqlite3.Connection) -> None:
    _story(con, "s3", "2026-11-20T09:00:00+00:00")
    etag: str = client.get("/stories.xml").headers["etag"]
    con.execute("UPDATE stories SET text = 'later' WHERE id = 's3'")
    con.commit()
    r: Response = client.get("/stories.xml", headers={"if-none-match": etag})
    assert r.status_code == 200 and "later" in r.text


def test_browse_search_lists_matching_stories_above_the_posts(client: TestClient) -> None:
    page: str = client.get("/browse", params={"q": "galler"}).text
    assert "<h2>Stories</h2>" in page and "<h2>Posts</h2>" in page
    assert page.index("Gallery closed") < page.index("Gallery opening")  # newest first
    scoped: str = client.get("/browse", params={"q": "gallery", "user": "alice"}).text
    assert "Gallery opening" in scoped and "Gallery closed" not in scoped
    assert "<h2>Stories</h2>" not in client.get("/browse", params={"q": "nothing"}).text
    later: str = client.get(
        "/browse", params={"q": "gallery", "before": "2026-11-20T00:00:00+00:00", "before_id": "x"}
    ).text
    assert "<h2>Stories</h2>" not in later  # the first page only
