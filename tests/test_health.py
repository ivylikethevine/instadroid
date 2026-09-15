import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.feedclient import make_app
from tests.support import json_body, json_object


def _add_runs(db: Path, runs: list[tuple[float, str | None, str | None]]) -> None:
    """runs: [(finished_hours_ago, error, warning)] — each run took 5 minutes."""
    con = sqlite3.connect(db)
    con.execute(
        """CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL, finished_at TEXT NOT NULL,
            new_posts INTEGER, error TEXT, android_release TEXT, android_sdk TEXT, device_product TEXT,
            warning TEXT
        )"""
    )
    now = datetime.now(UTC)
    for hours_ago, error, warning in sorted(runs, key=lambda r: -r[0]):  # oldest first, like real ids
        finished = now - timedelta(hours=hours_ago)
        con.execute(
            "INSERT INTO runs (started_at, finished_at, new_posts, error, warning) VALUES (?,?,?,?,?)",
            ((finished - timedelta(minutes=5)).isoformat(), finished.isoformat(), 0, error, warning),
        )
    con.commit()
    con.close()


def test_health_ok_with_no_runs_yet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = make_app(tmp_path, monkeypatch)
    r = client.get("/health")
    assert r.status_code == 200 and json_body(r) == {"ok": True, "posts": 2}


def test_health_ok_after_a_recent_successful_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = make_app(tmp_path, monkeypatch)
    _add_runs(tmp_path / "posts.sqlite", [(1, None, None)])
    assert client.get("/health").status_code == 200


def test_health_503_when_no_run_has_finished_for_too_long(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("POLL_MAX_HOURS", "4.5")
    client = make_app(tmp_path, monkeypatch)
    _add_runs(tmp_path / "posts.sqlite", [(6, None, None)])  # past 4.5h + 30min
    r = client.get("/health")
    assert r.status_code == 503
    body = json_object(r)
    reason = body["reason"]
    assert body["ok"] is False and isinstance(reason, str) and "no scrape run has finished" in reason


def test_health_503_when_runs_keep_failing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLL_MAX_HOURS", "4.5")
    client = make_app(tmp_path, monkeypatch)
    # Runs are still happening on schedule, but none has succeeded in 11h (> 2 * 4.5h + 1h).
    _add_runs(
        tmp_path / "posts.sqlite",
        [(11, None, None), (7, "RuntimeError('challenge')", None), (2, "RuntimeError('challenge')", None)],
    )
    r = client.get("/health")
    reason = json_object(r)["reason"]
    assert r.status_code == 503 and isinstance(reason, str) and "no successful scrape run" in reason


def test_health_ok_while_the_first_runs_are_still_failing_briefly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = make_app(tmp_path, monkeypatch)
    _add_runs(tmp_path / "posts.sqlite", [(1, "ConnectError('not online')", None)])
    assert client.get("/health").status_code == 200


def test_status_page_shows_a_warning_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = make_app(tmp_path, monkeypatch)
    _add_runs(tmp_path / "posts.sqlite", [(0.1, None, "stopped early: 3 empty screens in a row")])
    body = client.get("/status").text
    assert "WARN" in body
    assert "stopped early: 3 empty screens in a row" in body


def test_status_page_shows_instagram_version_and_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = make_app(tmp_path, monkeypatch)
    db = tmp_path / "posts.sqlite"
    _add_runs(db, [(0.1, None, None)])
    con = sqlite3.connect(db)
    con.execute("ALTER TABLE runs ADD COLUMN ig_version TEXT")
    con.execute("ALTER TABLE runs ADD COLUMN redroid_image TEXT")
    con.execute(
        "UPDATE runs SET android_release='13', ig_version='445.0.0.45.83',"
        " redroid_image='erstt/redroid:13.0.0_ndk_ChromeOS'"
    )
    con.commit()
    con.close()
    body = client.get("/status").text
    assert "Instagram 445.0.0.45.83" in body  # device line
    assert "<td>445.0.0.45.83</td>" in body  # per-run column
    assert "erstt/redroid:13.0.0_ndk_ChromeOS" in body
    assert "(profile" not in body  # a runs table from before selector_profile existed


def test_status_page_shows_the_selector_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = make_app(tmp_path, monkeypatch)
    db = tmp_path / "posts.sqlite"
    _add_runs(db, [(0.1, None, None)])
    con = sqlite3.connect(db)
    con.execute("ALTER TABLE runs ADD COLUMN ig_version TEXT")
    con.execute("ALTER TABLE runs ADD COLUMN selector_profile TEXT")
    con.execute("UPDATE runs SET android_release='13', ig_version='446.0.0.49.77', selector_profile='v445'")
    con.commit()
    con.close()
    assert "Instagram 446.0.0.49.77 (profile v445)" in client.get("/status").text


def test_status_page_shows_the_health_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLL_MAX_HOURS", "4.5")
    client = make_app(tmp_path, monkeypatch)
    _add_runs(tmp_path / "posts.sqlite", [(6, None, None)])
    body = client.get("/status").text
    assert "OVERDUE" in body and "no scrape run has finished" in body


def test_status_page_shows_peak_memory_and_oom_kills(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = make_app(tmp_path, monkeypatch)
    db = tmp_path / "posts.sqlite"
    _add_runs(db, [(0.2, None, None), (0.1, None, None)])
    con = sqlite3.connect(db)
    con.execute("ALTER TABLE runs ADD COLUMN mem_peak_mb INTEGER")
    con.execute("ALTER TABLE runs ADD COLUMN oom_kills INTEGER")
    con.execute("UPDATE runs SET mem_peak_mb=1843, oom_kills=0 WHERE id=1")
    con.execute("UPDATE runs SET mem_peak_mb=2012, oom_kills=7 WHERE id=2")
    con.commit()
    con.close()
    body = client.get("/status").text
    assert "<th>Peak mem</th>" in body
    assert "<td>1843 MiB</td>" in body
    assert "<td>2012 MiB, 7 OOM kill(s)</td>" in body
