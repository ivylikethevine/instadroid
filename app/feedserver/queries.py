"""Read-only access to the scraper's database: every query the feeds and the status page run, and the
row accessors they read results with. A route opens one connection() and passes it to each query."""

import re
import sqlite3
from collections.abc import Callable, Generator, Sequence
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path

from shared import sqlrows
from shared.sqlrows import SqlValue

from . import settings

type EtagPart = SqlValue | tuple[SqlValue, ...]  # what an ETag is computed over

MAX_SEARCH_WORDS: int = 32  # FTS5 caps an expression's size; words past this are ignored


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


def count(con: sqlite3.Connection, sql: str, args: Sequence[SqlValue] = ()) -> int:
    """The COUNT(*) a query selects, or 0 before the database or table exists."""
    row: sqlite3.Row | None = one_row(con, sql, args)
    return (sqlrows.cell_int(row, 0) or 0) if row else 0


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


@dataclass(frozen=True)
class PostFilter:
    """What a posts feed is narrowed to: one account, stored kinds, a start time (an ISO timestamp in
    UTC, compared with the posted time, else the saved time) and caption text."""

    user: str | None = None
    kinds: tuple[str, ...] = ()
    since: str | None = None
    q: str | None = None

    def where(self) -> tuple[str, list[SqlValue]]:
        """(" WHERE ..." or "", its arguments)."""
        clauses: list[str] = []
        args: list[SqlValue] = []
        if self.user:
            clauses.append("username = ?")
            args.append(self.user)
        if self.kinds:
            clauses.append(f"kind IN ({','.join('?' * len(self.kinds))})")
            args.extend(self.kinds)
        if self.since:
            clauses.append("COALESCE(posted_at, scraped_at) >= ?")
            args.append(self.since)
        if self.q:
            clauses.append("caption LIKE ? ESCAPE '\\'")
            args.append("%" + re.sub(r"([\\%_])", r"\\\1", self.q) + "%")
        return (" WHERE " + " AND ".join(clauses) if clauses else ""), args


def posts(con: sqlite3.Connection, where: PostFilter, requested: int) -> list[sqlite3.Row]:
    sql: str
    args: list[SqlValue]
    sql, args = where.where()
    return all_rows(
        con,
        f"SELECT * FROM posts{sql} ORDER BY COALESCE(posted_at, scraped_at) DESC LIMIT ?",
        (*args, limit(requested)),
    )


def fts_query(q: str) -> str:
    """A person's search text as an FTS5 query that can't be a syntax error: each word quoted, so
    nothing in it is an operator, and matched as a prefix; every word must match. "" for blank text.
    NUL goes first since FTS5 would stop reading the query there, inside an open quote."""
    words: list[str] = q.replace("\x00", " ").split()[:MAX_SEARCH_WORDS]
    return " ".join('"' + word.replace('"', '""') + '"*' for word in words)


