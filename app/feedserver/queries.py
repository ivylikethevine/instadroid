"""Read-only access to the scraper's database: every query the feeds and the status page run, and the
row accessors they read results with."""

import sqlite3
from collections.abc import Callable, Sequence
from contextlib import closing
from pathlib import Path

from shared import sqlrows
from shared.sqlrows import SqlValue

from . import settings

type EtagPart = SqlValue | tuple[SqlValue, ...]  # what an ETag is computed over


def _query[T](sql: str, args: Sequence[SqlValue], fetch: Callable[[sqlite3.Cursor], T], default: T) -> T:
    """Run a read-only query and `fetch` from its cursor, or `default` when the database or table
    doesn't exist yet (the scraper creates both on its first start). Read-only so the scraper's writer
    lock never blocks a request."""
    if not Path(settings.DB_PATH).exists():
        return default
    with closing(sqlite3.connect(f"file:{settings.DB_PATH}?mode=ro", uri=True)) as con:
        con.row_factory = sqlite3.Row
        try:
            return fetch(con.execute(sql, args))
        except sqlite3.OperationalError:
            return default


def all_rows(sql: str, args: Sequence[SqlValue] = ()) -> list[sqlite3.Row]:
    """Every row of a read-only query, or none before the database or table exists."""
    return _query(sql, args, sqlrows.fetch_all, [])


def one_row(sql: str, args: Sequence[SqlValue] = ()) -> sqlite3.Row | None:
    """The first row of a read-only query, or None (also before the database or table exists)."""
    return _query(sql, args, sqlrows.fetch_one, None)


# --- reading rows ---------------------------------------------------------------------------------


def values(row: sqlite3.Row) -> tuple[SqlValue, ...]:
    """Every value of a row, in column order (what tuple(row) gives)."""
    return tuple(sqlrows.cell(row, i) for i in range(len(row)))


def string(row: sqlite3.Row, key: str | int) -> str:
    """row[key] as it reads in an f-string (so NULL is "None"), for a NOT NULL column."""
    return str(sqlrows.cell(row, key))


def text(row: sqlite3.Row, key: str | int) -> str | None:
    """row[key] as text, or None for NULL."""
    value = sqlrows.cell(row, key)
    return value if value is None or isinstance(value, str) else str(value)


def integer(row: sqlite3.Row, key: str | int) -> int | None:
    """An INTEGER column (or COUNT) of a row, or None for NULL."""
    return sqlrows.cell_int(row, key)


def col_text(row: sqlite3.Row, name: str) -> str | None:
    """A text column, or None when it's NULL or the row predates that column (sqlite3.Row has no get)."""
    return text(row, name) if sqlrows.has_column(row, name) else None


def col_int(row: sqlite3.Row, name: str) -> int | None:
    """An integer column, or None when it's NULL or the row predates that column."""
    return integer(row, name) if sqlrows.has_column(row, name) else None


def limit(requested: int) -> int:
    return max(1, min(requested, settings.MAX_LIMIT))


# --- posts and stories ----------------------------------------------------------------------------


def posts(user: str | None, requested: int) -> list[sqlite3.Row]:
    where = " WHERE username = ?" if user else ""
    return all_rows(
        f"SELECT * FROM posts{where} ORDER BY COALESCE(posted_at, scraped_at) DESC LIMIT ?",
        (*([user] if user else []), limit(requested)),
    )


def extra_slides(post_ids: list[str]) -> dict[str, list[str]]:
    """{post_id: [extra slide filenames, in order]} for the given posts."""
    out: dict[str, list[str]] = {}
    if post_ids:
        placeholders = ",".join("?" * len(post_ids))
        sql = f"SELECT post_id, file FROM media WHERE post_id IN ({placeholders}) ORDER BY post_id, idx"
        for row in all_rows(sql, post_ids):
            out.setdefault(string(row, 0), []).append(string(row, 1))
    return out


def avatar_files() -> dict[str, str]:
    """{username: avatar_file} for every account with a captured avatar."""
    sql = "SELECT username, avatar_file FROM accounts WHERE avatar_file IS NOT NULL"
    return {string(row, 0): string(row, 1) for row in all_rows(sql)}


def feed_signal(user: str | None) -> tuple[EtagPart, ...]:
    """Cheap ETag input for /instagram.xml: post count and latest change (updated_at also moves when
    a row is merged in place), extra-slide count and latest avatar refresh, scoped to `user` when
    given so one account's new post doesn't invalidate every other per-account feed."""
    where, args = (" WHERE username = ?", (user,)) if user else ("", ())
    counts = one_row(
        f"SELECT COUNT(*), COALESCE(MAX(COALESCE(updated_at, scraped_at)), '') FROM posts{where}", args
    )
    media = one_row("SELECT COUNT(*) FROM media")
    avatar = one_row(f"SELECT COALESCE(MAX(avatar_updated_at), '') FROM accounts{where}", args)
    alerts = tuple(values(a) for a in open_alerts()) if not user else ()
    return (
        *(values(counts) if counts else (0, "")),
        sqlrows.cell(media, 0) if media else 0,
        sqlrows.cell(avatar, 0) if avatar else "",
        *alerts,
    )


def open_alerts() -> list[sqlite3.Row]:
    """The scraper's open failure alerts (instadroid/alerts.py), oldest first; none before that table exists."""
    return all_rows("SELECT kind, message, raised_at FROM alerts ORDER BY raised_at")


def post_count() -> int:
    row = one_row("SELECT COUNT(*) FROM posts")
    return (integer(row, 0) or 0) if row else 0


def stories(requested: int) -> list[sqlite3.Row]:
    """Stored stories, newest first (they share RETAIN_DAYS with posts; the scraper prunes them)."""
    return all_rows("SELECT * FROM stories ORDER BY scraped_at DESC LIMIT ?", (limit(requested),))


def stories_stats() -> tuple[int, str]:
    """(count, latest scraped_at) - the ETag input for /stories.xml."""
    row = one_row("SELECT COUNT(*), COALESCE(MAX(scraped_at), '') FROM stories")
    return (integer(row, 0) or 0, string(row, 1)) if row else (0, "")


def usernames() -> list[str]:
    return [string(r, 0) for r in all_rows("SELECT DISTINCT username FROM posts ORDER BY username")]


# --- runs and accounts ----------------------------------------------------------------------------


def user_counts() -> list[sqlite3.Row]:
    return all_rows(
        "SELECT username, COUNT(*) AS n, MAX(COALESCE(posted_at, scraped_at)) AS latest"
        " FROM posts GROUP BY username ORDER BY username"
    )


def recent_runs(count: int = 10) -> list[sqlite3.Row]:
    return all_rows("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (count,))


def latest_device() -> sqlite3.Row | None:
    """Device props from the most recent run that got far enough to read them (a run that failed
    before connect_device() leaves these NULL). SELECT * so an old runs table still works."""
    return one_row("SELECT * FROM runs WHERE android_release IS NOT NULL ORDER BY id DESC LIMIT 1")


def last_finished() -> str | None:
    """When the latest run finished, or None before any has."""
    row = one_row("SELECT MAX(finished_at) FROM runs")
    return text(row, 0) if row else None


def run_times() -> tuple[str | None, str | None, str | None]:
    """(latest finished_at, latest successful finished_at, first started_at) over every run."""
    row = one_row(
        "SELECT MAX(finished_at), MAX(CASE WHEN error IS NULL THEN finished_at END), MIN(started_at) FROM runs"
    )
    return (text(row, 0), text(row, 1), text(row, 2)) if row else (None, None, None)
