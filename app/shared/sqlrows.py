"""Typed reads of SQLite rows, shared by the scraper and the feed server.

sqlite3 types every row it returns, and every value in one, as Any. These read them with real types:
fetch_one()/fetch_all() return sqlite3.Row whatever the connection's row_factory, and cell() and its
typed variants check each value's type as it's read, raising TypeError on a mismatch."""

import sqlite3

type SqlValue = str | int | float | bytes | None  # what SQLite hands back for a column


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


def has_column(row: sqlite3.Row, name: str) -> bool:
    """Whether the row has a column `name` (case-insensitive, like cell()): False for a row read from a
    table that predates it. `name in row` would check the values instead."""
    return name.lower() in map(str.lower, row.keys())
