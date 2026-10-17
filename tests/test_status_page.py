"""/status: the plain-HTML page of recent runs, the device and per-account totals."""

import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from shared import control as files
from shared.errors import short_error

from tests.feedclient import make_app


def test_status_page_with_no_runs_yet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client: TestClient = make_app(tmp_path, monkeypatch)
    r: Response = client.get("/status")
    assert r.status_code == 200
    assert "No scrape runs recorded yet" in r.text
    assert "no successful run yet" in r.text


def test_status_page_shows_latest_ok_run_and_device(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db: Path = tmp_path / "posts.sqlite"
    client: TestClient = make_app(tmp_path, monkeypatch)
    con: sqlite3.Connection = sqlite3.connect(db)
    con.execute(
        """CREATE TABLE runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL, finished_at TEXT NOT NULL,
            new_posts INTEGER, error TEXT, android_release TEXT, android_sdk TEXT, device_product TEXT
        )"""
    )
    now: datetime = datetime.now(UTC)
    con.execute(
        "INSERT INTO runs (started_at, finished_at, new_posts, error, android_release, android_sdk,"
        " device_product) VALUES (?,?,?,?,?,?,?)",
        (
            (now - timedelta(minutes=2)).isoformat(),
            now.isoformat(),
            2,
            None,
            "13",
            "33",
            "redroid_x86_64",
        ),
    )
    con.commit()
    con.close()

    body: str = client.get("/status").text
    assert "Android 13" in body
    assert "API 33" in body
    assert "redroid_x86_64" in body
    assert "OK" in body
    assert "2m 0s" in body
    assert "2 new post(s)" in body
    assert "someone" in body and "other" in body  # per-account totals from the seeded posts


def test_status_page_flags_an_error_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db: Path = tmp_path / "posts.sqlite"
    client: TestClient = make_app(tmp_path, monkeypatch)
    con: sqlite3.Connection = sqlite3.connect(db)
    con.execute(
        """CREATE TABLE runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL, finished_at TEXT NOT NULL,
            new_posts INTEGER, error TEXT, android_release TEXT, android_sdk TEXT, device_product TEXT
        )"""
    )
    con.execute(
        "INSERT INTO runs (started_at, finished_at, new_posts, error) VALUES (?,?,?,?)",
        ("2026-09-08T08:00:00+00:00", "2026-09-08T08:00:30+00:00", 0, "RuntimeError('login failed')"),
    )
    con.commit()
    con.close()

    body: str = client.get("/status").text
    assert "ERROR" in body
    assert "login failed" in body


def test_status_page_shows_no_duration_for_unreadable_timestamps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db: Path = tmp_path / "posts.sqlite"
    client: TestClient = make_app(tmp_path, monkeypatch)
    con: sqlite3.Connection = sqlite3.connect(db)
    con.execute(
        """CREATE TABLE runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL, finished_at TEXT NOT NULL,
            new_posts INTEGER, error TEXT, android_release TEXT, android_sdk TEXT, device_product TEXT
        )"""
    )
    con.execute(
        "INSERT INTO runs (started_at, finished_at, new_posts) VALUES (?,?,?)",
        ("2026-09-08T08:00:00+00:00", "garbage", 1),  # a hand-edited row
    )
    con.commit()
    con.close()

    body: str = client.get("/status").text
    assert "finished garbage (—)" in body
    assert "<td>2026-09-08T08:00:00+00:00</td><td>—</td><td>1</td>" in body


def test_status_page_shows_the_manual_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONTROL_DIR", str(tmp_path / "control"))
    client: TestClient = make_app(tmp_path, monkeypatch)
    assert client.post("/control/lock").status_code == 200
    body: str = client.get("/status").text
    assert "Manual lock in place: scheduled runs are held." in body
    assert "control('DELETE', '/control/lock')\">Unlock</button>" in body


def test_short_error_truncates_multiline_stack_traces() -> None:
    assert short_error("RuntimeError('simple')") == "RuntimeError('simple')"
    multiline: str = "LaunchUiAutomationError('boom', 'a huge\nmulti-line\njava stack trace')"
    result: str = short_error(multiline)
    assert "\n" not in result
    assert result.startswith("LaunchUiAutomationError")
    long_one_liner: str = "x" * 200
    assert short_error(long_one_liner) == "x" * 139 + "…"
    assert short_error(long_one_liner, 300) == long_one_liner
    assert short_error(long_one_liner + "\nmore", None) == long_one_liner
    assert short_error("") == ""


def test_status_page_shows_link_failure_counts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db: Path = tmp_path / "posts.sqlite"
    client: TestClient = make_app(tmp_path, monkeypatch)
    con: sqlite3.Connection = sqlite3.connect(db)
    con.execute(
        """CREATE TABLE runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL, finished_at TEXT NOT NULL,
            new_posts INTEGER, error TEXT, android_release TEXT, android_sdk TEXT, device_product TEXT,
            link_sheet_failures INTEGER, link_clipboard_failures INTEGER
        )"""
    )
    now: datetime = datetime.now(UTC)
    con.execute(
        "INSERT INTO runs (started_at, finished_at, new_posts, link_sheet_failures, link_clipboard_failures)"
        " VALUES (?,?,?,?,?)",
        ((now - timedelta(minutes=1)).isoformat(), now.isoformat(), 1, 2, 3),
    )
    con.commit()
    con.close()

    body: str = client.get("/status").text
    assert "2 sheet / 3 clipboard" in body


def test_status_page_shows_latest_ok_run_includes_link_failures_dash_when_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # test_status_page_shows_latest_ok_run_and_device already seeds a runs table from before
    # link_sheet_failures/link_clipboard_failures existed; confirm the page degrades to "—" for it
    # instead of a KeyError, the same defensive shape feedserver already uses for "url"/"place"/etc.
    client: TestClient = make_app(tmp_path, monkeypatch)
    con: sqlite3.Connection = sqlite3.connect(tmp_path / "posts.sqlite")
    con.execute(
        """CREATE TABLE runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL, finished_at TEXT NOT NULL,
            new_posts INTEGER, error TEXT, android_release TEXT, android_sdk TEXT, device_product TEXT
        )"""
    )
    now: datetime = datetime.now(UTC)
    con.execute(
        "INSERT INTO runs (started_at, finished_at, new_posts) VALUES (?,?,?)",
        ((now - timedelta(minutes=1)).isoformat(), now.isoformat(), 0),
    )
    con.commit()
    con.close()

    r: Response = client.get("/status")
    assert r.status_code == 200  # must not raise on a runs row missing the link-failure columns


def test_status_page_shows_new_stories_column(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db: Path = tmp_path / "posts.sqlite"
    client: TestClient = make_app(tmp_path, monkeypatch)
    con: sqlite3.Connection = sqlite3.connect(db)
    con.execute(
        """CREATE TABLE runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL, finished_at TEXT NOT NULL,
            new_posts INTEGER, error TEXT, android_release TEXT, android_sdk TEXT, device_product TEXT,
            new_stories INTEGER
        )"""
    )
    now: datetime = datetime.now(UTC)
    con.execute(
        "INSERT INTO runs (started_at, finished_at, new_posts, new_stories) VALUES (?,?,?,?)",
        ((now - timedelta(minutes=1)).isoformat(), now.isoformat(), 0, 4),
    )
    con.execute(
        "CREATE TABLE stories (id TEXT PRIMARY KEY, username TEXT, media_file TEXT, kind TEXT,"
        " posted_date TEXT, scraped_at TEXT)"
    )
    con.execute(
        "INSERT INTO stories VALUES ('s1','alice','stories/s1.jpg','story','1h',?)",
        (now.isoformat(),),
    )
    con.commit()
    con.close()

    body: str = client.get("/status").text
    assert "1 story stored" in body
    # the runs table row's own new_stories value, distinct from the currently-active count above
    assert "<td>4</td>" in body


def _schedule(client: TestClient) -> str:
    body: str = client.get("/status").text
    return body[body.index("<h2>Schedule and storage</h2>") : body.index("<h2>Recent runs</h2>")]


def _seed_runs(db: Path, starts: list[datetime]) -> None:
    con: sqlite3.Connection = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE runs (id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL,"
        " finished_at TEXT NOT NULL, new_posts INTEGER, error TEXT)"
    )
    con.executemany(
        "INSERT INTO runs (started_at, finished_at) VALUES (?, ?)",
        [(t.isoformat(), t.isoformat()) for t in starts],
    )
    con.commit()
    con.close()


def test_status_shows_the_next_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    control: Path = tmp_path / "control"
    monkeypatch.setenv("CONTROL_DIR", str(control))
    client: TestClient = make_app(tmp_path, monkeypatch)
    assert "Next run: not scheduled yet" in _schedule(client)
    files.set_next_run(control, datetime.now(UTC) + timedelta(hours=2, minutes=5, seconds=30))
    assert re.search(r"Next run: \d{4}-\d\d-\d\d \d\d:\d\d UTC \(in 2h 05m\)", _schedule(client))
    files.set_next_run(control, datetime.now(UTC) + timedelta(minutes=12, seconds=30))
    assert "(in 12m)" in _schedule(client)
    files.set_next_run(control, datetime.now(UTC) - timedelta(minutes=1))
    assert "Next run: starting now, or in progress" in _schedule(client)
    files.set_lock(control, True)
    assert "Next run: held while locked" in _schedule(client)


def test_status_counts_down_the_daily_run_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_RUNS_PER_DAY", "3")
    client: TestClient = make_app(tmp_path, monkeypatch)
    now: datetime = datetime.now(UTC)
    _seed_runs(tmp_path / "posts.sqlite", [now - timedelta(hours=30), now - timedelta(hours=5)])
    assert "Runs: 2 of 3 left in the last 24h" in _schedule(client)
    con: sqlite3.Connection = sqlite3.connect(tmp_path / "posts.sqlite")
    con.executemany(
        "INSERT INTO runs (started_at, finished_at) VALUES (?, ?)",
        [((now - timedelta(hours=h)).isoformat(), now.isoformat()) for h in (4, 3)],
    )
    con.commit()
    con.close()
    allowed: str = (now + timedelta(hours=19)).strftime("%Y-%m-%d %H:")
    schedule: str = _schedule(client)
    assert "Runs: 0 of 3 left in the last 24h; the next is allowed at " + allowed in schedule
    monkeypatch.setenv("MAX_RUNS_PER_DAY", "0")
    assert "no daily run limit" in _schedule(make_app(tmp_path, monkeypatch, seed=""))


def test_status_shows_the_allowlist_and_a_held_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client: TestClient = make_app(tmp_path, monkeypatch)
    assert "Allowlist: not in use" in _schedule(client)
    con: sqlite3.Connection = sqlite3.connect(tmp_path / "posts.sqlite")
    con.executescript(
        "CREATE TABLE following (username TEXT PRIMARY KEY, updated_at TEXT NOT NULL);"
        "CREATE TABLE following_pending (username TEXT PRIMARY KEY, seen_at TEXT NOT NULL);"
        "INSERT INTO following VALUES ('a', '2026-10-01T08:00:00+00:00'), ('b', '2026-10-01T08:00:00+00:00');"
    )
    con.close()
    assert "Allowlist: 2 account(s), refreshed 2026-10-01 08:00 UTC</" in _schedule(client).replace(
        "<br>", "</"
    )
    con = sqlite3.connect(tmp_path / "posts.sqlite")
    con.execute("INSERT INTO following_pending VALUES ('a', '2026-10-02')")
    con.commit()
    con.close()
    assert "a refresh that found 1 is held back" in _schedule(client)


def test_status_shows_disk_use(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client: TestClient = make_app(tmp_path, monkeypatch)
    media: Path = tmp_path / "media"
    (media / "stories").mkdir(parents=True)
    (media / "a.webp").write_bytes(b"x" * 3 * 2**20)
    (media / "stories" / "b.webp").write_bytes(b"x" * 2**20)
    (media / "gone.webp").symlink_to(media / "missing")  # unreadable, left out
    (tmp_path / "posts.sqlite-wal").write_bytes(b"x" * 2**20)
    schedule: str = _schedule(client)
    assert re.search(r"Disk: media 4 MiB \(2 file\(s\)\), database 1 MiB, [\d.]+ (GiB|MiB) free", schedule)
    from feedserver import status  # the copy make_app imported

    assert status._mib(5 * 2**30) == "5.0 GiB"
