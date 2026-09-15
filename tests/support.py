"""Typed reads of what the tests get back: JSON bodies and SQLite rows (json.loads and sqlite3 both hand
back Any)."""

import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from devtools.jsonvalues import JSON as Json
from devtools.jsonvalues import loads
from httpx2 import Response


def parse_json(text: str | bytes) -> Json:
    return loads(text)


def json_body(r: Response) -> Json:
    """A response's JSON body (what r.json() returns, but typed)."""
    return parse_json(r.content)


def json_object(r: Response) -> dict[str, Json]:
    """A response's JSON body, which must be an object."""
    body = json_body(r)
    assert isinstance(body, dict), body
    return body


def json_at(value: Json, *path: str) -> Json:
    """value[path[0]][path[1]]..., where every step must be an object."""
    for key in path:
        assert isinstance(value, dict), f"expected an object at {key!r}, got {value!r}"
        value = value[key]
    return value


# What sqlite3 hands back for a column (no converters are registered).
type SqlValue = str | int | float | bytes | None


def sql_value(value: object) -> SqlValue:
    assert value is None or isinstance(value, str | int | float | bytes), value
    return value


def sql_rows(rows: Iterable[Iterable[object]]) -> list[tuple[SqlValue, ...]]:
    """Every row of a cursor (plain tuples or sqlite3.Row), with each column typed as a sqlite value."""
    return [tuple(sql_value(v) for v in row) for row in rows]


def sql_dict(row: sqlite3.Row) -> dict[str, SqlValue]:
    """A sqlite3.Row as a column -> value dict."""
    return dict(zip(row.keys(), sql_rows([row])[0], strict=True))


def fetch_row(rows: Iterable[object]) -> sqlite3.Row:
    """The first row of a cursor whose connection uses sqlite3.Row, which must have one."""
    row = next(iter(rows), None)
    assert isinstance(row, sqlite3.Row), row
    return row


def sql_row(rows: Iterable[Iterable[object]]) -> tuple[SqlValue, ...]:
    """The first row of a cursor, which must have one."""
    found = sql_rows(rows)
    assert found, "the query returned no rows"
    return found[0]


def sql_column(rows: Iterable[Iterable[object]]) -> list[SqlValue]:
    """The first column of every row."""
    return [row[0] for row in sql_rows(rows)]


def insert_post(
    con: sqlite3.Connection, media_dir: Path, post_id: str, days_old: float, media_file: str | None = None
) -> None:
    """A photo post `days_old` days old (scraped and posted then), with its media file written when given."""
    ts = (datetime.now(UTC) - timedelta(days=days_old)).isoformat()
    if media_file:
        (media_dir / media_file).write_bytes(b"x")
    con.execute(
        "INSERT INTO posts (id, username, kind, posted_date, caption, media_file, scraped_at, posted_at)"
        " VALUES (?,'u','photo','x','cap',?,?,?)",
        (post_id, media_file, ts, ts),
    )
    con.commit()
