"""`scraper.py export`: the archive's layout, reruns past retention, and staying inside its directory."""

import sqlite3
from pathlib import Path

import pytest
from instadroid import config, export, retention
from shared.jsonvalues import JSON, loads

from tests.support import run_scraper


def _post(
    con: sqlite3.Connection,
    post_id: str,
    username: str = "alice",
    caption: str | None = "some caption",
    media: str | None = None,
    day: int = 1,
    url: str | None = None,
) -> None:
    at: str = f"2026-09-{day:02d}T12:00:00+00:00"
    if media:
        path: Path = config.MEDIA_DIR / media
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"image " + media.encode())
    con.execute(
        "INSERT INTO posts (id, username, kind, posted_date, caption, media_file, scraped_at, posted_at, url, place)"
        " VALUES (?,?,'photo','2 days ago',?,?,?,?,?,'Anytown')",
        (post_id, username, caption, media, at, at, url),
    )
    con.commit()


def _read(path: Path) -> JSON:
    return loads(path.read_text(encoding="utf-8"))


def _tree(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}


def test_json_format_writes_each_post_and_its_media_and_no_pages(
    con: sqlite3.Connection, tmp_path: Path
) -> None:
    _post(con, "p1", media="p1.webp", url="https://www.instagram.com/p/p1/")
    (config.MEDIA_DIR / "slide.webp").write_bytes(b"slide")
    con.execute("INSERT INTO media (post_id, idx, file) VALUES ('p1', 1, 'slide.webp')")
    con.commit()
    out: Path = tmp_path / "archive"
    assert export.export_posts(con, str(out), html=False) == {"posts": 1, "accounts": 1}
    assert _tree(out) == {"data/alice/p1.json", "media/p1.webp", "media/slide.webp"}
    assert (out / "media" / "p1.webp").read_bytes() == b"image p1.webp"
    assert _read(out / "data" / "alice" / "p1.json") == {
        "id": "p1",
        "username": "alice",
        "kind": "photo",
        "posted_date": "2 days ago",
        "posted_at": "2026-09-01T12:00:00+00:00",
        "scraped_at": "2026-09-01T12:00:00+00:00",
        "caption": "some caption",
        "place": "Anytown",
        "url": "https://www.instagram.com/p/p1/",
        "media": ["p1.webp", "slide.webp"],
    }


