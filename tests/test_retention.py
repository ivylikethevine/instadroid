"""Retention: pruning old posts, expired stories, orphaned media, and the media size cap."""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from instadroid import config, retention

from tests.support import insert_post, sql_column


def test_prune_old_posts_deletes_rows_and_media_past_retain_days(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    media = config.MEDIA_DIR
    monkeypatch.setattr(config, "RETAIN_DAYS", 30)
    insert_post(con, media, "old", days_old=45, media_file="old.jpg")
    insert_post(con, media, "new", days_old=1, media_file="new.jpg")

    retention.prune_old_posts(con)

    ids = set(sql_column(con.execute("SELECT id FROM posts")))
    assert ids == {"new"}
    assert not (media / "old.jpg").exists()
    assert (media / "new.jpg").exists()


def test_prune_old_posts_disabled_when_retain_days_is_zero(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    media = config.MEDIA_DIR
    monkeypatch.setattr(config, "RETAIN_DAYS", 0)
    insert_post(con, media, "ancient", days_old=9999, media_file="ancient.jpg")

    retention.prune_old_posts(con)

    assert con.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 1
    assert (media / "ancient.jpg").exists()


def test_prune_old_posts_removes_orphaned_media_regardless_of_retain_days(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    media = config.MEDIA_DIR
    monkeypatch.setattr(config, "RETAIN_DAYS", 0)
    insert_post(con, media, "kept", days_old=1, media_file="kept.jpg")
    (media / "orphan.jpg").write_bytes(b"x")  # e.g. left behind by an interrupted run

    retention.prune_old_posts(con)

    assert {f.name for f in media.iterdir()} == {"kept.jpg"}


def test_orphan_sweep_covers_both_media_formats(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    media = config.MEDIA_DIR
    monkeypatch.setattr(config, "RETAIN_DAYS", 0)
    insert_post(con, media, "old", days_old=1, media_file="old.jpg")
    insert_post(con, media, "new", days_old=1, media_file="new.webp")
    (media / "orphan.jpg").write_bytes(b"x")
    (media / "orphan.webp").write_bytes(b"x")
    (media / "notes.txt").write_bytes(b"x")  # not media: never touched

    retention.prune_old_posts(con)

    assert {f.name for f in media.iterdir()} == {"old.jpg", "new.webp", "notes.txt"}


def _insert_story(
    con: sqlite3.Connection,
    media_dir: Path,
    story_id: str,
    days_old: float,
    username: str = "u",
    media_file: str | None = None,
) -> None:
    scraped_at = (datetime.now(UTC) - timedelta(days=days_old)).isoformat()
    if media_file:
        (media_dir / "stories").mkdir(parents=True, exist_ok=True)
        (media_dir / media_file).write_bytes(b"x")
    con.execute(
        "INSERT INTO stories (id, username, media_file, kind, posted_date, scraped_at) VALUES (?,?,?,?,?,?)",
        (story_id, username, media_file, "story", "1h", scraped_at),
    )
    con.commit()


def test_prune_expired_stories_deletes_rows_and_media_past_retain_days(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    media = config.MEDIA_DIR
    monkeypatch.setattr(config, "RETAIN_DAYS", 30)
    _insert_story(con, media, "old", days_old=45, media_file="stories/old.jpg")
    _insert_story(con, media, "fresh", days_old=1, media_file="stories/fresh.jpg")

    retention.prune_expired_stories(con)

    ids = set(sql_column(con.execute("SELECT id FROM stories")))
    assert ids == {"fresh"}
    assert not (media / "stories" / "old.jpg").exists()
    assert (media / "stories" / "fresh.jpg").exists()


def test_prune_expired_stories_noop_when_none_past_retain_days(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    media = config.MEDIA_DIR
    monkeypatch.setattr(config, "RETAIN_DAYS", 30)
    _insert_story(con, media, "fresh", days_old=1, media_file="stories/fresh.jpg")

    retention.prune_expired_stories(con)

    assert con.execute("SELECT COUNT(*) FROM stories").fetchone()[0] == 1


def test_prune_expired_stories_disabled_when_retain_days_is_zero(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    media = config.MEDIA_DIR
    monkeypatch.setattr(config, "RETAIN_DAYS", 0)
    _insert_story(con, media, "ancient", days_old=9999, media_file="ancient.jpg")

    retention.prune_expired_stories(con)

    assert con.execute("SELECT COUNT(*) FROM stories").fetchone()[0] == 1
    assert (media / "ancient.jpg").exists()


def test_prune_old_posts_also_removes_extra_carousel_media(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    media = config.MEDIA_DIR
    monkeypatch.setattr(config, "RETAIN_DAYS", 30)
    insert_post(con, media, "old", days_old=45, media_file="old.jpg")
    (media / "old_1.jpg").write_bytes(b"x")
    con.execute("INSERT INTO media (post_id, idx, file) VALUES ('old', 1, 'old_1.jpg')")
    con.commit()

    retention.prune_old_posts(con)

    assert con.execute("SELECT COUNT(*) FROM posts WHERE id='old'").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM media WHERE post_id='old'").fetchone()[0] == 0
    assert not (media / "old.jpg").exists()
    assert not (media / "old_1.jpg").exists()


def test_prune_old_posts_leaves_avatars_alone(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The orphan sweep globs MEDIA_DIR non-recursively; avatars/ must be structurally immune.
    media = config.MEDIA_DIR
    monkeypatch.setattr(config, "RETAIN_DAYS", 0)
    avatars = media / "avatars"
    avatars.mkdir()
    (avatars / "someone.jpg").write_bytes(b"x")

    retention.prune_old_posts(con)

    assert (avatars / "someone.jpg").exists()


def test_size_cap_disabled_when_media_max_mb_is_zero(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    media = config.MEDIA_DIR
    monkeypatch.setattr(config, "MEDIA_MAX_MB", 0)
    insert_post(con, media, "a", days_old=1, media_file="a.jpg")
    (media / "a.jpg").write_bytes(b"x" * 500_000)

    retention.prune_old_posts(con)

    assert con.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 1


def test_size_cap_removes_oldest_posts_first_when_over_budget(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    media = config.MEDIA_DIR
    insert_post(con, media, "older", days_old=5, media_file="older.jpg")
    insert_post(con, media, "newer", days_old=1, media_file="newer.jpg")
    (media / "older.jpg").write_bytes(b"x" * 500_000)
    (media / "newer.jpg").write_bytes(b"x" * 10_000)

    baseline = retention._media_and_db_size_mb(con)
    monkeypatch.setattr(config, "MEDIA_MAX_MB", baseline - 0.3)  # reachable only by dropping "older"

    retention.prune_old_posts(con)

    ids = set(sql_column(con.execute("SELECT id FROM posts")))
    assert ids == {"newer"}
    assert not (media / "older.jpg").exists()
    assert (media / "newer.jpg").exists()


def test_size_cap_stops_when_no_posts_remain(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    media = config.MEDIA_DIR
    monkeypatch.setattr(config, "MEDIA_MAX_MB", 0.0000001)  # unreachable even with zero posts
    insert_post(con, media, "only", days_old=1, media_file="only.jpg")

    retention.prune_old_posts(con)  # must terminate rather than spin

    assert con.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 0
