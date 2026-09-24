"""The database: migrations, merging duplicate posts and renamed accounts, recording runs, refresh bookkeeping."""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NoReturn, TypedDict, Unpack

import pytest
from instadroid import config, db, parsing
from shared.sqlrows import SqlValue

from tests.support import fetch_row, insert_post, sql_column


def test_merge_bumps_updated_at_without_touching_scraped_at(
    con: sqlite3.Connection,
) -> None:
    # The feed's ETag keys off updated_at precisely so a merge like this is visible even though
    # scraped_at (when the post was first seen) never changes.
    scraped_at: str = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    con.execute(
        "INSERT INTO posts (id, username, kind, posted_date, caption, media_file, scraped_at,"
        " hash, url, place, posted_at, updated_at)"
        " VALUES ('h1','club','carousel','3 days ago','Photo 1 of 2 by Club, 5 likes',NULL,?,"
        "'h1',NULL,NULL,?,?)",
        (scraped_at, scraped_at, scraped_at),
    )
    con.commit()

    existing: sqlite3.Row = fetch_row(con.execute("SELECT * FROM posts WHERE id='h1'"))
    now: datetime = datetime.now(UTC)
    merged: db.StoredPost
    media_to_drop: str | None
    merged, media_to_drop = db.merged_fields(existing, _candidate(caption="The real caption"), now)
    db.write_merged(con, "h1", merged)
    con.commit()

    row: sqlite3.Row = fetch_row(con.execute("SELECT * FROM posts WHERE id=?", (merged["id"],)))
    assert row["caption"] == "The real caption"
    assert row["scraped_at"] == scraped_at  # unchanged: still when it was first seen
    assert row["updated_at"] == now.isoformat()  # changed: this is when the content changed
    assert media_to_drop is None


class PostFields(TypedDict, total=False):
    """Any subset of db.PostRow's fields."""

    id: str
    username: str
    kind: str
    posted_date: str
    caption: str
    media_file: str | None
    scraped_at: str
    hash: str
    url: str | None
    place: str
    posted_at: str | None
    updated_at: str
    ig_version: str | None


def _candidate(**fields: Unpack[PostFields]) -> db.PostRow:
    """A freshly captured post with a hash id and no permalink, as scrape._store_post() builds it."""
    now: str = datetime.now(UTC).isoformat()
    row: db.PostRow = {
        "id": "h2", "username": "u", "kind": "carousel", "posted_date": "3 days ago", "caption": "Real caption",
        "media_file": None, "scraped_at": now, "hash": "h2", "url": None, "place": "", "posted_at": None,
        "updated_at": now, "ig_version": None,
    }  # fmt: skip
    row.update(fields)
    return row


def test_merge_keeps_the_first_seen_instagram_version(
    con: sqlite3.Connection,
) -> None:
    ts: str = datetime.now(UTC).isoformat()
    con.execute(
        "INSERT INTO posts (id, username, caption, scraped_at, hash, updated_at, ig_version)"
        " VALUES ('h1','club','Reel by Club',?,'h1',?,'400.0.0.1.1')",
        (ts, ts),
    )
    existing: sqlite3.Row = fetch_row(con.execute("SELECT * FROM posts WHERE id='h1'"))
    candidate: db.PostRow = _candidate(kind="video", posted_date="1 day ago", ig_version="445.0.0.45.83")
    merged: db.StoredPost
    _: str | None
    merged, _ = db.merged_fields(existing, candidate, datetime.now(UTC))
    db.write_merged(con, "h1", merged)
    assert (
        con.execute("SELECT ig_version FROM posts WHERE id=?", (merged["id"],)).fetchone()[0] == "400.0.0.1.1"
    )