def archive_page(
    con: sqlite3.Connection,
    user: str | None,
    q: str | None,
    before: tuple[str, str] | None,
    requested: int,
) -> list[sqlite3.Row]:
    """/browse's posts, newest first, up to one past limit(requested) so the caller can tell whether
    there's another page. `before` is the (sort_at, id) of the previous page's last row: seeking from it
    keeps the sort to one page of rows at any depth. Before the search index exists, a search finds nothing."""
    source: str = "posts"
    clauses: list[str] = []
    args: list[SqlValue] = []
    match: str = fts_query(q or "")
    if match:
        source = "posts_fts JOIN posts_fts_ids ON fts_id = posts_fts.rowid JOIN posts ON id = post_id"
        clauses.append("posts_fts MATCH ?")
        args.append(match)
    if user:
        clauses.append("username = ?")
        args.append(user)
    if before:
        clauses.append("(COALESCE(posted_at, scraped_at), id) < (?, ?)")
        args.extend(before)
    where: str = " WHERE " + " AND ".join(clauses) if clauses else ""
    return all_rows(
        con,
        f"SELECT posts.*, COALESCE(posted_at, scraped_at) AS sort_at FROM {source}{where}"
        " ORDER BY sort_at DESC, id DESC LIMIT ?",
        (*args, limit(requested) + 1),
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


def avatar_files(con: sqlite3.Connection, usernames: list[str]) -> dict[str, str]:
    """{username: avatar_file} for the given accounts that have a captured avatar."""
    placeholders: str = ",".join("?" * len(usernames))
    sql: str = f"SELECT username, avatar_file FROM accounts WHERE avatar_file IS NOT NULL AND username IN ({placeholders})"
    return {string(row, 0): string(row, 1) for row in all_rows(con, sql, usernames)}


def feed_signal(con: sqlite3.Connection, user: str | None, alerts: list[sqlite3.Row]) -> tuple[EtagPart, ...]:
    """Cheap ETag input for /instagram.xml: post count and latest change (updated_at also moves when
    a row is merged in place), extra-slide count, latest avatar refresh and the `alerts` the feed
    shows, scoped to `user` when given so one account's new post doesn't invalidate every other
    per-account feed."""
    where: str
    args: tuple[str, ...]
    where, args = (" WHERE username = ?", (user,)) if user else ("", ())
    slides: str = f" WHERE post_id IN (SELECT id FROM posts{where})" if user else ""
    counts: sqlite3.Row | None = one_row(
        con, f"SELECT COUNT(*), COALESCE(MAX(COALESCE(updated_at, scraped_at)), '') FROM posts{where}", args
    )
    avatar: sqlite3.Row | None = one_row(
        con, f"SELECT COALESCE(MAX(avatar_updated_at), '') FROM accounts{where}", args
    )
    return (
        *(sqlrows.values(counts) if counts else (0, "")),
        count(con, f"SELECT COUNT(*) FROM media{slides}", args),
        sqlrows.cell(avatar, 0) if avatar else "",
        *(sqlrows.values(a) for a in alerts),
    )


def open_alerts(con: sqlite3.Connection) -> list[sqlite3.Row]:
    """The scraper's open failure alerts (instadroid/alerts.py), oldest first; none before that table exists."""
    return all_rows(con, "SELECT kind, message, raised_at FROM alerts ORDER BY raised_at")


def post_count(con: sqlite3.Connection) -> int:
    return count(con, "SELECT COUNT(*) FROM posts")


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


def run_times(con: sqlite3.Connection) -> tuple[str | None, str | None, str | None]:
    """(latest finished_at, latest successful finished_at, first started_at) over every run."""
    row: sqlite3.Row | None = one_row(
        con,
        "SELECT (SELECT finished_at FROM runs ORDER BY id DESC LIMIT 1),"
        " (SELECT finished_at FROM runs WHERE error IS NULL ORDER BY id DESC LIMIT 1),"
        " (SELECT started_at FROM runs ORDER BY id LIMIT 1)",
    )
    return (text(row, 0), text(row, 1), text(row, 2)) if row else (None, None, None)


def starts_since(con: sqlite3.Connection, cutoff: str) -> list[str]:
    """Every run's started_at from `cutoff` (an ISO timestamp) on, oldest first: MAX_RUNS_PER_DAY's window."""
    rows: list[sqlite3.Row] = all_rows(
        con, "SELECT started_at FROM runs WHERE started_at >= ? ORDER BY started_at", (cutoff,)
    )
    return [string(r, 0) for r in rows]


def following_state(con: sqlite3.Connection) -> tuple[int, str | None, int]:
    """(accounts on the stored Following list, when it was last refreshed, accounts in a refresh held
    back for being far shorter); zeros before those tables exist."""
    row: sqlite3.Row | None = one_row(con, "SELECT COUNT(*), MAX(updated_at) FROM following")
    return (
        (sqlrows.cell_int(row, 0) or 0) if row else 0,
        text(row, 1) if row else None,
        count(con, "SELECT COUNT(*) FROM following_pending"),
    )
