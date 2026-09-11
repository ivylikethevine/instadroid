import hashlib
import sqlite3

import pytest
import scraper


def sha1_16(key: str) -> str:
    return hashlib.sha1(key.encode(), usedforsecurity=False).hexdigest()[:16]


def card(username, caption="", alt=""):
    return {"username": username, "caption": caption, "alt": alt}


def test_post_id_is_a_truncated_sha256_of_author_and_caption():
    assert scraper.post_id(card("u", "cap")) == hashlib.sha256(b"u|cap").hexdigest()[:16]
    # No caption yet: the media description up to its first comma, digits stripped.
    alt = "Photo 1 of 2 by U, 113 likes"
    assert scraper.post_id(card("u", alt=alt)) == hashlib.sha256(b"u|Photo  of  by U").hexdigest()[:16]


@pytest.fixture
def legacy_db(tmp_path, monkeypatch):
    """A database as the SHA-1 build left it: user_version 2, hashes from the old post_id()."""
    monkeypatch.setattr(scraper, "DB_PATH", str(tmp_path / "posts.sqlite"))
    monkeypatch.setattr(scraper, "MEDIA_DIR", tmp_path / "media")
    con = scraper.db_init()
    alt = "Photo 1 of 2 by Other, 113 likes, 10 comments"
    alt_id = sha1_16("other|Photo  of  by Other")
    rows = [
        # (id, username, caption, hash)
        ("ABC", "someone", "Real caption", sha1_16("someone|Real caption")),  # permalink post
        (alt_id, "other", alt, alt_id),  # no permalink, stored before its caption rendered
        ("EDIT", "editor", "Caption as first stored", sha1_16("editor|Caption after an edit")),
        (sha1_16("oldie|From before the hash column"), "oldie", "From before the hash column", None),
    ]
    for pid, user, caption, h in rows:
        con.execute(
            "INSERT INTO posts (id, username, kind, caption, scraped_at, hash) VALUES (?,?,?,?,?,?)",
            (pid, user, "photo", caption, "2026-09-08T00:00:00+00:00", h),
        )
    con.execute("PRAGMA user_version = 2")
    con.commit()
    con.close()
    return tmp_path / "posts.sqlite", alt_id


def _hashes(db):
    return dict(sqlite3.connect(db).execute("SELECT id, hash FROM posts"))


def test_upgrade_rekeys_every_stored_hash_to_what_post_id_now_computes(legacy_db):
    db, alt_id = legacy_db
    con = scraper.db_init()

    hashes = _hashes(db)
    assert hashes["ABC"] == scraper.post_id(card("someone", "Real caption"))
    assert hashes[alt_id] == scraper.post_id(
        card("other", alt="Photo 1 of 2 by Other, 113 likes, 10 comments")
    )
    assert hashes[sha1_16("oldie|From before the hash column")] == scraper.post_id(
        card("oldie", "From before the hash column")
    )
    # Unverifiable (caption edited since): best guess from what's stored.
    assert hashes["EDIT"] == scraper.post_id(card("editor", "Caption as first stored"))
    assert con.execute("PRAGMA user_version").fetchone()[0] == 3


def test_upgrade_keeps_ids_so_feed_entries_and_media_filenames_are_unchanged(legacy_db):
    db, alt_id = legacy_db
    before = set(_hashes(db))
    scraper.db_init()
    assert set(_hashes(db)) == before
    assert alt_id in before  # the hash-id post keeps its SHA-1-era id


def test_upgraded_row_is_recognised_by_the_scrape_loops_lookup(legacy_db):
    scraper.db_init()
    con = sqlite3.connect(legacy_db[0])
    h = scraper.post_id(card("someone", "Real caption"))
    assert con.execute("SELECT 1 FROM posts WHERE hash=? OR id=?", (h, h)).fetchone()


def test_upgrade_runs_once(legacy_db):
    db, _ = legacy_db
    scraper.db_init()
    con = sqlite3.connect(db)
    con.execute("UPDATE posts SET hash='sentinel' WHERE id='ABC'")
    con.commit()
    scraper.db_init()
    assert _hashes(db)["ABC"] == "sentinel"
