"""The feed server under test: it reads its settings from the environment at import, so each test
sets the environment and imports a fresh copy. Tests change its database between requests with hand-written
SQL: these are the older schemas the server has to keep reading."""

import sqlite3
import sys
from collections.abc import Mapping, Sequence
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from shared.sqlrows import SqlValue

# Legacy schema (no media, accounts or stories tables) with two posts, scraped in the order a run actually
# finds them (newest post first), so scraped_at DESC would get the order backwards; posted_at DESC must
# recover the true chronological order.
TWO_POSTS: str = """
CREATE TABLE posts (id TEXT PRIMARY KEY, username TEXT, kind TEXT, posted_date TEXT, caption TEXT,
    media_file TEXT, scraped_at TEXT, hash TEXT, url TEXT, place TEXT, posted_at TEXT);
INSERT INTO posts VALUES ('ABC', 'someone', 'photo', '2 days ago', 'Hi <there>', 'ABC.jpg',
    '2026-09-08T08:00:00+00:00', 'h1', 'https://www.instagram.com/p/ABC/', 'Anytown',
    '2026-09-08T09:00:00+00:00');
INSERT INTO posts VALUES ('h2', 'other', 'video', 'August 1', '', NULL,
    '2026-09-08T10:00:00+00:00', 'h2', NULL, NULL, '2026-09-07T09:00:00+00:00');
"""


def make_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, seed: str = TWO_POSTS, tz: str = ""
) -> TestClient:
    """A fresh feed server over tmp_path/posts.sqlite, created by the SQL script `seed` ("" leaves the
    database an empty file with no tables), with TZ set to `tz` whatever the environment has."""
    db: Path = tmp_path / "posts.sqlite"
    monkeypatch.setenv("DB_PATH", str(db))
    monkeypatch.setenv("MEDIA_DIR", str(tmp_path / "media"))
    monkeypatch.setenv("PUBLIC_URL", "http://feed.test")
    monkeypatch.setenv("TZ", tz)
    con: sqlite3.Connection = sqlite3.connect(db)
    con.executescript(seed)
    con.close()
    # Forget every feedserver module, so this import reads the environment again.
    name: str
    for name in [n for n in sys.modules if n == "feedserver" or n.startswith("feedserver.")]:
        del sys.modules[name]
    import feedserver

    return TestClient(feedserver.app)


def feed_sql(tmp_path: Path, *statements: str | tuple[str, Sequence[SqlValue]]) -> None:
    """Run each statement (SQL, or SQL and its parameters) on make_app's database, and commit."""
    con: sqlite3.Connection
    statement: str | tuple[str, Sequence[SqlValue]]
    with closing(sqlite3.connect(tmp_path / "posts.sqlite")) as con:
        for statement in statements:
            if isinstance(statement, str):
                con.execute(statement)
            else:
                con.execute(statement[0], statement[1])
        con.commit()


# The device columns of a legacy runs table; a table without them is older still.
DEVICE_COLUMNS: tuple[str, ...] = ("android_release TEXT", "android_sdk TEXT", "device_product TEXT")


def add_runs(
    tmp_path: Path, runs: Sequence[Mapping[str, SqlValue]], columns: Sequence[str] = DEVICE_COLUMNS
) -> None:
    """Rows, each {column: value}, in make_app's database's legacy runs table: its first columns plus
    `columns`, created if it isn't there yet."""
    feed_sql(
        tmp_path,
        "CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL,"
        f" finished_at TEXT NOT NULL, new_posts INTEGER, error TEXT{''.join(f', {c}' for c in columns)})",
        *(
            (f"INSERT INTO runs ({', '.join(run)}) VALUES ({', '.join('?' * len(run))})", tuple(run.values()))
            for run in runs
        ),
    )


def write_image(path: Path, size: tuple[int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, "blue").save(path)
