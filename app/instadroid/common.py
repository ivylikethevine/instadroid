"""Small pure helpers shared by every module: logging, timestamps, bounds, hashing, filenames."""

import hashlib
import re
import sqlite3
import urllib.request
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Protocol, Self
from urllib.parse import urlsplit

type SqlValue = str | int | float | bytes | None  # what SQLite hands back for a column


def log(*a: object) -> None:
    print(datetime.now().strftime("%H:%M:%S"), *a, flush=True)


def parse_iso(value: SqlValue) -> datetime | None:
    """A stored ISO timestamp as an aware datetime (naive = UTC), or None if missing/malformed.
    `value` is whatever a sqlite column happened to hold, not necessarily text; anything else counts
    as malformed."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def older_than(value: SqlValue, days: float) -> bool:
    """True if the stored timestamp is missing, malformed, or more than `days` old."""
    parsed = parse_iso(value)
    return parsed is None or datetime.now(UTC) - parsed > timedelta(days=days)


_BOUNDS = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


def parse_bounds(bounds: str | None) -> tuple[int, int, int, int] | None:
    """(x1, y1, x2, y2) from a uiautomator bounds string "[x1,y1][x2,y2]", or None."""
    m = _BOUNDS.match(bounds or "")
    if not m:
        return None
    x1, y1, x2, y2 = map(int, m.groups())
    return x1, y1, x2, y2


def bounds_center(bounds: str | None) -> tuple[int, int] | None:
    if not (b := parse_bounds(bounds)):
        return None
    x1, y1, x2, y2 = b
    return (x1 + x2) // 2, (y1 + y2) // 2


def bounds_bottom_right(bounds: str | None, inset: int = 10) -> tuple[int, int] | None:
    """Point near a node's bottom-right corner: where a truncated, left-aligned caption's
    trailing "... more" span sits, on its last (and typically fullest) line."""
    if not (b := parse_bounds(bounds)):
        return None
    x1, y1, x2, y2 = b
    return max(x1, x2 - inset), max(y1, y2 - inset)


def digest(data: str | bytes) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()[:16]


_SAFE_USERNAME = re.compile(r"^[A-Za-z0-9._]+$")


def safe_filename(username: str) -> str | None:
    """Reject anything that isn't a plausible Instagram handle before it's used as a filename —
    username is parsed from screen content, not trusted input."""
    if not username or username in (".", "..") or not _SAFE_USERNAME.match(username):
        return None
    return username


class UrlResponse(Protocol):
    """The part of urlopen()'s response (an http.client.HTTPResponse for http/https) this project uses."""

    def __enter__(self) -> Self: ...
    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None: ...
    def read(self) -> bytes: ...


class UrlOpener(Protocol):
    def __call__(self, url: str | urllib.request.Request, *, timeout: float) -> UrlResponse: ...


def urlopen() -> UrlOpener:
    """urllib.request.urlopen, typed by what it returns (typeshed declares its response as Any). Looked up
    on every call, so a test's monkeypatch of urllib.request.urlopen still applies."""
    return urllib.request.urlopen


def redact_url(url: str) -> str:
    """scheme://host/path only: no credentials in the netloc, no query string. Webhook and refresh URLs
    carry tokens that must never land in the shared container log."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.hostname or ''}{f':{parts.port}' if parts.port else ''}{parts.path}"


# sqlite3 types every row it returns, and every value in one, as Any. These read them with real types:
# fetch_one()/fetch_all() return sqlite3.Row whatever the connection's row_factory, and cell() and its
# typed variants check each value's type as it's read, raising TypeError on a mismatch.


def fetch_one(cur: sqlite3.Cursor) -> sqlite3.Row | None:
    """The cursor's next row, or None when there are no more."""
    cur.row_factory = sqlite3.Row
    rows: list[object] = cur.fetchmany(1)
    return next((r for r in rows if isinstance(r, sqlite3.Row)), None)


def fetch_all(cur: sqlite3.Cursor) -> list[sqlite3.Row]:
    """Every remaining row of the cursor."""
    cur.row_factory = sqlite3.Row
    rows: list[object] = cur.fetchall()
    return [r for r in rows if isinstance(r, sqlite3.Row)]


def scalar(cur: sqlite3.Cursor) -> SqlValue:
    """The first column of the cursor's next row (e.g. a SELECT MAX(...)), or None when there is no row."""
    row = fetch_one(cur)
    return cell(row, 0) if row is not None else None


def scalar_int(cur: sqlite3.Cursor) -> int | None:
    """scalar() of an integer result (a COUNT, a PRAGMA), or None when there is no row."""
    row = fetch_one(cur)
    return cell_int(row, 0) if row is not None else None


def cell(row: sqlite3.Row, key: int | str) -> SqlValue:
    """row[key]: a column by position or, like sqlite3.Row itself, by case-insensitive name."""
    values: tuple[object, ...] = row[:]
    if isinstance(key, str):
        names = list(map(str.lower, row.keys()))
        if key.lower() not in names:
            raise IndexError("No item with that key")
        key = names.index(key.lower())
    value = values[key]
    if value is None or isinstance(value, str | int | float | bytes):
        return value
    raise TypeError(f"column {key} holds {type(value).__name__}, not an SQLite value")


def cell_str(row: sqlite3.Row, key: int | str) -> str | None:
    """A TEXT column (or NULL)."""
    value = cell(row, key)
    if value is None or isinstance(value, str):
        return value
    raise TypeError(f"column {key!r} holds {type(value).__name__}, not text")


def cell_int(row: sqlite3.Row, key: int | str) -> int | None:
    """An INTEGER column (or NULL)."""
    value = cell(row, key)
    if value is None or isinstance(value, int):
        return value
    raise TypeError(f"column {key!r} holds {type(value).__name__}, not an integer")


def cell_float(row: sqlite3.Row, key: int | str) -> float | None:
    """A REAL column (or NULL); an integer, which SQLite may hand back for one, passes through as is."""
    value = cell(row, key)
    if value is None or isinstance(value, int | float):
        return value
    raise TypeError(f"column {key!r} holds {type(value).__name__}, not a number")


def must_str(row: sqlite3.Row, key: int | str) -> str:
    """A NOT NULL TEXT column."""
    value = cell_str(row, key)
    if value is None:
        raise TypeError(f"column {key!r} is NULL")
    return value


def must_int(row: sqlite3.Row, key: int | str) -> int:
    """An integer result that can't be NULL (a COUNT, a SUM over at least one row)."""
    value = cell_int(row, key)
    if value is None:
        raise TypeError(f"column {key!r} is NULL")
    return value