def test_db_init_backfills_updated_at_for_rows_from_before_the_column_existed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_file: Path = tmp_path / "posts.sqlite"
    media: Path = tmp_path / "media"
    media.mkdir()
    monkeypatch.setattr(config, "DB_PATH", str(db_file))
    monkeypatch.setattr(config, "MEDIA_DIR", media)

    con: sqlite3.Connection = sqlite3.connect(db_file)
    con.execute(
        """CREATE TABLE posts (
            id TEXT PRIMARY KEY, username TEXT NOT NULL, kind TEXT, posted_date TEXT,
            caption TEXT, media_file TEXT, scraped_at TEXT NOT NULL,
            hash TEXT, url TEXT, place TEXT, posted_at TEXT
        )"""
    )
    scraped_at: str = datetime.now(UTC).isoformat()
    con.execute(
        "INSERT INTO posts VALUES ('h1','u','photo','x','cap',NULL,?,'h1',NULL,NULL,?)",
        (scraped_at, scraped_at),
    )
    con.execute("PRAGMA user_version = 1")  # already past the one-time dedupe migration
    con.commit()
    con.close()

    con = db.db_init()

    row: sqlite3.Row = fetch_row(con.execute("SELECT updated_at FROM posts WHERE id='h1'"))
    assert row["updated_at"] == scraped_at


def test_db_init_migration_merges_legacy_duplicate_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_file: Path = tmp_path / "posts.sqlite"
    media: Path = tmp_path / "media"
    media.mkdir()
    monkeypatch.setattr(config, "DB_PATH", str(db_file))
    monkeypatch.setattr(config, "MEDIA_DIR", media)

    # Simulate a pre-migration DB (no posted_at column) with the exact bug pattern seen in
    # production: the same post stored twice because one pass identified it from a weak
    # media-description caption before the real caption had rendered.
    con: sqlite3.Connection = sqlite3.connect(db_file)
    con.execute(
        """CREATE TABLE posts (
            id TEXT PRIMARY KEY, username TEXT NOT NULL, kind TEXT, posted_date TEXT,
            caption TEXT, media_file TEXT, scraped_at TEXT NOT NULL,
            hash TEXT, url TEXT, place TEXT
        )"""
    )
    (media / "weakhash.jpg").write_bytes(b"x")
    now: str = datetime.now(UTC).isoformat()
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

    con = db.db_init()  # runs the one-time dedupe migration

    posts: list[sqlite3.Row] = con.execute("SELECT id, caption, media_file FROM posts").fetchall()
    assert len(posts) == 1
    assert posts[0]["caption"] == "Attendance check! see you there"
    assert posts[0]["media_file"] == "weakhash.jpg"  # the only crop that exists is kept


def _legacy_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    """A pre-migration database (no posted_at/updated_at columns) at config.DB_PATH, open for seeding."""
    media: Path = tmp_path / "media"
    media.mkdir()
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "posts.sqlite"))
    monkeypatch.setattr(config, "MEDIA_DIR", media)
    con: sqlite3.Connection = sqlite3.connect(tmp_path / "posts.sqlite")
    con.execute(
        """CREATE TABLE posts (
            id TEXT PRIMARY KEY, username TEXT, kind TEXT, posted_date TEXT,
            caption TEXT, media_file TEXT, scraped_at TEXT NOT NULL,
            hash TEXT, url TEXT, place TEXT
        )"""
    )
    return con


