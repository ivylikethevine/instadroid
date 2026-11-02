"""/status: the plain-HTML page of recent runs, the device and per-account totals."""

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from shared import control as files
from shared.errors import short_error

from tests.feedclient import DEVICE_COLUMNS, add_runs, feed_sql, make_app


def test_status_page_with_no_runs_yet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client: TestClient = make_app(tmp_path, monkeypatch)
    r: Response = client.get("/status")
    assert r.status_code == 200
    assert "No scrape runs recorded yet" in r.text
    assert "no successful run yet" in r.text


def test_status_page_shows_latest_ok_run_and_device(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client: TestClient = make_app(tmp_path, monkeypatch)
    now: datetime = datetime.now(UTC)
    add_runs(
        tmp_path,
        [
            {
                "started_at": (now - timedelta(minutes=2)).isoformat(),
                "finished_at": now.isoformat(),
                "new_posts": 2,
                "error": None,
                "android_release": "13",
                "android_sdk": "33",
                "device_product": "redroid_x86_64",
            }
        ],
    )

    body: str = client.get("/status").text
    assert "Android 13" in body
    assert "API 33" in body
    assert "redroid_x86_64" in body
    assert "OK" in body
    assert "2m 0s" in body
    assert "2 new post(s)" in body
    assert "someone" in body and "other" in body  # per-account totals from the seeded posts


def test_status_page_flags_an_error_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client: TestClient = make_app(tmp_path, monkeypatch)
    add_runs(
        tmp_path,
        [
            {
                "started_at": "2026-09-08T08:00:00+00:00",
                "finished_at": "2026-09-08T08:00:30+00:00",
                "new_posts": 0,
                "error": "RuntimeError('login failed')",
            }
        ],
    )

    body: str = client.get("/status").text
    assert "ERROR" in body
    assert "login failed" in body


def test_status_page_shows_no_duration_for_unreadable_timestamps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client: TestClient = make_app(tmp_path, monkeypatch)
    # A hand-edited row.
    add_runs(
        tmp_path, [{"started_at": "2026-09-08T08:00:00+00:00", "finished_at": "garbage", "new_posts": 1}]
    )

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
    client: TestClient = make_app(tmp_path, monkeypatch)
    now: datetime = datetime.now(UTC)
    add_runs(
        tmp_path,
        [
            {
                "started_at": (now - timedelta(minutes=1)).isoformat(),
                "finished_at": now.isoformat(),
                "new_posts": 1,
                "link_sheet_failures": 2,
                "link_clipboard_failures": 3,
            }
        ],
        (*DEVICE_COLUMNS, "link_sheet_failures INTEGER", "link_clipboard_failures INTEGER"),
    )

    body: str = client.get("/status").text
    assert "2 sheet / 3 clipboard" in body


def test_status_page_shows_new_stories_column(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client: TestClient = make_app(tmp_path, monkeypatch)
    now: datetime = datetime.now(UTC)
    add_runs(
        tmp_path,
        [
            {
                "started_at": (now - timedelta(minutes=1)).isoformat(),
                "finished_at": now.isoformat(),
                "new_posts": 0,
                "new_stories": 4,
            }
        ],
        (*DEVICE_COLUMNS, "new_stories INTEGER"),
    )
    feed_sql(
        tmp_path,
        "CREATE TABLE stories (id TEXT PRIMARY KEY, username TEXT, media_file TEXT, kind TEXT,"
        " posted_date TEXT, scraped_at TEXT)",
        ("INSERT INTO stories VALUES ('s1','alice','stories/s1.jpg','story','1h',?)", (now.isoformat(),)),
    )

    body: str = client.get("/status").text
    assert "1 story stored" in body
    # the runs table row's own new_stories value, distinct from the currently-active count above
    assert "<td>4</td>" in body


def _schedule(client: TestClient) -> str:
    body: str = client.get("/status").text
    return body[body.index("<h2>Schedule and storage</h2>") : body.index("<h2>Recent runs</h2>")]


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
    earlier: list[datetime] = [now - timedelta(hours=30), now - timedelta(hours=5)]
    add_runs(tmp_path, [{"started_at": t.isoformat(), "finished_at": t.isoformat()} for t in earlier], ())
    assert "Runs: 2 of 3 left in the last 24h" in _schedule(client)
    add_runs(
        tmp_path,
        [
            {"started_at": (now - timedelta(hours=h)).isoformat(), "finished_at": now.isoformat()}
            for h in (4, 3)
        ],
        (),
    )
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
    feed_sql(
        tmp_path,
        "CREATE TABLE following (username TEXT PRIMARY KEY, updated_at TEXT NOT NULL)",
        "CREATE TABLE following_pending (username TEXT PRIMARY KEY, seen_at TEXT NOT NULL)",
        "INSERT INTO following VALUES ('a', '2026-10-01T08:00:00+00:00'), ('b', '2026-10-01T08:00:00+00:00')",
    )
    assert "Allowlist: 2 account(s), refreshed 2026-10-01 08:00 UTC</" in _schedule(client).replace(
        "<br>", "</"
    )
    feed_sql(tmp_path, "INSERT INTO following_pending VALUES ('a', '2026-10-02')")
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
