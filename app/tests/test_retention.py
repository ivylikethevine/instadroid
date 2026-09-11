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


def test_dump_debug_does_not_raise_on_a_write_failure(tmp_path, monkeypatch):
    # e.g. a stale file left owned by a different uid from a `docker exec -u root` session, or
    # here: DEBUG_DIR itself can't be created because something else already occupies that path.
    blocked = tmp_path / "debug"
    blocked.write_text("not a directory")
    monkeypatch.setattr(scraper, "DEBUG_DIR", blocked)

    class FakeDevice:
        def dump_hierarchy(self):
            return "<hierarchy/>"

    scraper._dump_debug(FakeDevice(), "whatever")  # must not raise


def test_record_run_writes_a_row(con_and_media):
    con, _ = con_and_media
    started = datetime.now(UTC).isoformat()
    finished = (datetime.now(UTC) + timedelta(minutes=2)).isoformat()

    scraper.record_run(con, started, finished, 3, None, {"android_release": "13", "android_sdk": "33"})

    row = con.execute("SELECT * FROM runs").fetchone()
    assert row["new_posts"] == 3
    assert row["error"] is None
    assert row["android_release"] == "13"
    assert row["device_product"] is None  # not in the snapshot dict
    assert row["link_sheet_failures"] == 0  # default when the caller doesn't pass any


def test_record_run_stores_link_failure_counts(con_and_media):
    con, _ = con_and_media
    started = datetime.now(UTC).isoformat()
    finished = (datetime.now(UTC) + timedelta(minutes=2)).isoformat()

    scraper.record_run(con, started, finished, 1, None, {}, link_sheet_failures=2, link_clipboard_failures=1)

    row = con.execute("SELECT * FROM runs").fetchone()
    assert row["link_sheet_failures"] == 2
    assert row["link_clipboard_failures"] == 1


def test_record_run_stores_new_stories_count(con_and_media):
    con, _ = con_and_media
    started = datetime.now(UTC).isoformat()
    finished = (datetime.now(UTC) + timedelta(minutes=2)).isoformat()

    scraper.record_run(con, started, finished, 0, None, {}, new_stories=3)

    assert con.execute("SELECT new_stories FROM runs").fetchone()[0] == 3


def test_db_init_creates_an_empty_stories_table(con_and_media):
    con, _ = con_and_media
    assert con.execute("SELECT COUNT(*) FROM stories").fetchone()[0] == 0
    scraper.db_init()  # re-run must be a no-op, not a crash


def _insert_story(con, media_dir, story_id, hours_old, username="u", media_file=None):
    now = datetime.now(UTC)
    scraped_at = (now - timedelta(hours=hours_old)).isoformat()
    expires_at = (now - timedelta(hours=hours_old - scraper.STORY_RETAIN_HOURS)).isoformat()
    if media_file:
        (media_dir / "stories").mkdir(parents=True, exist_ok=True)
        (media_dir / media_file).write_bytes(b"x")
    con.execute(
        "INSERT INTO stories (id, username, media_file, kind, posted_date, scraped_at, expires_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (story_id, username, media_file, "story", "1h", scraped_at, expires_at),
    )
    con.commit()


def test_prune_expired_stories_removes_only_rows_past_their_expiry(con_and_media):
    con, media = con_and_media
    _insert_story(con, media, "old", hours_old=30, media_file="stories/old.jpg")
    _insert_story(con, media, "fresh", hours_old=1, media_file="stories/fresh.jpg")

    scraper._prune_expired_stories(con)

    ids = {r[0] for r in con.execute("SELECT id FROM stories")}
    assert ids == {"fresh"}
    assert not (media / "stories" / "old.jpg").exists()
    assert (media / "stories" / "fresh.jpg").exists()


def test_prune_expired_stories_noop_when_none_expired(con_and_media):
    con, media = con_and_media
    _insert_story(con, media, "fresh", hours_old=1, media_file="stories/fresh.jpg")

    scraper._prune_expired_stories(con)

    assert con.execute("SELECT COUNT(*) FROM stories").fetchone()[0] == 1


