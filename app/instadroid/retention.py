"""Deleting old posts, stories and orphaned media, and the optional size cap."""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from shared import sqlrows

from . import config
from .common import log


def _post_files(con: sqlite3.Connection, post_id: str) -> set[str]:
    """Every media file of one post: its cover and extra slides. Reads media_file straight off the posts
    row rather than only the media table, since a row can predate carousel capture (or be inserted
    directly, as tests do) with no media rows."""
    row: sqlite3.Row | None = sqlrows.fetch_one(
        con.execute("SELECT media_file FROM posts WHERE id=?", (post_id,))
    )
    media_file: str | None = sqlrows.cell_str(row, "media_file") if row else None
    files: set[str] = {media_file} if media_file else set[str]()
    rows: list[sqlite3.Row] = sqlrows.fetch_all(
        con.execute("SELECT file FROM media WHERE post_id=?", (post_id,))
    )
    f: str | None
    return files | {f for r in rows if (f := sqlrows.cell_str(r, 0))}


def _delete_posts(con: sqlite3.Connection, post_ids: list[str], files: set[str]) -> None:
    """Delete post rows and their extra-slide media rows in one transaction, then unlink `files`
    (_post_files() of those posts)."""
    con.executemany("DELETE FROM posts WHERE id=?", [(pid,) for pid in post_ids])
    con.executemany("DELETE FROM media WHERE post_id=?", [(pid,) for pid in post_ids])
    con.commit()
    discard_media(*files)


def discard_media(*files: str | None) -> None:
    """Unlink media files (relative to MEDIA_DIR), skipping None/empty names and missing files."""
    fn: str | None
    for fn in files:
        if fn:
            (config.MEDIA_DIR / fn).unlink(missing_ok=True)


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _media_and_db_size_mb(con: sqlite3.Connection) -> float:
    """Live disk footprint: the media tree plus the database's *live* size. A raw file-size stat on
    DB_PATH would overcount once rows have been deleted — SQLite returns freed pages to an internal
    freelist rather than shrinking the file — which would make the size cap below keep deleting
    posts chasing a floor the file can never reach."""
    page_count: int = sqlrows.scalar_int(con.execute("PRAGMA page_count")) or 0
    freelist: int = sqlrows.scalar_int(con.execute("PRAGMA freelist_count")) or 0
    page_size: int = sqlrows.scalar_int(con.execute("PRAGMA page_size")) or 0
    total: int = (page_count - freelist) * page_size
    if config.MEDIA_DIR.exists():
        total += sum(f.stat().st_size for f in config.MEDIA_DIR.rglob("*") if f.is_file())
    return total / (1024 * 1024)


def _enforce_size_cap(con: sqlite3.Connection) -> None:
    """Delete the oldest posts until total size is back under MEDIA_MAX_MB or there's nothing left to
    delete. 0 disables. Runs after age-based pruning and the orphan sweep, so it only ever has to make
    up the difference. The size is measured once, then each chosen post's files come off it; the few
    database pages its row frees aren't counted, which can at most cost one extra post."""
    if config.MEDIA_MAX_MB <= 0:
        return
    size_mb: float = _media_and_db_size_mb(con)
    if size_mb <= config.MEDIA_MAX_MB:
        return
    removed: list[str] = []
    files: set[str] = set()
    oldest: sqlite3.Cursor = con.execute("SELECT id FROM posts ORDER BY COALESCE(posted_at, scraped_at) ASC")
    while size_mb > config.MEDIA_MAX_MB:
        row: sqlite3.Row | None = sqlrows.fetch_one(oldest)
        if not row:
            log(f"retention: still over MEDIA_MAX_MB={config.MEDIA_MAX_MB} with no posts left to remove")
            break
        post_id: str = sqlrows.must_str(row, "id")
        post_files: set[str] = (
            _post_files(con, post_id) - files
        )  # a file two posts share only frees space once
        size_mb -= sum(_file_size(config.MEDIA_DIR / f) for f in post_files) / (1024 * 1024)
        removed.append(post_id)
        files |= post_files
    oldest.close()
    _delete_posts(con, removed, files)
    if removed:
        log(f"retention: removed {len(removed)} additional post(s) to stay under {config.MEDIA_MAX_MB}MB")


def prune_expired_stories(con: sqlite3.Connection) -> None:
    """Stories share RETAIN_DAYS with posts (0 disables deletion) rather than expiring on their own
    schedule — once captured, a story is kept exactly as long as everything else."""
    if config.RETAIN_DAYS <= 0:
        return
    cutoff: str = (datetime.now(UTC) - timedelta(days=config.RETAIN_DAYS)).isoformat()
    gone: list[sqlite3.Row] = sqlrows.fetch_all(
        con.execute("SELECT media_file FROM stories WHERE scraped_at < ?", (cutoff,))
    )
    cur: sqlite3.Cursor = con.execute("DELETE FROM stories WHERE scraped_at < ?", (cutoff,))
    con.commit()
    discard_media(*(sqlrows.cell_str(r, 0) for r in gone))
    if cur.rowcount:
        log(f"retention: removed {cur.rowcount} expired stor{'y' if cur.rowcount == 1 else 'ies'}")


def prune_old_posts(con: sqlite3.Connection) -> None:
    """Delete posts older than RETAIN_DAYS (0 disables), any media file no row references any
    more, and (if MEDIA_MAX_MB is set) additional oldest posts until total size is back under the
    cap — in that order, so disk use stays flat instead of growing forever."""
    if config.RETAIN_DAYS > 0:
        cutoff: str = (datetime.now(UTC) - timedelta(days=config.RETAIN_DAYS)).isoformat()
        old_ids: list[str] = [
            sqlrows.must_str(r, 0)
            for r in sqlrows.fetch_all(
                con.execute("SELECT id FROM posts WHERE COALESCE(posted_at, scraped_at) < ?", (cutoff,))
            )
        ]
        _delete_posts(con, old_ids, set[str]().union(*(_post_files(con, pid) for pid in old_ids)))
        if old_ids:
            log(f"retention: removed {len(old_ids)} post(s) older than {config.RETAIN_DAYS}d")
    if config.MEDIA_DIR.exists():
        kept: set[str | None] = {
            sqlrows.cell_str(r, 0)
            for r in sqlrows.fetch_all(
                con.execute("SELECT media_file FROM posts WHERE media_file IS NOT NULL")
            )
        }
        kept |= {sqlrows.cell_str(r, 0) for r in sqlrows.fetch_all(con.execute("SELECT file FROM media"))}
        # Only ever written media_file names are *.jpg/*.webp (capture.crop_media()), and these globs are
        # non-recursive, so pointing MEDIA_DIR at the wrong directory can't delete unrelated files
        # and avatars/ (its own subdirectory) is never touched by this sweep.
        orphans: list[Path] = [
            f for ext in config.MEDIA_EXTS for f in config.MEDIA_DIR.glob(f"*{ext}") if f.name not in kept
        ]
        f: Path
        for f in orphans:
            f.unlink(missing_ok=True)
        if orphans:
            log(f"retention: removed {len(orphans)} orphaned media file(s)")
    _enforce_size_cap(con)
