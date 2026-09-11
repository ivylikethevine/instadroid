import sqlite3
from datetime import UTC, datetime, timedelta

from tests.test_feed import make_app


def _add_runs(db, runs):
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


def test_health_ok_with_no_runs_yet(tmp_path, monkeypatch):
    client = make_app(tmp_path, monkeypatch)
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"ok": True, "posts": 2}


def test_health_ok_after_a_recent_successful_run(tmp_path, monkeypatch):
    client = make_app(tmp_path, monkeypatch)
    _add_runs(tmp_path / "posts.sqlite", [(1, None, None)])
    assert client.get("/health").status_code == 200


def test_health_503_when_no_run_has_finished_for_too_long(tmp_path, monkeypatch):
    monkeypatch.setenv("POLL_MAX_HOURS", "4.5")
    client = make_app(tmp_path, monkeypatch)
    _add_runs(tmp_path / "posts.sqlite", [(6, None, None)])  # past 4.5h + 30min
    r = client.get("/health")
    assert r.status_code == 503
    assert r.json()["ok"] is False and "no scrape run has finished" in r.json()["reason"]


def test_health_503_when_runs_keep_failing(tmp_path, monkeypatch):
    monkeypatch.setenv("POLL_MAX_HOURS", "4.5")
    client = make_app(tmp_path, monkeypatch)
    # Runs are still happening on schedule, but none has succeeded in 11h (> 2 * 4.5h + 1h).
    _add_runs(
        tmp_path / "posts.sqlite",
        [(11, None, None), (7, "RuntimeError('challenge')", None), (2, "RuntimeError('challenge')", None)],
    )
    r = client.get("/health")
    assert r.status_code == 503 and "no successful scrape run" in r.json()["reason"]


def test_health_ok_while_the_first_runs_are_still_failing_briefly(tmp_path, monkeypatch):
    client = make_app(tmp_path, monkeypatch)
    _add_runs(tmp_path / "posts.sqlite", [(1, "ConnectError('not online')", None)])
    assert client.get("/health").status_code == 200


def test_status_page_shows_a_warning_run(tmp_path, monkeypatch):
    client = make_app(tmp_path, monkeypatch)
    _add_runs(tmp_path / "posts.sqlite", [(0.1, None, "stopped early: 3 empty screens in a row")])
    body = client.get("/status").text
    assert "WARN" in body
    assert "stopped early: 3 empty screens in a row" in body


def test_status_page_shows_instagram_version_and_image(tmp_path, monkeypatch):
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


def test_status_page_shows_the_health_reason(tmp_path, monkeypatch):
    monkeypatch.setenv("POLL_MAX_HOURS", "4.5")
    client = make_app(tmp_path, monkeypatch)
    _add_runs(tmp_path / "posts.sqlite", [(6, None, None)])
    body = client.get("/status").text
    assert "OVERDUE" in body and "no scrape run has finished" in body
