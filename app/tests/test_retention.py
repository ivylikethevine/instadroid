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


def test_merge_bumps_updated_at_without_touching_scraped_at(con_and_media):
    # The feed's ETag keys off updated_at precisely so a merge like this is visible even though
    # scraped_at (when the post was first seen) never changes.
    con, media = con_and_media
    scraped_at = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    con.execute(
        "INSERT INTO posts (id, username, kind, posted_date, caption, media_file, scraped_at,"
        " hash, url, place, posted_at, updated_at)"
        " VALUES ('h1','club','carousel','3 days ago','Photo 1 of 2 by Club, 5 likes',NULL,?,"
        "'h1',NULL,NULL,?,?)",
        (scraped_at, scraped_at, scraped_at),
    )
    con.commit()

    existing = con.execute("SELECT * FROM posts WHERE id='h1'").fetchone()
    now = datetime.now(UTC)
    final_id, fields, media_to_drop = scraper._merged_fields(
        existing, "h1", None, "h2", "carousel", "3 days ago", None, "The real caption", None, None, now
    )
    scraper._write_merged(con, existing["id"], final_id, fields)
    con.commit()

    row = con.execute("SELECT * FROM posts WHERE id=?", (final_id,)).fetchone()
    assert row["caption"] == "The real caption"
    assert row["scraped_at"] == scraped_at  # unchanged: still when it was first seen
    assert row["updated_at"] == now.isoformat()  # changed: this is when the content changed
    assert media_to_drop is None


def test_db_init_backfills_updated_at_for_rows_from_before_the_column_existed(tmp_path, monkeypatch):
    db = tmp_path / "posts.sqlite"
    media = tmp_path / "media"
    media.mkdir()
    monkeypatch.setattr(scraper, "DB_PATH", str(db))
    monkeypatch.setattr(scraper, "MEDIA_DIR", media)

    con = sqlite3.connect(db)
    con.execute(
        """CREATE TABLE posts (
            id TEXT PRIMARY KEY, username TEXT NOT NULL, kind TEXT, posted_date TEXT,
            caption TEXT, media_file TEXT, scraped_at TEXT NOT NULL,
            hash TEXT, url TEXT, place TEXT, posted_at TEXT
        )"""
    )
    scraped_at = datetime.now(UTC).isoformat()
    con.execute(
        "INSERT INTO posts VALUES ('h1','u','photo','x','cap',NULL,?,'h1',NULL,NULL,?)",
        (scraped_at, scraped_at),
    )
    con.execute("PRAGMA user_version = 1")  # already past the one-time dedupe migration
    con.commit()
    con.close()

    con = scraper.db_init()

    row = con.execute("SELECT updated_at FROM posts WHERE id='h1'").fetchone()
    assert row["updated_at"] == scraped_at


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


def test_db_init_migration_skips_a_corrupt_row_instead_of_crashing(tmp_path, monkeypatch):
    db = tmp_path / "posts.sqlite"
    media = tmp_path / "media"
    media.mkdir()
    monkeypatch.setattr(scraper, "DB_PATH", str(db))
    monkeypatch.setattr(scraper, "MEDIA_DIR", media)

    con = sqlite3.connect(db)
    con.execute(
        """CREATE TABLE posts (
            id TEXT PRIMARY KEY, username TEXT NOT NULL, kind TEXT, posted_date TEXT,
            caption TEXT, media_file TEXT, scraped_at TEXT NOT NULL,
            hash TEXT, url TEXT, place TEXT
        )"""
    )
    # One row with an unparseable scraped_at (e.g. hand-edited or corrupted), one normal row.
    con.execute(
        "INSERT INTO posts VALUES ('bad','u','photo','2 days ago','cap',NULL,'not-a-timestamp',"
        "'bad', NULL, NULL)"
    )
    con.execute(
        "INSERT INTO posts VALUES ('good','u2','photo','2 days ago','cap',NULL,?,'good', NULL, NULL)",
        (datetime.now(UTC).isoformat(),),
    )
    con.commit()
    con.close()

    con = scraper.db_init()  # must not raise, and must not loop forever on the corrupt row

    assert con.execute("PRAGMA user_version").fetchone()[0] == 1
    ids = {r[0] for r in con.execute("SELECT id FROM posts")}
    assert ids == {"bad", "good"}  # the corrupt row is left alone, not dropped or crashed on

    # Re-running db_init() (as a real restart would) must be a no-op, not a repeat crash.
    scraper.db_init()
