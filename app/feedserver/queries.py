"""Read-only access to the scraper's database: every query the feeds and the status page run, and the
row accessors they read results with. A route opens one connection() and passes it to each query."""

import sqlite3
from collections.abc import Callable, Generator, Sequence
from contextlib import closing, contextmanager
from pathlib import Path

from shared import sqlrows
from shared.sqlrows import SqlValue

from . import settings

type EtagPart = SqlValue | tuple[SqlValue, ...]  # what an ETag is computed over


@contextmanager
def connection() -> Generator[sqlite3.Connection]:
    """One read-only connection to the database for a request's queries, closed afterwards. Read-only so
    the scraper's writer lock never blocks a request. Before the scraper has created the database, an
    empty in-memory one instead, where every query finds no table and reads as its default."""
    exists: bool = Path(settings.DB_PATH).exists()
    target: str
    uri: bool
    con: sqlite3.Connection
    target, uri = (f"file:{settings.DB_PATH}?mode=ro", True) if exists else (":memory:", False)
    with closing(sqlite3.connect(target, uri=uri)) as con:
        yield con


def _query[T](
    con: sqlite3.Connection,
    sql: str,
    args: Sequence[SqlValue],
    fetch: Callable[[sqlite3.Cursor], T],
    default: T,
) -> T:
    """Run a query and `fetch` from its cursor, or `default` when the table doesn't exist yet (the
    scraper creates it on its first start)."""
    try:
        return fetch(con.execute(sql, args))
    except sqlite3.OperationalError:
        return default


def all_rows(con: sqlite3.Connection, sql: str, args: Sequence[SqlValue] = ()) -> list[sqlite3.Row]:
    """Every row of a query, or none before the database or table exists."""
    return _query(con, sql, args, sqlrows.fetch_all, [])


def one_row(con: sqlite3.Connection, sql: str, args: Sequence[SqlValue] = ()) -> sqlite3.Row | None:
    """The first row of a query, or None (also before the database or table exists)."""
    return _query(con, sql, args, sqlrows.fetch_one, None)


# --- reading rows ---------------------------------------------------------------------------------


def string(row: sqlite3.Row, key: str | int) -> str:
    """row[key] as it reads in an f-string (so NULL is "None"), for a NOT NULL column."""
    return str(sqlrows.cell(row, key))


def text(row: sqlite3.Row, key: str | int) -> str | None:
    """row[key] as text, or None for NULL."""
    value: SqlValue = sqlrows.cell(row, key)
    return value if value is None or isinstance(value, str) else str(value)


def limit(requested: int) -> int:
    return max(1, min(requested, settings.MAX_LIMIT))


# --- posts and stories ----------------------------------------------------------------------------


def posts(con: sqlite3.Connection, user: str | None, requested: int) -> list[sqlite3.Row]:
    where: str = " WHERE username = ?" if user else ""
    return all_rows(
        con,
        f"SELECT * FROM posts{where} ORDER BY COALESCE(posted_at, scraped_at) DESC LIMIT ?",
        (*([user] if user else []), limit(requested)),
    )


def extra_slides(con: sqlite3.Connection, post_ids: list[str]) -> dict[str, list[str]]:
    """{post_id: [extra slide filenames, in order]} for the given posts."""
    out: dict[str, list[str]] = {}
    if post_ids:
        placeholders: str = ",".join("?" * len(post_ids))
        sql: str = f"SELECT post_id, file FROM media WHERE post_id IN ({placeholders}) ORDER BY post_id, idx"
        row: sqlite3.Row
        for row in all_rows(con, sql, post_ids):
            out.setdefault(string(row, 0), []).append(string(row, 1))
    return out


def avatar_files(con: sqlite3.Connection) -> dict[str, str]:
    """{username: avatar_file} for every account with a captured avatar."""
    sql: str = "SELECT username, avatar_file FROM accounts WHERE avatar_file IS NOT NULL"
    return {string(row, 0): string(row, 1) for row in all_rows(con, sql)}


