"""Failure alerts (instadroid/alerts.py): conditions, raise/resolve bookkeeping, delivery, and how the
feed server shows open alerts."""

import sqlite3
import urllib.request
from collections.abc import Buffer, Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from instadroid import alerts, config
from shared.sqlrows import SqlValue

from tests.feedclient import make_app
from tests.support import UrlResponse, record_run_ago, sql_column

if TYPE_CHECKING:
    from _typeshed import SupportsRead

NOW: datetime = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
CHALLENGE: str = "RuntimeError(\"Instagram wants a human: 'Confirm it's you' screen; see /debug\")"


@pytest.fixture
def con(con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    monkeypatch.setattr(config, "ALERT_FAILED_RUNS", 3)
    monkeypatch.setattr(config, "ALERT_NO_POSTS_HOURS", 0)
    return con


def kinds(con: sqlite3.Connection) -> list[SqlValue]:
    return sql_column(con.execute("SELECT kind FROM alerts"))


def _run(con: sqlite3.Connection, hours_ago: float, error: str | None = None) -> None:
    record_run_ago(con, hours_ago * 60, error, now=NOW)


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[urllib.request.Request]:
    requests: list[urllib.request.Request] = []

    def urlopen(request: urllib.request.Request, timeout: float) -> UrlResponse:
        requests.append(request)
        return UrlResponse()

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
    hours: int
    for hours in (3, 2, 1):
        _run(con, hours, "DeviceNotReady('adb offline')")
    found: dict[str, str] = alerts.conditions(con, NOW)
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
    data: Buffer | SupportsRead[bytes] | Iterable[bytes] | None = sent[0].data
    assert isinstance(data, bytes) and b"Confirm it's you" in data
    assert kinds(con) == ["login"]

    _run(con, 1)
    alerts.update(con, NOW)
    assert sent[-1].get_header("Title") == "instadroid: resolved: Instagram wants a human"
    assert not con.execute("SELECT kind FROM alerts").fetchall()


def test_a_failed_delivery_is_reported_without_the_token(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(config, "ALERT_URL", "https://user:pw@ntfy.example/topic?auth=secret")

    def urlopen(request: urllib.request.Request, timeout: float) -> None:
        raise OSError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    _run(con, 1, CHALLENGE)
    errors: list[str] = alerts.update(con, NOW)
    assert errors and "https://ntfy.example/topic" in errors[0]
    output: str = capsys.readouterr().out + errors[0]
    assert "secret" not in output and "pw" not in output
    assert kinds(con) == ["login"]  # still recorded


def test_open_alerts_lead_the_feed_and_show_on_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client: TestClient = make_app(tmp_path, monkeypatch)
    before: str = client.get("/instagram.xml").headers["etag"]
    con: sqlite3.Connection = sqlite3.connect(tmp_path / "posts.sqlite")
    con.execute("CREATE TABLE alerts (kind TEXT PRIMARY KEY, message TEXT, raised_at TEXT)")
    con.execute(
        "INSERT INTO alerts VALUES ('login', 'finish it in scrcpy: <challenge>', '2026-09-14T12:00:00+00:00')"
    )
    con.commit()
    con.close()
    r: Response = client.get("/instagram.xml")
    assert r.headers["etag"] != before
    body: str = r.text
    assert body.index("instadroid needs attention") < body.index("someone:")  # first entry
    assert "<id>http://feed.test/alert/login/2026-09-14T12:00:00+00:00</id>" in body
    assert "&lt;challenge&gt;" in body
    assert "instadroid needs attention" not in client.get("/instagram.xml", params={"user": "someone"}).text
    assert (
        "Alert since 2026-09-14T12:00: finish it in scrcpy: &lt;challenge&gt;" in client.get("/status").text
    )


def test_an_open_alert_whose_message_changes_is_updated_quietly(
    con: sqlite3.Connection, sent: list[urllib.request.Request]
) -> None:
    _run(con, 2, CHALLENGE)
    alerts.update(con, NOW)
    _run(con, 1, "RuntimeError(\"Instagram wants a human: 'Enter the code' screen\")")  # a later challenge
    assert alerts.update(con, NOW) == []
    assert len(sent) == 1  # the same alert is still open: no second notification
    messages: list[SqlValue] = sql_column(con.execute("SELECT message FROM alerts"))
    assert len(messages) == 1 and isinstance(messages[0], str) and "Enter the code" in messages[0]
