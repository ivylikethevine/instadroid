"""The feed server under test: it reads its settings from the environment at import, so each test
sets the environment and imports a fresh copy."""

import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image


def fresh_feedserver() -> None:
    """Forget every feedserver module, so the next `import feedserver` reads the environment again."""
    for name in [n for n in sys.modules if n == "feedserver" or n.startswith("feedserver.")]:
        del sys.modules[name]


# Legacy schema (no media, accounts or stories tables) with two posts, scraped in the order a run actually
# finds them (newest post first), so scraped_at DESC would get the order backwards; posted_at DESC must
# recover the true chronological order.
TWO_POSTS = """
CREATE TABLE posts (id TEXT PRIMARY KEY, username TEXT, kind TEXT, posted_date TEXT, caption TEXT,
    media_file TEXT, scraped_at TEXT, hash TEXT, url TEXT, place TEXT, posted_at TEXT);
INSERT INTO posts VALUES ('ABC', 'someone', 'photo', '2 days ago', 'Hi <there>', 'ABC.jpg',
    '2026-09-08T08:00:00+00:00', 'h1', 'https://www.instagram.com/p/ABC/', 'Anytown',
    '2026-09-08T09:00:00+00:00');
INSERT INTO posts VALUES ('h2', 'other', 'video', 'August 1', '', NULL,
    '2026-09-08T10:00:00+00:00', 'h2', NULL, NULL, '2026-09-07T09:00:00+00:00');
"""


def make_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, seed: str = TWO_POSTS) -> TestClient:
    """A fresh feed server over tmp_path/posts.sqlite, created by the SQL script `seed` ("" leaves the
    database an empty file with no tables)."""
    db = tmp_path / "posts.sqlite"
    monkeypatch.setenv("DB_PATH", str(db))
    monkeypatch.setenv("MEDIA_DIR", str(tmp_path / "media"))
    monkeypatch.setenv("PUBLIC_URL", "http://feed.test")
    con = sqlite3.connect(db)
    con.executescript(seed)
    con.close()
    fresh_feedserver()
    import feedserver

    return TestClient(feedserver.app)


def write_image(path: Path, size: tuple[int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, "blue").save(path)
