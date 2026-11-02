"""Backfilling a permalink for a post stored under a hash id, when that post is back on screen."""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NamedTuple

import pytest
from instadroid import config, db, parsing, scrape
from shared import sqlrows

from tests.deviceflows import feed_device
from tests.fakedevice import FakeDevice
from tests.support import insert_post

pytestmark: pytest.MarkDecorator = pytest.mark.usefixtures("fast_offline")

NOW: datetime = datetime.now(UTC)
# A day ago: recent enough that retention (RETAIN_DAYS) keeps the rows, older than any update a run makes.
OLD: str = (NOW - timedelta(days=1)).isoformat()


def _seed_hash_post(con: sqlite3.Connection, h: str, username: str, attempts: int = 0) -> None:
    """A post stored under its hash, with no permalink, last updated at OLD."""
    insert_post(con, h, username=username, caption="a caption", now=NOW, permalink_attempts=attempts)


def _row(con: sqlite3.Connection, h: str) -> tuple[str, str | None, str, int]:
    found: sqlite3.Row | None = sqlrows.fetch_one(
        con.execute("SELECT id, url, updated_at, permalink_attempts FROM posts WHERE hash=?", (h,))
    )
    assert found is not None
    return (
        sqlrows.must_str(found, "id"),
        sqlrows.cell_str(found, "url"),
        sqlrows.must_str(found, "updated_at"),
        sqlrows.must_int(found, "permalink_attempts"),
    )


class QuietRun(NamedTuple):
    con: sqlite3.Connection
    d: FakeDevice
    top: str
    other: str


@pytest.fixture
def quiet_run(con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> QuietRun:
    """One Following screen on a fresh database, with no stories and a stop once both posts are seen;
    nothing is stored yet."""
    monkeypatch.setattr(config, "MAX_STORIES_PER_RUN", 0)
    monkeypatch.setattr(config, "STOP_AFTER_SEEN", 2)
    monkeypatch.setattr(config, "PERMALINK_BACKFILL_PER_RUN", 3)
    monkeypatch.setattr(config, "PERMALINK_BACKFILL_TRIES", 3)
    d: FakeDevice = feed_device()
    other: parsing.Post
    top: parsing.Post
    top, other = parsing.parse_hierarchy(d.screens["following"])  # the Reel, then the carousel
    return QuietRun(con, d, parsing.post_id(top), parsing.post_id(other))


@pytest.fixture
def stored(quiet_run: QuietRun) -> QuietRun:
    """quiet_run with both posts stored under their hashes."""
    _seed_hash_post(quiet_run.con, quiet_run.top, "someone_nice")
    _seed_hash_post(quiet_run.con, quiet_run.other, "other_user")
    return quiet_run


def test_a_stored_post_without_a_permalink_gets_one_and_keeps_its_id(stored: QuietRun) -> None:
    stats: scrape.RunStats = scrape.scrape_once(stored.d, stored.con)

    assert stats["new"] == 0
    h: str
    url: str
    for h, url in (
        (stored.top, "https://www.instagram.com/reel/TOP123/"),
        (stored.other, "https://www.instagram.com/p/OTHER1/"),
    ):
        attempts: int
        post_id: str
        stored_url: str | None
        updated_at: str
        post_id, stored_url, updated_at, attempts = _row(stored.con, h)
        assert post_id == h  # the Atom entry id is derived from it, so it never changes
        assert stored_url == url
        assert updated_at > OLD  # so the feed's ETag moves and readers pick the link up
        assert attempts == 1


def test_backfill_attempts_per_run_are_capped(stored: QuietRun, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "PERMALINK_BACKFILL_PER_RUN", 1)
    scrape.scrape_once(stored.d, stored.con)
    urls: list[str | None] = [_row(stored.con, h)[1] for h in (stored.top, stored.other)]
    assert sum(url is not None for url in urls) == 1


def test_a_failed_copy_counts_and_a_post_is_not_retried_forever(
    quiet_run: QuietRun, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "PERMALINK_BACKFILL_TRIES", 2)
    con: sqlite3.Connection = quiet_run.con
    d: FakeDevice = feed_device(
        top_share="share_noclip"
    )  # the Reel's Copy link copies nothing; the carousel's works
    _seed_hash_post(con, quiet_run.top, "someone_nice")
    _seed_hash_post(con, quiet_run.other, "other_user", attempts=2)  # already tried as often as allowed

    scrape.scrape_once(d, con)

    assert _row(con, quiet_run.top)[1:] == (None, OLD, 1)  # tried once, nothing to write
    # Not tried again, though its link would have worked.
    assert _row(con, quiet_run.other)[1:] == (None, OLD, 2)
    assert "share_noclip" in d.history and "share_other" not in d.history


def test_a_shortcode_already_stored_elsewhere_is_not_taken(quiet_run: QuietRun) -> None:
    con: sqlite3.Connection = quiet_run.con
    _seed_hash_post(con, quiet_run.top, "someone_nice")
    con.execute(
        "INSERT INTO posts (id, username, kind, posted_date, caption, scraped_at, hash, url, updated_at)"
        " VALUES ('TOP123', 'someone_nice', 'video', 'x', 'c', ?, 'otherhash', ?, ?)",
        (OLD, "https://www.instagram.com/reel/TOP123/", OLD),
    )
    con.commit()
    scrape.scrape_once(quiet_run.d, con)
    assert (
        _row(con, quiet_run.top)[1] is None
    )  # TOP123 is already its own row: don't give the same link twice


def test_zero_per_run_disables_backfilling(stored: QuietRun, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "PERMALINK_BACKFILL_PER_RUN", 0)
    scrape.scrape_once(stored.d, stored.con)
    assert "share_top" not in stored.d.history and "share_other" not in stored.d.history
    assert _row(stored.con, stored.top)[1] is None and _row(stored.con, stored.other)[1] is None


def test_the_attempts_column_is_added_to_an_existing_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path: Path = tmp_path / "old.sqlite"
    monkeypatch.setattr(config, "DB_PATH", str(path))
    old: sqlite3.Connection = sqlite3.connect(path)
    old.execute(
        "CREATE TABLE posts (id TEXT PRIMARY KEY, username TEXT NOT NULL, kind TEXT, posted_date TEXT,"
        " caption TEXT, media_file TEXT, scraped_at TEXT NOT NULL)"
    )
    old.commit()
    old.close()
    con: sqlite3.Connection = db.db_init()
    columns: set[str] = {
        sqlrows.must_str(row, 1) for row in sqlrows.fetch_all(con.execute("PRAGMA table_info(posts)"))
    }
    assert "permalink_attempts" in columns