def test_db_init_migration_skips_a_row_it_already_merged_away(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The permalink row is scraped first, so the merge keeps its id and deletes the hash-id duplicate;
    # when the loop then reaches the duplicate's id, the row is gone and is skipped, not crashed on.
    con: sqlite3.Connection = _legacy_db(tmp_path, monkeypatch)
    now: datetime = datetime.now(UTC)
    con.execute(
        "INSERT INTO posts VALUES ('ABC','club','photo','3 days ago','Same caption',NULL,?,"
        "'h1','https://www.instagram.com/p/ABC/',NULL)",
        ((now - timedelta(minutes=1)).isoformat(),),
    )
    con.execute(
        "INSERT INTO posts VALUES ('h2','club','photo','3 days ago','Same caption',NULL,?,'h2',NULL,NULL)",
        (now.isoformat(),),
    )
    con.commit()
    con.close()

    con = db.db_init()

    assert sql_column(con.execute("SELECT id FROM posts")) == ["ABC"]
    assert con.execute("PRAGMA user_version").fetchone()[0] == 5


def test_db_init_migration_skips_a_row_the_merge_raises_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    con: sqlite3.Connection = _legacy_db(tmp_path, monkeypatch)
    con.execute(
        "INSERT INTO posts VALUES ('odd','u','photo','2 days ago','cap',NULL,?,'odd',NULL,NULL)",
        (datetime.now(UTC).isoformat(),),
    )
    con.commit()
    con.close()

    def unexpected(
        con: sqlite3.Connection,
        username: str,
        posted_at: datetime | None,
        posted_at_prec: int | None,
        caption: str | None,
        exclude_id: str | None = None,
    ) -> NoReturn:
        raise RuntimeError("something this row does that nothing anticipated")

    monkeypatch.setattr(db, "find_duplicate", unexpected)
    con = db.db_init()

    assert sql_column(con.execute("SELECT id FROM posts")) == ["odd"]
    assert "WARN: dedupe migration skipped row 'odd': RuntimeError(" in capsys.readouterr().out
    assert con.execute("PRAGMA user_version").fetchone()[0] == 5


def test_accounts_backfill_skips_a_username_the_insert_rejects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "posts.sqlite"))
    con: sqlite3.Connection = db.db_init()
    now: str = datetime.now(UTC).isoformat()
    con.execute("INSERT INTO posts (id, username, scraped_at) VALUES ('p1', 'fine', ?)", (now,))
    con.execute("INSERT INTO posts (id, username, scraped_at) VALUES ('p2', 'cursed', ?)", (now,))
    con.execute(
        "CREATE TRIGGER no_cursed BEFORE INSERT ON accounts WHEN NEW.username = 'cursed'"
        " BEGIN SELECT RAISE(ABORT, 'no such account'); END"
    )
    con.execute("PRAGMA user_version = 1")  # back to before the accounts backfill
    con.commit()
    con.close()

    con = db.db_init()

    assert sql_column(con.execute("SELECT username FROM accounts ORDER BY username")) == ["fine"]
    assert "WARN: accounts backfill skipped 'cursed': IntegrityError(" in capsys.readouterr().out
    assert con.execute("PRAGMA user_version").fetchone()[0] == 5


def test_migration_drops_the_stories_expiry_column(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "posts.sqlite"))
    con: sqlite3.Connection = db.db_init()
    con.execute("ALTER TABLE stories ADD COLUMN expires_at TEXT")  # what db_init() created before v3
    con.execute("CREATE INDEX stories_expires_at ON stories(expires_at)")
    con.execute("PRAGMA user_version = 2")
    con.commit()
    con.close()

    con = db.db_init()

    columns: list[SqlValue] = sql_column(con.execute("SELECT name FROM pragma_table_info('stories')"))
    assert "expires_at" not in columns
    assert not sql_column(con.execute("SELECT name FROM sqlite_master WHERE name='stories_expires_at'"))
    assert con.execute("PRAGMA user_version").fetchone()[0] == 5


@pytest.mark.parametrize(
    ("stored_media", "kept", "dropped"),
    [(None, "h2.jpg", None), ("h1.jpg", "h1.jpg", "h2.jpg"), ("h2.jpg", "h2.jpg", None)],
)
def test_merge_keeps_the_stored_crop_and_drops_a_second_one(
    con: sqlite3.Connection, stored_media: str | None, kept: str, dropped: str | None
) -> None:
    now: str = datetime.now(UTC).isoformat()
    con.execute(
        "INSERT INTO posts (id, username, kind, posted_date, caption, media_file, scraped_at, hash,"
        " posted_at, updated_at) VALUES ('h1','u','carousel','3 days ago','Real caption',?,?,'h1',?,?)",
        (stored_media, now, now, now),
    )
    con.commit()
    existing: sqlite3.Row = fetch_row(con.execute("SELECT * FROM posts WHERE id='h1'"))
    merged: db.StoredPost
    media_to_drop: str | None
    merged, media_to_drop = db.merged_fields(existing, _candidate(media_file="h2.jpg"), datetime.now(UTC))
    assert (merged["media_file"], media_to_drop) == (kept, dropped)


