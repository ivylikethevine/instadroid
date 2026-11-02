"""The caption search index: triggers keep it in step with posts through every kind of write, and the
migration fills it from a database that predates it."""

import sqlite3
from datetime import UTC, datetime

import pytest
from instadroid import config, db, retention
from shared import sqlrows

from tests.support import fetch_row, insert_post

STAMP: str = "2026-09-14T00:00:00+00:00"


def _matches(con: sqlite3.Connection, word: str) -> set[str]:
    """The post ids whose caption has `word`, through the index."""
    cur: sqlite3.Cursor = con.execute(
        "SELECT post_id FROM posts_fts JOIN posts_fts_ids ON fts_id = posts_fts.rowid WHERE posts_fts MATCH ?",
        (word,),
    )
    return {sqlrows.must_str(r, 0) for r in sqlrows.fetch_all(cur)}


def _assert_in_step(con: sqlite3.Connection) -> None:
    """Exactly one index entry per post, holding its caption, and an index FTS5 itself finds sound."""
    con.execute("INSERT INTO posts_fts (posts_fts, rank) VALUES ('integrity-check', 1)")
    indexed: dict[str, str | None] = {
        sqlrows.must_str(r, 0): sqlrows.cell_str(r, 1)
        for r in sqlrows.fetch_all(
            con.execute(
                "SELECT post_id, posts_fts.caption FROM posts_fts_ids"
                " JOIN posts_fts ON posts_fts.rowid = fts_id"
            )
        )
    }
    stored: dict[str, str | None] = {
        sqlrows.must_str(r, 0): sqlrows.cell_str(r, 1)
        for r in sqlrows.fetch_all(con.execute("SELECT id, caption FROM posts WHERE id IS NOT NULL"))
    }
    assert indexed == stored
    assert sqlrows.scalar_int(con.execute("SELECT COUNT(*) FROM posts_fts")) == len(stored)


def _insert(con: sqlite3.Connection, post_id: str, caption: str | None, username: str = "u") -> None:
    con.execute(
        "INSERT INTO posts (id, username, caption, scraped_at) VALUES (?,?,?,?)",
        (post_id, username, caption, STAMP),
    )
    con.commit()


def test_an_insert_is_searchable_and_a_null_caption_is_harmless(con: sqlite3.Connection) -> None:
    _insert(con, "p1", "sunset over the bay")
    _insert(con, "p2", None)
    assert _matches(con, "sunset") == {"p1"}
    _assert_in_step(con)


def test_a_caption_update_replaces_the_old_words(con: sqlite3.Connection) -> None:
    _insert(con, "p1", "before words")
    con.execute("UPDATE posts SET caption = 'after words' WHERE id = 'p1'")
    assert _matches(con, "before") == set()
    assert _matches(con, "after") == {"p1"}
    _assert_in_step(con)


def test_an_id_update_moves_the_entry(con: sqlite3.Connection) -> None:
    _insert(con, "hash", "same words")
    con.execute("UPDATE posts SET id = 'code' WHERE id = 'hash'")
    assert _matches(con, "words") == {"code"}
    _assert_in_step(con)


def test_writes_that_leave_the_caption_alone_leave_the_index_alone(con: sqlite3.Connection) -> None:
    _insert(con, "p1", "kept words", username="old")
    assert db.rename_account(con, "old", "new") == 1
    db.remember_hash(con, "p1", "h")
    assert _matches(con, "kept") == {"p1"}
    _assert_in_step(con)


def test_a_merge_under_the_same_id_replaces_the_row_without_a_stale_entry(con: sqlite3.Connection) -> None:
    """write_merged() uses INSERT OR REPLACE, which fires no delete trigger and changes the rowid."""
    _insert(con, "p1", "...")
    existing: sqlite3.Row = fetch_row(con.execute("SELECT * FROM posts WHERE id = 'p1'"))
    new: db.StoredPost = {**db.stored_post(existing), "caption": "the real caption"}
    row: db.StoredPost
    row, _ = db.merged_fields(existing, new, datetime.now(UTC))
    db.write_merged(con, "p1", {**row, "caption": "the real caption"})
    assert _matches(con, "real") == {"p1"}
    assert _matches(con, '"..."') == set()
    _assert_in_step(con)


def test_a_merge_onto_a_new_id_drops_the_old_entry(con: sqlite3.Connection) -> None:
    _insert(con, "hash", "merged words")
    existing: sqlite3.Row = fetch_row(con.execute("SELECT * FROM posts WHERE id = 'hash'"))
    db.write_merged(con, "hash", {**db.stored_post(existing), "id": "code"})
    assert _matches(con, "merged") == {"code"}
    _assert_in_step(con)


def test_retention_removes_entries(con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "RETAIN_DAYS", 30)
    monkeypatch.setattr(config, "MEDIA_MAX_MB", 0)
    insert_post(con, "old", days_old=90)
    insert_post(con, "new", days_old=1)
    retention.prune_old_posts(con)
    assert _matches(con, "cap") == {"new"}
    _assert_in_step(con)


def test_the_size_cap_removes_entries(con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "RETAIN_DAYS", 0)
    monkeypatch.setattr(config, "MEDIA_MAX_MB", 0.0001)
    insert_post(con, "a", days_old=2, media_file="a.webp")
    retention.prune_old_posts(con)
    assert _matches(con, "cap") == set()
    _assert_in_step(con)


def test_vacuum_changing_rowids_does_not_detach_the_index(con: sqlite3.Connection) -> None:
    _insert(con, "p1", "first words")
    _insert(con, "p2", "second words")
    con.execute("DELETE FROM posts WHERE id = 'p1'")
    con.commit()
    con.execute("VACUUM")
    assert _matches(con, "second") == {"p2"}
    con.execute("DELETE FROM posts WHERE id = 'p2'")
    assert _matches(con, "second") == set()
    _assert_in_step(con)


def test_the_migration_backfills_a_database_without_an_index(con: sqlite3.Connection) -> None:
    con.executescript(
        """DROP TRIGGER posts_fts_insert; DROP TRIGGER posts_fts_update; DROP TRIGGER posts_fts_delete;
        DROP TABLE posts_fts; DROP TABLE posts_fts_ids;"""
    )
    _insert(con, "p1", "stored before the index")
    _insert(con, "p2", None)
    con.execute("INSERT INTO posts (id, username, scraped_at) VALUES (NULL, 'u', ?)", (STAMP,))
    con.execute(f"PRAGMA user_version = {db.MIGRATIONS.index(db._migrate_search_index)}")
    con.commit()
    con.close()
    con = db.db_init()
    assert sqlrows.scalar_int(con.execute("PRAGMA user_version")) == len(db.MIGRATIONS)
    assert _matches(con, "index") == {"p1"}
    _assert_in_step(con)
    _insert(con, "p3", "stored after the index")
    assert _matches(con, "after") == {"p3"}


def test_a_rerun_of_the_migration_starts_over(con: sqlite3.Connection) -> None:
    """What a crash before the version bump retries."""
    _insert(con, "p1", "only once")
    db._migrate_search_index(con)
    con.commit()
    assert _matches(con, "once") == {"p1"}
    _assert_in_step(con)