def test_launch_app_falls_back_to_monkey_launch_without_recursing_forever():
    # resolve-activity failing used to recurse into _launch_app itself instead of falling back,
    # which is unbounded recursion, not a fallback.
    class FakeDevice:
        def __init__(self):
            self.app_start_calls = []

        def shell(self, args):
            raise RuntimeError("resolve-activity unavailable")

        def app_start(self, pkg, activity=None, stop=None):
            self.app_start_calls.append((pkg, activity, stop))

    d = FakeDevice()
    scraper._launch_app(d)  # must not raise RecursionError
    assert d.app_start_calls == [(scraper.IG_PKG, None, False)]


def test_device_snapshot_tolerates_shell_failures():
    class BrokenDevice:
        def shell(self, cmd):
            raise RuntimeError("adb not connected")

    snapshot = scraper._device_snapshot(BrokenDevice())
    assert snapshot == {
        "android_release": None,
        "android_sdk": None,
        "device_product": None,
        "ig_version": None,
        "redroid_image": None,
    }


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

    # dedupe (v1), accounts backfill (v2), post hash upgrade (v3)
    assert con.execute("PRAGMA user_version").fetchone()[0] == 3
    ids = {r[0] for r in con.execute("SELECT id FROM posts")}
    assert ids == {"bad", "good"}  # the corrupt row is left alone, not dropped or crashed on

    # Re-running db_init() (as a real restart would) must be a no-op, not a repeat crash.
    scraper.db_init()