def test_write_merged_replaces_a_hash_id_row_with_the_permalink_one(con: sqlite3.Connection) -> None:
    now: str = datetime.now(UTC).isoformat()
    con.execute(
        "INSERT INTO posts (id, username, kind, posted_date, caption, media_file, scraped_at, hash,"
        " posted_at, updated_at) VALUES ('h1','u','carousel','3 days ago','Real caption',NULL,?,'h1',?,?)",
        (now, now, now),
    )
    con.commit()
    existing: sqlite3.Row = fetch_row(con.execute("SELECT * FROM posts WHERE id='h1'"))
    merged: db.StoredPost
    _: str | None
    merged, _ = db.merged_fields(
        existing, _candidate(id="ABC", url="https://www.instagram.com/p/ABC/"), datetime.now(UTC)
    )
    assert merged["id"] == "ABC"  # a permalink id wins over a hash id
    db.write_merged(con, "h1", merged)
    con.commit()
    assert sql_column(con.execute("SELECT id FROM posts")) == ["ABC"]


def test_record_run_writes_a_row(con: sqlite3.Connection) -> None:
    started: str = datetime.now(UTC).isoformat()
    finished: str = (datetime.now(UTC) + timedelta(minutes=2)).isoformat()

    db.record_run(con, started, finished, 3, None, {"android_release": "13", "android_sdk": "33"})

    row: sqlite3.Row = fetch_row(con.execute("SELECT * FROM runs"))
    assert row["new_posts"] == 3
    assert row["error"] is None
    assert row["android_release"] == "13"
    assert row["device_product"] is None  # not in the snapshot dict
    assert row["link_sheet_failures"] == 0  # default when the caller doesn't pass any


def test_record_run_stores_link_failure_counts(con: sqlite3.Connection) -> None:
    started: str = datetime.now(UTC).isoformat()
    finished: str = (datetime.now(UTC) + timedelta(minutes=2)).isoformat()

    db.record_run(con, started, finished, 1, None, {}, link_sheet_failures=2, link_clipboard_failures=1)

    row: sqlite3.Row = fetch_row(con.execute("SELECT * FROM runs"))
    assert row["link_sheet_failures"] == 2
    assert row["link_clipboard_failures"] == 1


def test_record_run_stores_new_stories_count(con: sqlite3.Connection) -> None:
    started: str = datetime.now(UTC).isoformat()
    finished: str = (datetime.now(UTC) + timedelta(minutes=2)).isoformat()

    db.record_run(con, started, finished, 0, None, {}, new_stories=3)

    assert con.execute("SELECT new_stories FROM runs").fetchone()[0] == 3


def test_record_run_stores_selector_drift_stats(con: sqlite3.Connection) -> None:
    started: str = datetime.now(UTC).isoformat()
    finished: str = (datetime.now(UTC) + timedelta(minutes=2)).isoformat()

    db.record_run(
        con, started, finished, 0, None, {}, cards_per_screen=4.5, share_captioned=0.8, share_complete=0.9
    )

    row: sqlite3.Row = fetch_row(
        con.execute("SELECT cards_per_screen, share_captioned, share_complete FROM runs")
    )
    assert row["cards_per_screen"] == 4.5
    assert row["share_captioned"] == 0.8
    assert row["share_complete"] == 0.9


def test_db_init_creates_an_empty_stories_table(con: sqlite3.Connection) -> None:
    assert con.execute("SELECT COUNT(*) FROM stories").fetchone()[0] == 0
    db.db_init()  # re-run must be a no-op, not a crash


def test_db_init_migration_skips_a_corrupt_row_instead_of_crashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_file: Path = tmp_path / "posts.sqlite"
    media: Path = tmp_path / "media"
    media.mkdir()
    monkeypatch.setattr(config, "DB_PATH", str(db_file))
    monkeypatch.setattr(config, "MEDIA_DIR", media)

    con: sqlite3.Connection = sqlite3.connect(db_file)
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

    con = db.db_init()  # must not raise, and must not loop forever on the corrupt row

    # dedupe (v1), accounts backfill (v2), story-retention cleanup (v3), media_post index (v4), rehash (v5)
    assert con.execute("PRAGMA user_version").fetchone()[0] == 5
    ids: set[SqlValue] = set(sql_column(con.execute("SELECT id FROM posts")))
    assert ids == {"bad", "good"}  # the corrupt row is left alone, not dropped or crashed on

    # Re-running db_init() (as a real restart would) must be a no-op, not a repeat crash.
    db.db_init()


