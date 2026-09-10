import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
import scraper


@pytest.fixture
def con_and_media(tmp_path, monkeypatch):
    db = tmp_path / "posts.sqlite"
    media = tmp_path / "media"
    media.mkdir()
    monkeypatch.setattr(scraper, "DB_PATH", str(db))
    monkeypatch.setattr(scraper, "MEDIA_DIR", media)
    con = scraper.db_init()
    return con, media


def _insert(con, media_dir, post_id, days_old, media_file=None):
    ts = (datetime.now(UTC) - timedelta(days=days_old)).isoformat()
    if media_file:
        (media_dir / media_file).write_bytes(b"x")
    con.execute(
        "INSERT INTO posts (id, username, kind, posted_date, caption, media_file, scraped_at, posted_at)"
        " VALUES (?,'u','photo','x','cap',?,?,?)",
        (post_id, media_file, ts, ts),
    )
    con.commit()


def test_prune_old_posts_deletes_rows_and_media_past_retain_days(con_and_media, monkeypatch):
    con, media = con_and_media
    monkeypatch.setattr(scraper, "RETAIN_DAYS", 30)
    _insert(con, media, "old", days_old=45, media_file="old.jpg")
    _insert(con, media, "new", days_old=1, media_file="new.jpg")

    scraper._prune_old_posts(con)

    ids = {r[0] for r in con.execute("SELECT id FROM posts")}
    assert ids == {"new"}
    assert not (media / "old.jpg").exists()
    assert (media / "new.jpg").exists()


def test_prune_old_posts_disabled_when_retain_days_is_zero(con_and_media, monkeypatch):
    con, media = con_and_media
    monkeypatch.setattr(scraper, "RETAIN_DAYS", 0)
    _insert(con, media, "ancient", days_old=9999, media_file="ancient.jpg")

    scraper._prune_old_posts(con)

    assert con.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 1
    assert (media / "ancient.jpg").exists()


def test_prune_old_posts_removes_orphaned_media_regardless_of_retain_days(con_and_media, monkeypatch):
    con, media = con_and_media
    monkeypatch.setattr(scraper, "RETAIN_DAYS", 0)
    _insert(con, media, "kept", days_old=1, media_file="kept.jpg")
    (media / "orphan.jpg").write_bytes(b"x")  # e.g. left behind by an interrupted run

    scraper._prune_old_posts(con)

    assert {f.name for f in media.iterdir()} == {"kept.jpg"}


def test_db_init_migration_merges_legacy_duplicate_rows(tmp_path, monkeypatch):
    db = tmp_path / "posts.sqlite"
    media = tmp_path / "media"
    media.mkdir()
    monkeypatch.setattr(scraper, "DB_PATH", str(db))
    monkeypatch.setattr(scraper, "MEDIA_DIR", media)

    # Simulate a pre-migration DB (no posted_at column) with the exact bug pattern seen in
    # production: the same post stored twice because one pass identified it from a weak
    # media-description caption before the real caption had rendered.
    con = sqlite3.connect(db)
    con.execute(
        """CREATE TABLE posts (
            id TEXT PRIMARY KEY, username TEXT NOT NULL, kind TEXT, posted_date TEXT,
            caption TEXT, media_file TEXT, scraped_at TEXT NOT NULL,
            hash TEXT, url TEXT, place TEXT
        )"""
    )
    (media / "weakhash.jpg").write_bytes(b"x")
    now = datetime.now(UTC).isoformat()
    con.execute(
        "INSERT INTO posts VALUES ('weakhash','club','carousel','3 days ago',"
        "'Photo 1 of 2 by Club, 113 likes',?,?, 'weakhash', NULL, NULL)",
        ("weakhash.jpg", now),
    )
    con.execute(
        "INSERT INTO posts VALUES ('realhash','club','carousel','3 days ago',"
        "'Attendance check! see you there',NULL,?, 'realhash', NULL, NULL)",
        (now,),
    )
    con.commit()
    con.close()

    con = scraper.db_init()  # runs the one-time dedupe migration

    posts = con.execute("SELECT id, caption, media_file FROM posts").fetchall()
    assert len(posts) == 1
    assert posts[0]["caption"] == "Attendance check! see you there"
    assert posts[0]["media_file"] == "weakhash.jpg"  # the only crop that exists is kept

    # Running db_init() again must not re-merge or error (PRAGMA user_version guards it).
    scraper.db_init()
    assert con.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 1