def test_migration_backfills_an_accounts_row_for_every_existing_username(tmp_path, monkeypatch):
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
            hash TEXT, url TEXT, place TEXT, posted_at TEXT, updated_at TEXT
        )"""
    )
    now = datetime.now(UTC).isoformat()
    con.execute(
        "INSERT INTO posts VALUES ('h1','club','photo','x','cap','h1.jpg',?,'h1',NULL,NULL,?,?)",
        (now, now, now),
    )
    con.execute("PRAGMA user_version = 1")  # already past the dedupe migration
    con.commit()
    con.close()

    con = scraper.db_init()

    assert con.execute("PRAGMA user_version").fetchone()[0] == 3  # accounts (v2), then hash upgrade (v3)
    assert con.execute("SELECT username FROM accounts WHERE username='club'").fetchone() is not None
    # media_file / the media table are untouched: no backfill needed there.
    assert con.execute("SELECT media_file FROM posts WHERE id='h1'").fetchone()[0] == "h1.jpg"
    assert con.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 0

    scraper.db_init()  # re-run must be a no-op


def test_prune_old_posts_also_removes_extra_carousel_media(con_and_media, monkeypatch):
    con, media = con_and_media
    monkeypatch.setattr(scraper, "RETAIN_DAYS", 30)
    _insert(con, media, "old", days_old=45, media_file="old.jpg")
    (media / "old_1.jpg").write_bytes(b"x")
    con.execute("INSERT INTO media (post_id, idx, file) VALUES ('old', 1, 'old_1.jpg')")
    con.commit()

    scraper._prune_old_posts(con)

    assert con.execute("SELECT COUNT(*) FROM posts WHERE id='old'").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM media WHERE post_id='old'").fetchone()[0] == 0
    assert not (media / "old.jpg").exists()
    assert not (media / "old_1.jpg").exists()


def test_prune_old_posts_leaves_avatars_alone(con_and_media, monkeypatch):
    # The orphan sweep globs MEDIA_DIR non-recursively; avatars/ must be structurally immune.
    con, media = con_and_media
    monkeypatch.setattr(scraper, "RETAIN_DAYS", 0)
    avatars = media / "avatars"
    avatars.mkdir()
    (avatars / "someone.jpg").write_bytes(b"x")

    scraper._prune_old_posts(con)

    assert (avatars / "someone.jpg").exists()


def test_size_cap_disabled_when_media_max_mb_is_zero(con_and_media, monkeypatch):
    con, media = con_and_media
    monkeypatch.setattr(scraper, "MEDIA_MAX_MB", 0)
    _insert(con, media, "a", days_old=1, media_file="a.jpg")
    (media / "a.jpg").write_bytes(b"x" * 500_000)

    scraper._prune_old_posts(con)

    assert con.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 1


def test_size_cap_removes_oldest_posts_first_when_over_budget(con_and_media, monkeypatch):
    con, media = con_and_media
    _insert(con, media, "older", days_old=5, media_file="older.jpg")
    _insert(con, media, "newer", days_old=1, media_file="newer.jpg")
    (media / "older.jpg").write_bytes(b"x" * 500_000)
    (media / "newer.jpg").write_bytes(b"x" * 10_000)

    baseline = scraper._media_and_db_size_mb()
    monkeypatch.setattr(scraper, "MEDIA_MAX_MB", baseline - 0.3)  # reachable only by dropping "older"

    scraper._prune_old_posts(con)

    ids = {r[0] for r in con.execute("SELECT id FROM posts")}
    assert ids == {"newer"}
    assert not (media / "older.jpg").exists()
    assert (media / "newer.jpg").exists()


def test_size_cap_stops_when_no_posts_remain(con_and_media, monkeypatch):
    con, media = con_and_media
    monkeypatch.setattr(scraper, "MEDIA_MAX_MB", 0.0000001)  # unreachable even with zero posts
    _insert(con, media, "only", days_old=1, media_file="only.jpg")

    scraper._prune_old_posts(con)  # must terminate rather than spin

    assert con.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 0


def test_merge_accounts_repoints_posts_and_drops_old_account_row(con_and_media):
    con, media = con_and_media
    _insert(con, media, "p1", days_old=1)
    con.execute("UPDATE posts SET username='old_handle' WHERE id='p1'")
    con.execute("INSERT INTO accounts (username, account_id) VALUES ('old_handle', 'acct123')")
    con.commit()

    moved = scraper.rename_account(con, "old_handle", "new_handle")

    assert moved == 1
    assert con.execute("SELECT username FROM posts WHERE id='p1'").fetchone()[0] == "new_handle"
    assert con.execute("SELECT COUNT(*) FROM accounts WHERE username='old_handle'").fetchone()[0] == 0
    new_account = con.execute("SELECT account_id FROM accounts WHERE username='new_handle'").fetchone()
    assert new_account["account_id"] == "acct123"  # carried over from the old handle


def test_merge_accounts_is_a_noop_for_the_same_username(con_and_media):
    con, _ = con_and_media
    assert scraper.rename_account(con, "same", "same") == 0


def test_needs_avatar_refresh_true_when_never_captured(con_and_media):
    con, _ = con_and_media
    con.execute("INSERT INTO accounts (username) VALUES ('u')")
    con.commit()
    assert scraper._needs_avatar_refresh(con, "u") is True


def test_needs_avatar_refresh_false_when_recently_captured(con_and_media, monkeypatch):
    con, _ = con_and_media
    monkeypatch.setattr(scraper, "AVATAR_REFRESH_DAYS", 14)
    now = datetime.now(UTC).isoformat()
    con.execute(
        "INSERT INTO accounts (username, avatar_file, avatar_updated_at) VALUES ('u', 'avatars/u.jpg', ?)",
        (now,),
    )
    con.commit()
    assert scraper._needs_avatar_refresh(con, "u") is False


def test_needs_avatar_refresh_true_when_stale(con_and_media, monkeypatch):
    con, _ = con_and_media
    monkeypatch.setattr(scraper, "AVATAR_REFRESH_DAYS", 14)
    old = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    con.execute(
        "INSERT INTO accounts (username, avatar_file, avatar_updated_at) VALUES ('u', 'avatars/u.jpg', ?)",
        (old,),
    )
    con.commit()
    assert scraper._needs_avatar_refresh(con, "u") is True
