"""Typed reads of what the tests get back, JSON bodies and SQLite rows (json.loads and sqlite3 both hand
back Any; the row reads build on shared.sqlrows), database seeding, fake urlopen() and subprocess.run(),
running scraper.py as a script, and the root profile with its feed fixture, shared across test modules."""

import runpy
import sqlite3
import subprocess
import sys
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Self, Unpack

import igprofiles
import pytest
from devtools import ROOT
from httpx2 import Response
from igprofiles import BaseProfile
from instadroid import config, db
from shared import sqlrows
from shared.jsonvalues import JSON as Json
from shared.jsonvalues import loads
from shared.sqlrows import SqlValue

V424: BaseProfile = igprofiles.load("v424")
FEED_445: Path = igprofiles.fixture("v424", "feed_445.xml")


def json_body(r: Response) -> Json:
    """A response's JSON body (what r.json() returns, but typed)."""
    return loads(r.content)


def json_object(r: Response) -> dict[str, Json]:
    """A response's JSON body, which must be an object."""
    body: Json = json_body(r)
    assert isinstance(body, dict), body
    return body


def json_at(value: Json, *path: str) -> Json:
    """value[path[0]][path[1]]..., where every step must be an object."""
    key: str
    for key in path:
        assert isinstance(value, dict), f"expected an object at {key!r}, got {value!r}"
        value = value[key]
    return value


def fetch_row(cur: sqlite3.Cursor) -> sqlite3.Row:
    """The cursor's next row, which must exist."""
    row: sqlite3.Row | None = sqlrows.fetch_one(cur)
    assert row is not None, "the query returned no rows"
    return row


def row_dict(row: sqlite3.Row) -> dict[str, SqlValue]:
    """A row as {column: value}."""
    return dict(zip(row.keys(), sqlrows.values(row), strict=True))


def sql_column(cur: sqlite3.Cursor) -> list[SqlValue]:
    """The first column of every remaining row."""
    return [sqlrows.cell(row, 0) for row in sqlrows.fetch_all(cur)]


def record_run_ago(
    con: sqlite3.Connection,
    minutes: float,
    error: str | None = None,
    *,
    now: datetime | None = None,
    **stats: Unpack[db.RunMetrics],
) -> None:
    """A runs row that started and finished `minutes` before `now` (default: the current time), with no
    device snapshot and no new posts."""
    at: str = ((now or datetime.now(UTC)) - timedelta(minutes=minutes)).isoformat()
    db.record_run(con, at, at, 0, error, {}, **stats)


def record_drift_baseline(con: sqlite3.Connection, runs: int = 5) -> None:
    """`runs` successful runs, the baseline a selector-drift check compares a run with."""
    for _ in range(runs):
        record_run_ago(con, 0, cards_per_screen=4.0, share_captioned=0.8, share_complete=0.9)


def insert_post(
    con: sqlite3.Connection,
    post_id: str,
    *,
    username: str = "u",
    caption: str = "cap",
    days_old: float = 1,
    now: datetime | None = None,
    dated: bool = True,
    media_file: str | None = None,
    h: str | None = None,
    url: str | None = None,
    permalink_attempts: int = 0,
) -> None:
    """A photo post scraped and last updated `days_old` days before `now` (default: the current time), with
    hash `h` (default: `post_id`) and its media file written when given. `dated=False` leaves its posted date
    and time empty, as for a card that showed none; otherwise it was posted when it was scraped."""
    ts: str = ((now or datetime.now(UTC)) - timedelta(days=days_old)).isoformat()
    if media_file:
        (config.MEDIA_DIR / media_file).write_bytes(b"x")
    con.execute(
        "INSERT INTO posts (id, username, kind, posted_date, caption, media_file, scraped_at, hash, url,"
        " posted_at, updated_at, permalink_attempts) VALUES (?,?,'photo',?,?,?,?,?,?,?,?,?)",
        (
            post_id, username, "x" if dated else None, caption, media_file, ts, h or post_id, url,
            ts if dated else None, ts, permalink_attempts,
        ),
    )  # fmt: skip
    con.commit()


def insert_story(
    con: sqlite3.Connection,
    story_id: str,
    scraped_at: str,
    *,
    username: str = "u",
    media_file: str | None = None,
) -> None:
    """A story row; any media file is the caller's to write."""
    con.execute(
        "INSERT INTO stories (id, username, media_file, kind, posted_date, scraped_at)"
        " VALUES (?,?,?,'story','1h',?)",
        (story_id, username, media_file, scraped_at),
    )
    con.commit()


def run_scraper(monkeypatch: pytest.MonkeyPatch, *args: str) -> None:
    """Run `python scraper.py *args` in this process."""
    monkeypatch.setattr(sys, "argv", ["scraper.py", *args])
    runpy.run_path(str(ROOT / "app" / "scraper.py"), run_name="__main__")


def fake_run(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
    raises: Exception | None = None,
) -> list[list[str]]:
    """Patch subprocess.run to answer every command with this result, or raise `raises`. Returns the list
    the commands are recorded in."""
    commands: list[list[str]] = []

    def run(cmd: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(list(cmd))
        if raises is not None:
            raise raises
        return subprocess.CompletedProcess(list(cmd), returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(subprocess, "run", run)
    return commands


class UrlResponse:
    """What a monkeypatched urllib.request.urlopen returns: an empty response that is its own context
    manager (the part of common.UrlResponse the project uses)."""

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return b""


@dataclass
class UrlopenCalls:
    """What a fake urlopen was handed, in call order; a plain URL is recorded as its Request."""

    requests: list[urllib.request.Request] = field(default_factory=list[urllib.request.Request])
    timeouts: list[float | None] = field(default_factory=list[float | None])


def record_urlopen(
    monkeypatch: pytest.MonkeyPatch,
    fail: Exception | Callable[[urllib.request.Request], Exception | None] | None = None,
) -> UrlopenCalls:
    """Patch urllib.request.urlopen to record each call and answer with an empty response. `fail` is raised
    instead: an exception for every call, or a function giving a request's exception (None lets it through)."""
    calls: UrlopenCalls = UrlopenCalls()

    def urlopen(request: str | urllib.request.Request, timeout: float | None = None) -> UrlResponse:
        recorded: urllib.request.Request = (
            urllib.request.Request(request) if isinstance(request, str) else request
        )
        calls.requests.append(recorded)
        calls.timeouts.append(timeout)
        failure: Exception | None = fail if fail is None or isinstance(fail, Exception) else fail(recorded)
        if failure is not None:
            raise failure
        return UrlResponse()

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return calls
