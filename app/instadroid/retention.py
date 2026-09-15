"""Deleting old posts, stories and orphaned media, and the optional size cap."""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from shared import sqlrows

from . import config
from .common import log


def _delete_post(con: sqlite3.Connection, post_id: str) -> None:
    """Delete one post row, its extra-slide media rows, and unlink every file involved (cover +
    slides). Reads media_file straight off the posts row rather than only the media table, since a
    row can predate carousel capture (or be inserted directly, as tests do) with no media rows."""
    row = sqlrows.fetch_one(con.execute("SELECT media_file FROM posts WHERE id=?", (post_id,)))
    media_file = sqlrows.cell_str(row, "media_file") if row else None
    files: set[str | None] = {media_file} if media_file else set()
    files |= {
        sqlrows.cell_str(r, 0)
        for r in sqlrows.fetch_all(con.execute("SELECT file FROM media WHERE post_id=?", (post_id,)))
    }
    con.execute("DELETE FROM posts WHERE id=?", (post_id,))
    con.execute("DELETE FROM media WHERE post_id=?", (post_id,))
    con.commit()
    discard_media(*files)


def discard_media(*files: str | None) -> None:
    """Unlink media files (relative to MEDIA_DIR), skipping None/empty names and missing files."""
    for fn in files:
        if fn:
            (config.MEDIA_DIR / fn).unlink(missing_ok=True)


def _media_and_db_size_mb() -> float:
    """Live disk footprint: the media tree plus the database's *live* size. A raw file-size stat on
    DB_PATH would overcount once rows have been deleted — SQLite returns freed pages to an internal
    freelist rather than shrinking the file — which would make the size cap below keep deleting
    posts chasing a floor the file can never reach."""
    total = 0
    if Path(config.DB_PATH).exists():
        con = sqlite3.connect(config.DB_PATH)
        try:
            page_count = sqlrows.scalar_int(con.execute("PRAGMA page_count")) or 0
            freelist = sqlrows.scalar_int(con.execute("PRAGMA freelist_count")) or 0
            page_size = sqlrows.scalar_int(con.execute("PRAGMA page_size")) or 0
            total += (page_count - freelist) * page_size
        finally:
            con.close()
    if config.MEDIA_DIR.exists():
        total += sum(f.stat().st_size for f in config.MEDIA_DIR.rglob("*") if f.is_file())
    return total / (1024 * 1024)


def _enforce_size_cap(con: sqlite3.Connection) -> None:
    """Delete the oldest posts, one at a time, until total size is back under MEDIA_MAX_MB or
    there's nothing left to delete. 0 disables. Runs after age-based pruning and the orphan sweep,
    so it only ever has to make up the difference."""
    if config.MEDIA_MAX_MB <= 0:
        return
    removed = 0
    while _media_and_db_size_mb() > config.MEDIA_MAX_MB:
        row = sqlrows.fetch_one(
            con.execute("SELECT id FROM posts ORDER BY COALESCE(posted_at, scraped_at) ASC LIMIT 1")
        )
        if not row:
            log(f"retention: still over MEDIA_MAX_MB={config.MEDIA_MAX_MB} with no posts left to remove")
            break
        _delete_post(con, sqlrows.must_str(row, "id"))
        removed += 1
    if removed:
        log(f"retention: removed {removed} additional post(s) to stay under {config.MEDIA_MAX_MB}MB")


def prune_expired_stories(con: sqlite3.Connection) -> None:
    """Stories share RETAIN_DAYS with posts (0 disables deletion) rather than expiring on their own
    schedule — once captured, a story is kept exactly as long as everything else."""
    if config.RETAIN_DAYS <= 0:
        return
    cutoff = (datetime.now(UTC) - timedelta(days=config.RETAIN_DAYS)).isoformat()
    gone = sqlrows.fetch_all(con.execute("SELECT media_file FROM stories WHERE scraped_at < ?", (cutoff,)))
    cur = con.execute("DELETE FROM stories WHERE scraped_at < ?", (cutoff,))
    con.commit()
    discard_media(*(sqlrows.cell_str(r, 0) for r in gone))
    if cur.rowcount:
        log(f"retention: removed {cur.rowcount} expired stor{'y' if cur.rowcount == 1 else 'ies'}")


def prune_old_posts(con: sqlite3.Connection) -> None:
    """Delete posts older than RETAIN_DAYS (0 disables), any media file no row references any
    more, and (if MEDIA_MAX_MB is set) additional oldest posts until total size is back under the
    cap — in that order, so disk use stays flat instead of growing forever."""
    if config.RETAIN_DAYS > 0:
        cutoff = (datetime.now(UTC) - timedelta(days=config.RETAIN_DAYS)).isoformat()
        old_ids = [
            sqlrows.must_str(r, 0)
            for r in sqlrows.fetch_all(
                con.execute("SELECT id FROM posts WHERE COALESCE(posted_at, scraped_at) < ?", (cutoff,))
            )
        ]
        for pid in old_ids:
            _delete_post(con, pid)
        if old_ids:
            log(f"retention: removed {len(old_ids)} post(s) older than {config.RETAIN_DAYS}d")
    if config.MEDIA_DIR.exists():
        kept = {
            sqlrows.cell_str(r, 0)
            for r in sqlrows.fetch_all(
                con.execute("SELECT media_file FROM posts WHERE media_file IS NOT NULL")
            )
        }
        kept |= {sqlrows.cell_str(r, 0) for r in sqlrows.fetch_all(con.execute("SELECT file FROM media"))}
        # Only ever written media_file names are *.jpg/*.webp (capture.crop_media()), and these globs are
        # non-recursive, so pointing MEDIA_DIR at the wrong directory can't delete unrelated files
        # and avatars/ (its own subdirectory) is never touched by this sweep.
        orphans = [
            f for ext in config.MEDIA_EXTS for f in config.MEDIA_DIR.glob(f"*{ext}") if f.name not in kept
        ]
        for f in orphans:
            f.unlink(missing_ok=True)
        if orphans:
            log(f"retention: removed {len(orphans)} orphaned media file(s)")
    _enforce_size_cap(con)