def test_migration_backfills_an_accounts_row_for_every_existing_username(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_file: Path = tmp_path / "posts.sqlite"
    media: Path = tmp_path / "media"
    media.mkdir()
    monkeypatch.setattr(config, "DB_PATH", str(db_file))
    monkeypatch.setattr(config, "MEDIA_DIR", media)

    con: sqlite3.Connection = sqlite3.connect(db_file)
    con.execute(
        """CREATE TABLE posts (
            id TEXT PRIMARY KEY, username TEXT NOT NULL, kind TEXT, posted_date TEXT,
            caption TEXT, media_file TEXT, scraped_at TEXT NOT NULL,
            hash TEXT, url TEXT, place TEXT, posted_at TEXT, updated_at TEXT
        )"""
    )
    now: str = datetime.now(UTC).isoformat()
    con.execute(
        "INSERT INTO posts VALUES ('h1','club','photo','x','cap','h1.jpg',?,'h1',NULL,NULL,?,?)",
        (now, now, now),
    )
    con.execute("PRAGMA user_version = 1")  # already past the dedupe migration
    con.commit()
    con.close()

    con = db.db_init()

    assert con.execute("PRAGMA user_version").fetchone()[0] == 5  # accounts backfill (v2), v3, v4, v5
    assert con.execute("SELECT username FROM accounts WHERE username='club'").fetchone() is not None
    # media_file / the media table are untouched: no backfill needed there.
    assert con.execute("SELECT media_file FROM posts WHERE id='h1'").fetchone()[0] == "h1.jpg"
    assert con.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 0

    db.db_init()  # re-run must be a no-op


def test_migration_drops_the_redundant_media_post_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_file: Path = tmp_path / "posts.sqlite"
    monkeypatch.setattr(config, "DB_PATH", str(db_file))
    con: sqlite3.Connection = db.db_init()
    con.execute("CREATE INDEX media_post ON media(post_id)")  # what db_init() created before v4
    con.execute("PRAGMA user_version = 3")
    con.commit()
    con.close()

    con = db.db_init()

    assert con.execute("PRAGMA user_version").fetchone()[0] == 5
    assert not sql_column(con.execute("SELECT name FROM sqlite_master WHERE name='media_post'"))


def test_rehash_migration_skips_a_row_without_an_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A corrupt legacy row without an id is skipped, not crashed on, and the migration still finishes."""
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "posts.sqlite"))
    con: sqlite3.Connection = db.db_init()
    con.execute(
        "INSERT INTO posts (id, username, caption, scraped_at, hash) VALUES (NULL, 'u', 'cap', 'x', 'old')"
    )
    con.execute("PRAGMA user_version = 4")
    con.commit()
    con.close()

    con = db.db_init()

    assert con.execute("PRAGMA user_version").fetchone()[0] == 5
    assert sql_column(con.execute("SELECT hash FROM posts WHERE id IS NULL")) == ["old"]


def test_migration_rehashes_stored_posts_to_the_current_post_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After parsing._post_key() changed, stored hashes are recomputed from the stored caption so the
    next run recognises every post outright instead of re-capturing and merging it."""
    db_file: Path = tmp_path / "posts.sqlite"
    monkeypatch.setattr(config, "DB_PATH", str(db_file))
    con: sqlite3.Connection = db.db_init()
    insert_post(con, config.MEDIA_DIR, "captioned", days_old=1)
    insert_post(con, config.MEDIA_DIR, "weak", days_old=1)
    con.execute(
        "UPDATE posts SET username='u', caption='First line here\nsecond', hash='old1' WHERE id='captioned'"
    )
    con.execute(
        "UPDATE posts SET username='u', caption='Photo 2 of 5 by U, 9 likes', hash='old2' WHERE id='weak'"
    )
    con.execute("PRAGMA user_version = 4")
    con.commit()
    con.close()

    con = db.db_init()

    assert con.execute("PRAGMA user_version").fetchone()[0] == 5
    card: parsing.Post = parsing._new_post("u", "photo", "", "", 0)
    card["caption"] = "First line here…"  # the same post, seen truncated
    assert db.stored_post(fetch_row(con.execute("SELECT * FROM posts WHERE id='captioned'")))[
        "hash"
    ] == parsing.post_id(card)
    card["caption"], card["alt"] = "", "Photo 1 of 5 by U, 12 likes, 3 comments"
    assert db.stored_post(fetch_row(con.execute("SELECT * FROM posts WHERE id='weak'")))[
        "hash"
    ] == parsing.post_id(card)


def test_merge_accounts_repoints_posts_and_drops_old_account_row(
    con: sqlite3.Connection,
) -> None:
    insert_post(con, config.MEDIA_DIR, "p1", days_old=1)
    con.execute("UPDATE posts SET username='old_handle' WHERE id='p1'")
    con.execute("INSERT INTO accounts (username, account_id) VALUES ('old_handle', 'acct123')")
    con.commit()

    moved: int = db.rename_account(con, "old_handle", "new_handle")

    assert moved == 1
    assert con.execute("SELECT username FROM posts WHERE id='p1'").fetchone()[0] == "new_handle"
    assert con.execute("SELECT COUNT(*) FROM accounts WHERE username='old_handle'").fetchone()[0] == 0
    new_account: sqlite3.Row = fetch_row(
        con.execute("SELECT account_id FROM accounts WHERE username='new_handle'")
    )
    assert new_account["account_id"] == "acct123"  # carried over from the old handle


def test_merge_accounts_is_a_noop_for_the_same_username(
    con: sqlite3.Connection,
) -> None:
    assert db.rename_account(con, "same", "same") == 0


def test_rename_account_keeps_a_followed_allowlist_entry_in_sync(
    con: sqlite3.Connection,
) -> None:
    con.execute("INSERT INTO following (username, updated_at) VALUES ('old_handle', '2020-01-01')")
    con.commit()

    db.rename_account(con, "old_handle", "new_handle")

    assert set(sql_column(con.execute("SELECT username FROM following"))) == {"new_handle"}


def test_rename_account_leaves_the_allowlist_alone_when_the_old_name_wasnt_on_it(
    con: sqlite3.Connection,
) -> None:
    con.execute("INSERT INTO following (username, updated_at) VALUES ('someone_else', '2020-01-01')")
    con.commit()

    db.rename_account(con, "old_handle", "new_handle")

    assert set(sql_column(con.execute("SELECT username FROM following"))) == {"someone_else"}


def test_needs_avatar_refresh_true_when_never_captured(
    con: sqlite3.Connection,
) -> None:
    con.execute("INSERT INTO accounts (username) VALUES ('u')")
    con.commit()
    assert db.needs_avatar_refresh(con, "u") is True


def test_needs_avatar_refresh_false_when_recently_captured(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "AVATAR_REFRESH_DAYS", 14)
    now: str = datetime.now(UTC).isoformat()
    con.execute(
        "INSERT INTO accounts (username, avatar_file, avatar_updated_at) VALUES ('u', 'avatars/u.jpg', ?)",
        (now,),
    )
    con.commit()
    assert db.needs_avatar_refresh(con, "u") is False


def test_needs_avatar_refresh_true_when_stale(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "AVATAR_REFRESH_DAYS", 14)
    old: str = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    con.execute(
        "INSERT INTO accounts (username, avatar_file, avatar_updated_at) VALUES ('u', 'avatars/u.jpg', ?)",
        (old,),
    )
    con.commit()
    assert db.needs_avatar_refresh(con, "u") is True


def test_needs_following_refresh_true_when_never_captured(
    con: sqlite3.Connection,
) -> None:
    assert db.needs_following_refresh(con) is True


def test_needs_following_refresh_false_when_recently_captured(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "FOLLOWING_REFRESH_DAYS", 7)
    now: str = datetime.now(UTC).isoformat()
    con.execute("INSERT INTO following (username, updated_at) VALUES ('u', ?)", (now,))
    con.commit()
    assert db.needs_following_refresh(con) is False


def test_needs_following_refresh_true_when_stale(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "FOLLOWING_REFRESH_DAYS", 7)
    old: str = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    con.execute("INSERT INTO following (username, updated_at) VALUES ('u', ?)", (old,))
    con.commit()
    assert db.needs_following_refresh(con) is True