def feed_signal(con: sqlite3.Connection, user: str | None, alerts: list[sqlite3.Row]) -> tuple[EtagPart, ...]:
    """Cheap ETag input for /instagram.xml: post count and latest change (updated_at also moves when
    a row is merged in place), extra-slide count, latest avatar refresh and the `alerts` the feed
    shows, scoped to `user` when given so one account's new post doesn't invalidate every other
    per-account feed."""
    where: str
    args: tuple[str, ...]
    where, args = (" WHERE username = ?", (user,)) if user else ("", ())
    counts: sqlite3.Row | None = one_row(
        con, f"SELECT COUNT(*), COALESCE(MAX(COALESCE(updated_at, scraped_at)), '') FROM posts{where}", args
    )
    media: sqlite3.Row | None = one_row(con, "SELECT COUNT(*) FROM media")
    avatar: sqlite3.Row | None = one_row(
        con, f"SELECT COALESCE(MAX(avatar_updated_at), '') FROM accounts{where}", args
    )
    return (
        *(sqlrows.values(counts) if counts else (0, "")),
        sqlrows.cell(media, 0) if media else 0,
        sqlrows.cell(avatar, 0) if avatar else "",
        *(sqlrows.values(a) for a in alerts),
    )


def open_alerts(con: sqlite3.Connection) -> list[sqlite3.Row]:
    """The scraper's open failure alerts (instadroid/alerts.py), oldest first; none before that table exists."""
    return all_rows(con, "SELECT kind, message, raised_at FROM alerts ORDER BY raised_at")


def post_count(con: sqlite3.Connection) -> int:
    row: sqlite3.Row | None = one_row(con, "SELECT COUNT(*) FROM posts")
    return (sqlrows.cell_int(row, 0) or 0) if row else 0


def stories(con: sqlite3.Connection, requested: int) -> list[sqlite3.Row]:
    """Stored stories, newest first (they share RETAIN_DAYS with posts; the scraper prunes them)."""
    return all_rows(con, "SELECT * FROM stories ORDER BY scraped_at DESC LIMIT ?", (limit(requested),))


def stories_stats(con: sqlite3.Connection) -> tuple[int, str]:
    """(count, latest scraped_at) - the ETag input for /stories.xml."""
    row: sqlite3.Row | None = one_row(con, "SELECT COUNT(*), COALESCE(MAX(scraped_at), '') FROM stories")
    return (sqlrows.cell_int(row, 0) or 0, string(row, 1)) if row else (0, "")


def usernames(con: sqlite3.Connection) -> list[str]:
    return [string(r, 0) for r in all_rows(con, "SELECT DISTINCT username FROM posts ORDER BY username")]


# --- runs and accounts ----------------------------------------------------------------------------


def user_counts(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return all_rows(
        con,
        "SELECT username, COUNT(*) AS n, MAX(COALESCE(posted_at, scraped_at)) AS latest"
        " FROM posts GROUP BY username ORDER BY username",
    )


def recent_runs(con: sqlite3.Connection, count: int = 10) -> list[sqlite3.Row]:
    return all_rows(con, "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (count,))


def latest_device(con: sqlite3.Connection) -> sqlite3.Row | None:
    """Device props from the most recent run that got far enough to read them (a run that failed
    before connect_device() leaves these NULL). SELECT * so an old runs table still works."""
    return one_row(con, "SELECT * FROM runs WHERE android_release IS NOT NULL ORDER BY id DESC LIMIT 1")


# A runs row is inserted as its run finishes, and runs never overlap, so id order is time order: these
# read the rows at either end by id rather than MAX/MIN over a table that's never pruned.


def last_finished(con: sqlite3.Connection) -> str | None:
    """When the latest run finished, or None before any has."""
    row: sqlite3.Row | None = one_row(con, "SELECT finished_at FROM runs ORDER BY id DESC LIMIT 1")
    return text(row, 0) if row else None


def run_times(con: sqlite3.Connection) -> tuple[str | None, str | None, str | None]:
    """(latest finished_at, latest successful finished_at, first started_at) over every run."""
    row: sqlite3.Row | None = one_row(
        con,
        "SELECT (SELECT finished_at FROM runs ORDER BY id DESC LIMIT 1),"
        " (SELECT finished_at FROM runs WHERE error IS NULL ORDER BY id DESC LIMIT 1),"
        " (SELECT started_at FROM runs ORDER BY id LIMIT 1)",
    )
    return (text(row, 0), text(row, 1), text(row, 2)) if row else (None, None, None)
