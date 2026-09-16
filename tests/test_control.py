"""Manual control of the poll loop (instadroid/control.py) and its feed-server endpoints."""

import os
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NoReturn

import pytest
from fastapi.testclient import TestClient
from instadroid import config, control, device, scrape

from tests.feedclient import make_app
from tests.support import json_body, json_object, record_run_ago


@pytest.fixture
def con(con: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    monkeypatch.setattr(config, "CONTROL_DIR", tmp_path)
    monkeypatch.setattr(config, "RUN_NOW_MIN_MINUTES", 30)
    monkeypatch.setattr(config, "CONTROL_POLL_SECONDS", 30)
    return con


def test_lock_and_its_expiry(
    con: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "LOCK_MAX_HOURS", 6)
    assert not control.locked()
    control.set_lock(True)
    assert control.locked() and (tmp_path / "manual.lock").exists()
    old = time.time() - 7 * 3600
    os.utime(tmp_path / "manual.lock", (old, old))
    assert not control.locked()  # forgotten: ignored
    monkeypatch.setattr(config, "LOCK_MAX_HOURS", 0)
    assert control.locked()  # 0 = a lock never expires
    control.set_lock(False)
    assert not control.locked()


def test_a_hold_never_expires_and_unlock_clears_it(
    con: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "LOCK_MAX_HOURS", 6)
    assert control.hold_reason() is None
    control.set_hold("RuntimeError(\"Instagram wants a human: 'Confirm it's you' screen\")")
    reason = control.hold_reason()
    assert control.locked() and reason is not None and "wants a human" in reason
    old = time.time() - 7 * 24 * 3600
    os.utime(tmp_path / "needs-human.hold", (old, old))
    assert control.locked()  # a week old and still holding: no timer resumes it
    control.set_lock(False)  # unlock is the one way out
    assert not control.locked() and control.hold_reason() is None


def test_a_needs_human_error_holds_all_later_runs(
    con: sqlite3.Connection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def challenge() -> NoReturn:
        raise RuntimeError("Instagram wants a human: 'Confirm it's you' screen; see /debug")

    monkeypatch.setattr(device, "connect_device", challenge)
    stats, exc = scrape.run_recorded(con)
    assert stats is None and isinstance(exc, RuntimeError)
    assert control.locked() and (tmp_path / "needs-human.hold").read_text().startswith("RuntimeError(")
    assert "holding all runs until `scraper.py unlock`" in capsys.readouterr().out
    # A device failure, by contrast, is retried and never holds.
    control.set_lock(False)

    def offline() -> NoReturn:
        raise device.DeviceNotReady("could not bring com.instagram.android to the foreground")

    monkeypatch.setattr(device, "connect_device", offline)
    scrape.run_recorded(con)
    assert not control.locked()


def test_the_poll_loop_holds_while_held(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    control.set_hold("RuntimeError('still on login screen after submit (wrong password?)')")
    held: list[float] = []

    def sleep(seconds: float) -> None:
        held.append(seconds)
        if len(held) == 2:
            control.set_lock(False)

    monkeypatch.setattr(time, "sleep", sleep)

    class StopLoop(Exception):
        pass

    def run_recorded(c: sqlite3.Connection) -> NoReturn:
        raise StopLoop

    def startup_wait_seconds(c: sqlite3.Connection) -> float:
        return 0

    monkeypatch.setattr(scrape, "_startup_wait_seconds", startup_wait_seconds)
    monkeypatch.setattr(scrape, "run_recorded", run_recorded)
    with pytest.raises(StopLoop):
        scrape.main()
    assert held == [30, 30]
    out = capsys.readouterr().out
    assert "Instagram needs a person (RuntimeError('still on login screen" in out and "resuming" in out


def test_scrape_now_waits_for_the_rate_limit(con: sqlite3.Connection, tmp_path: Path) -> None:
    control.request_run_now()
    record_run_ago(con, 45)
    record_run_ago(con, 10)
    assert not control.take_run_now(con) and (tmp_path / "scrape-now").exists()  # too soon: kept
    con.execute("DELETE FROM runs")
    record_run_ago(con, 45)
    assert control.take_run_now(con) and not (tmp_path / "scrape-now").exists()


def test_wait_sleeps_in_steps_and_ends_early_for_scrape_now(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 2:
            control.request_run_now()

    monkeypatch.setattr(time, "sleep", sleep)
    control.wait(con, 100)
    assert sleeps == [30, 30]  # the third check found the request (no runs yet, so it's due)
    sleeps.clear()
    monkeypatch.setattr(time, "sleep", sleeps.append)
    control.wait(con, 75)
    assert sleeps == [30, 30, 15]


def test_the_poll_loop_holds_while_locked(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    control.set_lock(True)
    held: list[float] = []

    def sleep(seconds: float) -> None:
        held.append(seconds)
        if len(held) == 3:
            control.set_lock(False)

    monkeypatch.setattr(time, "sleep", sleep)
    started: list[int] = []

    class StopLoop(Exception):
        pass

    def run_recorded(c: sqlite3.Connection) -> NoReturn:
        started.append(1)
        raise StopLoop

    def startup_wait_seconds(c: sqlite3.Connection) -> float:
        return 0

    monkeypatch.setattr(scrape, "_startup_wait_seconds", startup_wait_seconds)
    monkeypatch.setattr(scrape, "run_recorded", run_recorded)
    with pytest.raises(StopLoop):
        scrape.main()
    assert held == [30, 30, 30] and started == [1]  # held three polls, then ran
    out = capsys.readouterr().out
    assert "holding scheduled runs" in out and "lock removed; resuming" in out


def _control_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("CONTROL_DIR", str(tmp_path / "control"))
    monkeypatch.setenv("RUN_NOW_MIN_MINUTES", "30")
    return make_app(tmp_path, monkeypatch)


def test_control_endpoints(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _control_app(tmp_path, monkeypatch)
    control_dir = tmp_path / "control"
    assert json_body(client.post("/control/lock")) == {"locked": True, "scrape_now": False, "hold": None}
    assert (control_dir / "manual.lock").exists()
    assert client.post("/control/scrape-now").status_code == 409  # locked
    assert json_body(client.delete("/control/lock")) == {"locked": False, "scrape_now": False, "hold": None}
    assert client.post("/control/scrape-now").status_code == 202  # no runs recorded yet
    assert (control_dir / "scrape-now").exists()
    assert json_body(client.get("/control")) == {"locked": False, "scrape_now": True, "hold": None}
    assert "scrape-now request is waiting" in client.get("/status").text
    (control_dir / "scrape-now").unlink()
    # A hold the scraper raised: reported with its reason, refuses scrape-now, and unlock clears it.
    (control_dir / "needs-human.hold").write_text("RuntimeError('Instagram wants a human')\n")
    assert json_body(client.get("/control")) == {
        "locked": True,
        "scrape_now": False,
        "hold": "RuntimeError('Instagram wants a human')",
    }
    assert client.post("/control/scrape-now").status_code == 409
    assert "Held until unlocked, Instagram needs a person" in client.get("/status").text
    assert json_body(client.delete("/control/lock")) == {"locked": False, "scrape_now": False, "hold": None}
    assert not (control_dir / "needs-human.hold").exists()

    con = sqlite3.connect(tmp_path / "posts.sqlite")
    con.execute(
        "CREATE TABLE runs (id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT, new_posts INTEGER, error TEXT)"
    )
    recent = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    con.execute("INSERT INTO runs (started_at, finished_at, new_posts) VALUES (?, ?, 0)", (recent, recent))
    con.commit()
    con.close()
    r = client.post("/control/scrape-now")
    detail = json_object(r)["detail"]
    assert r.status_code == 429 and isinstance(detail, str) and "25" in detail  # 30 - 5 minutes to go


def test_control_endpoints_need_the_feed_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEED_TOKEN", "t0ken")
    client = _control_app(tmp_path, monkeypatch)
    assert client.post("/control/lock").status_code == 401
    assert client.post("/control/lock", headers={"Authorization": "Bearer t0ken"}).status_code == 200


def test_a_cross_site_browser_request_cannot_change_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _control_app(tmp_path, monkeypatch)
    assert client.post("/control/lock", headers={"Origin": "https://evil.example"}).status_code == 403
    assert not (tmp_path / "control" / "manual.lock").exists()
    assert (
        client.post("/control/lock", headers={"Origin": "http://feed.test"}).status_code == 200
    )  # PUBLIC_URL
    assert (
        client.get("/control", headers={"Origin": "https://evil.example"}).status_code == 200
    )  # reads are fine
