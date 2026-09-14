"""Failure alerts (instadroid/alerts.py): conditions, raise/resolve bookkeeping, delivery, and how the
feed server shows open alerts."""

import sqlite3
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from instadroid import alerts, config, db

from tests.test_feed import make_app

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
CHALLENGE = "RuntimeError(\"Instagram wants a human: 'Confirm it's you' screen; see /debug\")"


@pytest.fixture
def con(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "posts.sqlite"))
    monkeypatch.setattr(config, "ALERT_FAILED_RUNS", 3)
    monkeypatch.setattr(config, "ALERT_NO_POSTS_HOURS", 0)
    return db.db_init()


def _run(con: sqlite3.Connection, hours_ago: float, error: str | None = None) -> None:
    started = (NOW - timedelta(hours=hours_ago)).isoformat()
    db.record_run(con, started, started, 0, error, {})


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[urllib.request.Request]:
    requests: list[urllib.request.Request] = []

    class Response:
        def __enter__(self) -> Response:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def read(self) -> bytes:
            return b""

    def urlopen(request: urllib.request.Request, timeout: float) -> Response:
        requests.append(request)
        return Response()

    monkeypatch.setattr(config, "ALERT_URL", "https://ntfy.example/instadroid-topic?auth=secret")
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return requests


def test_no_conditions_on_a_healthy_history(con: sqlite3.Connection) -> None:
    _run(con, 3)
    _run(con, 1, "DeviceNotReady('adb offline')")  # one failure isn't a pattern
    assert alerts.conditions(con, NOW) == {}


def test_a_login_challenge_is_an_alert_straight_away(con: sqlite3.Connection) -> None:
    _run(con, 1, CHALLENGE)
    assert set(alerts.conditions(con, NOW)) == {alerts.LOGIN}


def test_consecutive_failures_are_an_alert(con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    for hours in (3, 2, 1):
        _run(con, hours, "DeviceNotReady('adb offline')")
    found = alerts.conditions(con, NOW)
    assert set(found) == {alerts.FAILING} and "the last 3 runs failed" in found[alerts.FAILING]
    monkeypatch.setattr(config, "ALERT_FAILED_RUNS", 0)
    assert alerts.conditions(con, NOW) == {}


def test_no_new_posts_waits_for_a_full_window(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "ALERT_NO_POSTS_HOURS", 48)
    _run(con, 10)
    assert alerts.conditions(con, NOW) == {}  # the scraper hasn't run for 48h yet
    _run(con, 60)
    assert set(alerts.conditions(con, NOW)) == {alerts.NO_POSTS}
    con.execute(
        "INSERT INTO posts (id, username, scraped_at) VALUES ('p', 'u', ?)",
        ((NOW - timedelta(hours=5)).isoformat(),),
    )
    assert alerts.conditions(con, NOW) == {}


def test_an_alert_is_announced_once_and_again_when_resolved(
    con: sqlite3.Connection, sent: list[urllib.request.Request]
) -> None:
    _run(con, 2, CHALLENGE)
    assert alerts.update(con, NOW) == []
    assert alerts.update(con, NOW) == []  # still open: no second notification
    assert [r.get_header("Title") for r in sent] == ["instadroid: Instagram wants a human"]
    assert sent[0].get_header("Priority") == "high" and sent[0].get_method() == "POST"
    assert b"Confirm it's you" in (sent[0].data or b"")
    assert [r[0] for r in con.execute("SELECT kind FROM alerts")] == ["login"]

    _run(con, 1)
    alerts.update(con, NOW)
    assert sent[-1].get_header("Title") == "instadroid: resolved: Instagram wants a human"
    assert not con.execute("SELECT kind FROM alerts").fetchall()


def test_a_failed_delivery_is_reported_without_the_token(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(config, "ALERT_URL", "https://user:pw@ntfy.example/topic?auth=secret")

    def urlopen(request: Any, timeout: float) -> None:
        raise OSError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    _run(con, 1, CHALLENGE)
    errors = alerts.update(con, NOW)
    assert errors and "https://ntfy.example/topic" in errors[0]
    output = capsys.readouterr().out + errors[0]
    assert "secret" not in output and "pw" not in output
    assert [r[0] for r in con.execute("SELECT kind FROM alerts")] == ["login"]  # still recorded


def test_open_alerts_lead_the_feed_and_show_on_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = make_app(tmp_path, monkeypatch)
    before = client.get("/instagram.xml").headers["etag"]
    con = sqlite3.connect(tmp_path / "posts.sqlite")
    con.execute("CREATE TABLE alerts (kind TEXT PRIMARY KEY, message TEXT, raised_at TEXT)")
    con.execute(
        "INSERT INTO alerts VALUES ('login', 'finish it in scrcpy: <challenge>', '2026-09-14T12:00:00+00:00')"
    )
    con.commit()
    con.close()
    r = client.get("/instagram.xml")
    assert r.headers["etag"] != before
    body = r.text
    assert body.index("instadroid needs attention") < body.index("someone:")  # first entry
    assert "<id>http://feed.test/alert/login/2026-09-14T12:00:00+00:00</id>" in body
    assert "&lt;challenge&gt;" in body
    assert "instadroid needs attention" not in client.get("/instagram.xml", params={"user": "someone"}).text
    assert (
        "Alert since 2026-09-14T12:00: finish it in scrcpy: &lt;challenge&gt;" in client.get("/status").text
    )