def test_html_format_pages_each_account_newest_first(
    con: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(export, "PAGE_POSTS", 2)
    monkeypatch.setattr(export, "BATCH_POSTS", 2)
    day: int
    for day in range(1, 6):
        _post(con, f"p{day}", caption=f"caption{day}x", day=day)
    _post(con, "b1", username="bob", media="avatars/b1.webp", url="https://www.instagram.com/p/b1/")
    out: Path = tmp_path / "archive"
    assert export.export_posts(con, str(out)) == {"posts": 6, "accounts": 2}
    index: str = (out / "index.html").read_text(encoding="utf-8")
    assert '<a href="accounts/alice/index.html">alice</a></td><td>5</td><td>2026-09-05' in index
    first: str = (out / "accounts" / "alice" / "index.html").read_text(encoding="utf-8")
    second: str = (out / "accounts" / "alice" / "page-2.html").read_text(encoding="utf-8")
    third: str = (out / "accounts" / "alice" / "page-3.html").read_text(encoding="utf-8")
    assert first.index("caption5x") < first.index("caption4x") and "caption3x" not in first
    assert 'href="page-2.html">Older' in first and "Newer" not in first
    assert 'href="index.html">Newer' in second and 'href="page-3.html">Older' in second
    assert "caption1x" in third and "Older" not in third
    bob: str = (out / "accounts" / "bob" / "index.html").read_text(encoding="utf-8")
    assert '<img src="../../media/avatars/b1.webp"' in bob
    assert (out / "media" / "avatars" / "b1.webp").exists()
    assert 'href="https://www.instagram.com/p/b1/"' in bob
    assert "Posted 2 days ago · at Anytown · saved 2026-09-01" in bob


def test_a_rerun_keeps_posts_retention_has_since_pruned(
    con: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _post(con, "old", caption="old words", media="old.webp", day=1)
    _post(con, "new", caption="new words", day=2)
    out: Path = tmp_path / "archive"
    export.export_posts(con, str(out))
    monkeypatch.setattr(config, "RETAIN_DAYS", 1)
    monkeypatch.setattr(config, "MEDIA_MAX_MB", 0)
    retention.prune_old_posts(con)
    assert not (config.MEDIA_DIR / "old.webp").exists()
    _post(con, "newest", caption="newest words", day=3)
    assert export.export_posts(con, str(out))["posts"] == 1
    page: str = (out / "accounts" / "alice" / "index.html").read_text(encoding="utf-8")
    assert "old words" in page and "new words" in page and "newest words" in page
    assert (out / "media" / "old.webp").exists()


def test_a_rerun_updates_a_post_still_stored(con: sqlite3.Connection, tmp_path: Path) -> None:
    _post(con, "p1", caption="first", media="p1.webp")
    out: Path = tmp_path / "archive"
    export.export_posts(con, str(out))
    con.execute("UPDATE posts SET caption = 'second' WHERE id = 'p1'")
    export.export_posts(con, str(out))
    assert (out / "media" / "p1.webp").exists()
    assert "second" in (out / "accounts" / "alice" / "index.html").read_text(encoding="utf-8")
    assert list((out / "data").rglob("*.tmp")) == []


def test_a_missing_media_file_is_left_out_of_the_post(con: sqlite3.Connection, tmp_path: Path) -> None:
    _post(con, "p1", media="p1.webp")
    (config.MEDIA_DIR / "p1.webp").unlink()
    out: Path = tmp_path / "archive"
    export.export_posts(con, str(out), html=False)
    post: JSON = _read(out / "data" / "alice" / "p1.json")
    assert isinstance(post, dict) and post["media"] == []


@pytest.mark.parametrize(
    "name, segment",
    [
        ("..", "%2E."),
        (".", "%2E"),
        ("", "_"),
        ("../../escape", "%2E.%2F..%2Fescape"),
        ("/etc/passwd", "%2Fetc%2Fpasswd"),
        ("a\\b", "a%5Cb"),
        ("plain.name_1", "plain.name_1"),
    ],
)
def test_safe_name_is_one_harmless_segment(name: str, segment: str) -> None:
    assert export.safe_name(name) == segment


def test_stored_names_cannot_write_outside_the_directory(con: sqlite3.Connection, tmp_path: Path) -> None:
    secret: Path = tmp_path / "secret.txt"
    secret.write_text("private")
    _post(con, "../../../evil-id", username="../../../evil-user")
    _post(con, "p2", username="..")
    _post(con, "p3", username="")
    con.execute(
        "INSERT INTO posts (id, username, media_file, scraped_at) VALUES ('p4', 'm', '../secret.txt', '2026')"
    )
    con.execute(
        "INSERT INTO posts (id, username, media_file, scraped_at) VALUES ('p5', 'm', ?, '2026')",
        (str(secret),),
    )
    con.commit()
    out: Path = tmp_path / "deep" / "archive"
    before: set[str] = _tree(tmp_path) - _tree(out)
    export.export_posts(con, str(out))
    assert _tree(tmp_path) - {f"deep/archive/{p}" for p in _tree(out)} == before
    assert not list(out.rglob("secret.txt"))  # nor read from outside MEDIA_DIR
    post: JSON = _read(out / "data" / "m" / "p4.json")
    assert isinstance(post, dict) and post["media"] == []
    assert "%2E." in {p.name for p in (out / "data").iterdir()}


@pytest.mark.parametrize("linked", ["data", "media", "data/alice", "data/alice/p1.json", "accounts"])
def test_a_symlink_in_the_directory_cannot_lead_outside_it(
    con: sqlite3.Connection, tmp_path: Path, linked: str
) -> None:
    _post(con, "p1", media="p1.webp")
    elsewhere: Path = tmp_path / "elsewhere"
    elsewhere.mkdir()
    out: Path = tmp_path / "archive"
    link: Path = out / linked
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(elsewhere / "target" if linked.endswith(".json") else elsewhere)
    with pytest.raises(PermissionError, match="outside the export directory"):
        export.export_posts(con, str(out))
    assert list(elsewhere.rglob("*")) == []


def test_a_media_symlink_pointing_out_of_media_dir_is_not_copied(
    con: sqlite3.Connection, tmp_path: Path
) -> None:
    secret: Path = tmp_path / "secret.txt"
    secret.write_text("private")
    (config.MEDIA_DIR / "link.webp").symlink_to(secret)
    con.execute(
        "INSERT INTO posts (id, username, media_file, scraped_at) VALUES ('p', 'u', 'link.webp', '2026')"
    )
    con.commit()
    out: Path = tmp_path / "archive"
    export.export_posts(con, str(out))
    assert not (out / "media").exists()


def test_every_value_in_the_pages_is_escaped(con: sqlite3.Connection, tmp_path: Path) -> None:
    evil: str = '<script>alert("x")</script>'
    _post(con, "p1", username=evil, caption=evil, url="javascript:alert(1)")
    con.execute("UPDATE posts SET place = ?, posted_date = ? WHERE id = 'p1'", (evil, evil))
    con.commit()
    out: Path = tmp_path / "archive"
    export.export_posts(con, str(out))
    page: Path
    for page in out.rglob("*.html"):
        text: str = page.read_text(encoding="utf-8")
        assert "<script" not in text and "javascript:" not in text


def test_a_post_with_nothing_but_the_required_fields_renders(con: sqlite3.Connection, tmp_path: Path) -> None:
    con.execute(
        "INSERT INTO posts (id, username, scraped_at) VALUES ('bare', 'u', '2026-09-01T00:00:00+00:00')"
    )
    con.commit()
    out: Path = tmp_path / "archive"
    export.export_posts(con, str(out))
    assert "saved 2026-09-01" in (out / "accounts" / "u" / "index.html").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "content",
    ["not json", "[1, 2]", '{"id": "x"}', '{"id": 1, "username": "u", "scraped_at": "2026"}'],
)
def test_a_bad_post_file_is_skipped_not_fatal(con: sqlite3.Connection, tmp_path: Path, content: str) -> None:
    _post(con, "p1", caption="good words")
    out: Path = tmp_path / "archive"
    (out / "data" / "alice").mkdir(parents=True)
    (out / "data" / "alice" / "bad.json").write_text(content)
    (out / "data" / "empty").mkdir()
    (out / "data" / "only-bad").mkdir()
    (out / "data" / "only-bad" / "bad.json").write_text(content)
    (out / "data" / "stray.txt").write_text("x")
    export.export_posts(con, str(out))
    assert "good words" in (out / "accounts" / "alice" / "index.html").read_text(encoding="utf-8")
    assert not (out / "accounts" / "only-bad").exists()
    assert "only-bad" not in (out / "index.html").read_text(encoding="utf-8")


def test_a_post_file_with_a_non_list_of_media_reads_as_none(tmp_path: Path) -> None:
    path: Path = tmp_path / "p.json"
    path.write_text('{"id": "x", "username": "u", "scraped_at": "2026", "media": "a.webp"}')
    post: export.ExportedPost | None = export._read_post(path)
    assert post is not None and post["media"] == []
    path.write_text('{"id": "x", "username": "u", "scraped_at": "2026", "media": ["a.webp", 1]}')
    post = export._read_post(path)
    assert post is not None and post["media"] == ["a.webp"]


def test_the_subcommand_exports_in_either_format(
    con: sqlite3.Connection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _post(con, "p1")
    html: Path = tmp_path / "html"
    run_scraper(monkeypatch, "export", str(html))
    assert f"exported 1 post(s) of 1 account(s) to {html}" in capsys.readouterr().out
    assert (html / "index.html").exists()
    run_scraper(monkeypatch, "export", str(html), "--format", "html")
    only_json: Path = tmp_path / "json"
    run_scraper(monkeypatch, "export", str(only_json), "--format", "json")
    assert (only_json / "data" / "alice" / "p1.json").exists() and not (only_json / "index.html").exists()


@pytest.mark.parametrize(
    "args",
    [[], ["--format", "json"], ["dir", "--format"], ["dir", "--format", "xml"], ["dir", "extra"]],
)
def test_the_subcommand_checks_its_arguments(
    con: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    args: list[str],
) -> None:
    with pytest.raises(SystemExit):
        run_scraper(monkeypatch, "export", *args)
    assert "usage: scraper.py export" in capsys.readouterr().out


def test_the_subcommand_reports_a_directory_it_cannot_use(
    con: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    taken: Path = tmp_path / "file"
    taken.write_text("x")
    with pytest.raises(SystemExit, match="export failed"):
        run_scraper(monkeypatch, "export", str(taken))
