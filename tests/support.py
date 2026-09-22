"""Typed reads of what the tests get back, JSON bodies and SQLite rows (json.loads and sqlite3 both hand
back Any; the row reads build on shared.sqlrows), database seeding, and a fake urlopen() response, shared
across test modules."""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Self, Unpack

from devtools.jsonvalues import JSON as Json
from devtools.jsonvalues import loads
from httpx2 import Response
from instadroid import db
from shared import sqlrows
from shared.sqlrows import SqlValue


def parse_json(text: str | bytes) -> Json:
    return loads(text)


def json_body(r: Response) -> Json:
    """A response's JSON body (what r.json() returns, but typed)."""
    return parse_json(r.content)


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


def insert_post(
    con: sqlite3.Connection, media_dir: Path, post_id: str, days_old: float, media_file: str | None = None
) -> None:
    """A photo post `days_old` days old (scraped and posted then), with its media file written when given."""
    ts: str = (datetime.now(UTC) - timedelta(days=days_old)).isoformat()
    if media_file:
        (media_dir / media_file).write_bytes(b"x")
    con.execute(
        "INSERT INTO posts (id, username, kind, posted_date, caption, media_file, scraped_at, posted_at)"
        " VALUES (?,'u','photo','x','cap',?,?,?)",
        (post_id, media_file, ts, ts),
    )
    con.commit()


class UrlResponse:
    """What a monkeypatched urllib.request.urlopen returns: an empty response that is its own context
    manager (the part of common.UrlResponse the project uses)."""

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return b""
