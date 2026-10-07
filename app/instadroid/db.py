"""The SQLite schema, numbered migrations, runs, accounts, and duplicate-post merging."""

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import ReadOnly, TypedDict, Unpack

from shared import sqlrows
from shared.timestamps import parse_iso

from . import common, config, device, parsing, retention
from .common import log


class PostRow(TypedDict):
    """A posts table row, as a live scrape writes it."""

    id: str  # the permalink shortcode, or the card's hash when no permalink was captured
    username: str
    kind: str
    posted_date: str
    caption: str
    media_file: str | None
    scraped_at: str  # first seen
    hash: str  # parsing.post_id() of the card
    alt_hash: str | None  # its post_id() under the other caption form (collapsed or expanded), if they differ
    url: str | None
    place: str
    posted_at: str | None
    updated_at: str  # last changed (e.g. merged); drives the feed ETag
    ig_version: str | None


class StoredPost(TypedDict):
    """A posts table row as it may be stored: rows written before a column existed, or migrated, can
    hold NULL in any column but id, username and scraped_at. Read-only, so a PostRow is one too."""

    id: ReadOnly[str]
    username: ReadOnly[str]
    kind: ReadOnly[str | None]
    posted_date: ReadOnly[str | None]
    caption: ReadOnly[str | None]
    media_file: ReadOnly[str | None]
    scraped_at: ReadOnly[str]
    hash: ReadOnly[str | None]
    alt_hash: ReadOnly[str | None]
    url: ReadOnly[str | None]
    place: ReadOnly[str | None]
    posted_at: ReadOnly[str | None]
    updated_at: ReadOnly[str | None]
    ig_version: ReadOnly[str | None]


class RunMetrics(TypedDict, total=False):
    """The runs columns a scrape reports besides its new post count (scrape.RunStats's `metrics`)."""

    link_sheet_failures: int
    link_clipboard_failures: int
    new_stories: int
    filtered_posts: int
    warning: str | None
    cards_per_screen: float
    share_captioned: float
    share_complete: float
    mem_peak_mb: int | None
    oom_kills: int | None


class RunRow(device.DeviceSnapshot, RunMetrics):
    """A runs table row."""

    started_at: str
    finished_at: str
    new_posts: int
    error: str | None


class VersionPair(TypedDict):
    """One row of version_pairs()."""

    redroid_image: str | None
    ig_version: str
    selector_profile: str | None
    runs: int
    clean_runs: int
    ok_runs: int
    new_posts: int
    last_run: str


def stored_post(row: sqlite3.Row) -> StoredPost:
    """A posts row read with `SELECT *`."""
    text: Callable[[sqlite3.Row, int | str], str | None] = sqlrows.cell_str
    return {
        "id": sqlrows.must_str(row, "id"),
        "username": sqlrows.must_str(row, "username"),
        "kind": text(row, "kind"),
        "posted_date": text(row, "posted_date"),
        "caption": text(row, "caption"),
        "media_file": text(row, "media_file"),
        "scraped_at": sqlrows.must_str(row, "scraped_at"),
        "hash": text(row, "hash"),
        "alt_hash": text(row, "alt_hash"),
        "url": text(row, "url"),
        "place": text(row, "place"),
        "posted_at": text(row, "posted_at"),
        "updated_at": text(row, "updated_at"),
        "ig_version": text(row, "ig_version"),
    }


def db_init() -> sqlite3.Connection:
    Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    con: sqlite3.Connection = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute(
        """CREATE TABLE IF NOT EXISTS posts (
            id TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            kind TEXT,
            posted_date TEXT,
            caption TEXT,
            media_file TEXT,
            scraped_at TEXT NOT NULL
        )"""
    )
    # ig_version: the Instagram versionName that scraped the post, so a parsing quirk can be traced
    # to the app build that produced it. NULL for posts stored before this column existed.
    _add_columns(
        con, "posts", dict.fromkeys(("hash", "url", "place", "posted_at", "updated_at", "ig_version"), "TEXT")
    )
    # permalink_attempts: Copy link tries spent backfilling a hash-id post (scrape._backfill_permalink()).
    _add_columns(con, "posts", {"permalink_attempts": "INTEGER NOT NULL DEFAULT 0"})
    # alt_hash: the card's hash under its other caption form, so a post is recognised collapsed or expanded.
    _add_columns(con, "posts", {"alt_hash": "TEXT"})
    # Backfill for rows written before updated_at existed, and a no-op once that's done.
    con.execute("UPDATE posts SET updated_at = scraped_at WHERE updated_at IS NULL")
    con.execute("CREATE INDEX IF NOT EXISTS posts_hash ON posts(hash)")
    con.execute("CREATE INDEX IF NOT EXISTS posts_alt_hash ON posts(alt_hash)")
    con.execute("CREATE INDEX IF NOT EXISTS posts_username_posted_at ON posts(username, posted_at)")
    con.execute(
        # idx is 1-based: the cover image stays in posts.media_file as today, this only holds
        # additional carousel slides, so no data migration is needed for any existing post.
        """CREATE TABLE IF NOT EXISTS media (
            post_id TEXT NOT NULL,
            idx INTEGER NOT NULL,
            file TEXT NOT NULL,
            PRIMARY KEY (post_id, idx)
        )"""
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS accounts (
            username TEXT PRIMARY KEY,
            account_id TEXT,
            avatar_file TEXT,
            avatar_updated_at TEXT
        )"""
    )
    con.execute(
        # id is a content hash of the captured crop (see stories.capture_story_media()) — stories have no
        # public permalink/shortcode the way posts do, so there's no other stable identity to key
        # on. No pre-existing data to migrate: this table starts empty on every DB.
        """CREATE TABLE IF NOT EXISTS stories (
            id TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            media_file TEXT,
            kind TEXT,
            posted_date TEXT,
            scraped_at TEXT NOT NULL
        )"""
    )
    # phash: stories._dhash() of the crop, for catching a re-capture of a story already stored.
    _add_columns(con, "stories", {"phash": "TEXT"})
    con.execute("CREATE INDEX IF NOT EXISTS stories_username ON stories(username)")
    con.execute("CREATE INDEX IF NOT EXISTS stories_scraped_at ON stories(scraped_at)")
    con.execute(
        # Open failure alerts, one row per kind (see alerts.update()); the feed server shows them.
        """CREATE TABLE IF NOT EXISTS alerts (
            kind TEXT PRIMARY KEY,
            message TEXT NOT NULL,
            raised_at TEXT NOT NULL
        )"""
    )
    con.execute(
        # The whole table is replaced atomically on every successful refresh (see
        # navigation.refresh_following_list()) rather than upserted row by row, so an unfollow is reflected
        # simply by that username's row no longer existing after the next refresh — every row
        # shares the same updated_at, which also doubles as "when was this list last refreshed."
        """CREATE TABLE IF NOT EXISTS following (
            username TEXT PRIMARY KEY,
            updated_at TEXT NOT NULL
        )"""
    )
    con.execute(
        # A stored post seen again under another username (scrape.py): evidence of a rename, kept
        # until rename_account() moves the history or the post is pruned.
        """CREATE TABLE IF NOT EXISTS rename_candidates (
            old TEXT NOT NULL,
            new TEXT NOT NULL,
            post_id TEXT NOT NULL,
            seen_at TEXT NOT NULL,
            PRIMARY KEY (old, new, post_id)
        )"""
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL,
            new_posts INTEGER,
            error TEXT,
            android_release TEXT,
            android_sdk TEXT,
            device_product TEXT
        )"""
    )
    _add_columns(
        con,
        "runs",
        {
            **dict.fromkeys(
                (
                    "link_sheet_failures",
                    "link_clipboard_failures",
                    "new_stories",
                    "filtered_posts",
                    "mem_peak_mb",
                    "oom_kills",
                ),
                "INTEGER",
            ),
            **dict.fromkeys(("warning", "ig_version", "redroid_image", "selector_profile"), "TEXT"),
            **dict.fromkeys(("cards_per_screen", "share_captioned", "share_complete"), "REAL"),
        },
    )
    con.commit()
    _migrate(con)
    return con


def _migrate(con: sqlite3.Connection) -> None:
    """Run each MIGRATIONS entry the database hasn't had yet, in order. PRAGMA user_version counts
    how many have run, and is bumped only after each one finishes, so a crash mid-migration retries
    it on the next start. Append new migrations; never reorder or remove one."""
    done: int = sqlrows.scalar_int(con.execute("PRAGMA user_version")) or 0
    version: int
    migration: Callable[[sqlite3.Connection], None]
    for version, migration in enumerate(MIGRATIONS[done:], start=done + 1):
        migration(con)
        con.execute(f"PRAGMA user_version = {version}")
        con.commit()


def _add_columns(con: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    """ALTER TABLE ADD COLUMN for each {name: type} the table doesn't have yet."""
    existing: set[sqlrows.SqlValue] = {
        sqlrows.cell(r, 1) for r in sqlrows.fetch_all(con.execute(f"PRAGMA table_info({table})"))
    }
    col: str
    kind: str
    for col, kind in columns.items():
        if col not in existing:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {kind}")


def record_run(
    con: sqlite3.Connection,
    started_at: str,
    finished_at: str,
    new_posts: int,
    error: str | None,
    snapshot: device.DeviceSnapshot,
    **stats: Unpack[RunMetrics],
) -> None:
    """Insert one runs row. `snapshot` (see device.device_snapshot()) and `stats` are keyed by runs column
    name, so a new metric only needs its column in db_init(), a RunMetrics key, and a value in
    scrape._scrape_feed()'s `metrics`."""
    row: RunRow = {
        "started_at": started_at,
        "finished_at": finished_at,
        "new_posts": new_posts,
        "error": error,
        "link_sheet_failures": 0,
        "link_clipboard_failures": 0,
        "new_stories": 0,
        "filtered_posts": 0,
        **snapshot,
        **stats,
    }
    insert_row(con, "runs", row)
    con.commit()


def consecutive_failures(con: sqlite3.Connection) -> int:
    """How many of the most recent runs, counting back from the newest, ended in an error: what the
    poll loop's failure backoff (scrape.next_sleep_seconds()) is based on, read from the table so a
    restart doesn't forget it."""
    n: int = 0
    r: sqlite3.Row
    for r in sqlrows.fetch_all(con.execute("SELECT error FROM runs ORDER BY id DESC LIMIT 64")):
        if sqlrows.cell_str(r, 0) is None:
            break
        n += 1
    return n


def version_pairs(con: sqlite3.Connection) -> list[VersionPair]:
    """Every redroid image / Instagram build / profile combination this database has run, with its
    run count, clean runs (no error, no warning), posts stored and last run date: the raw data for
    docs/COMPATIBILITY.md (`scraper.py compat`). Runs that never read the device are left out."""
    cur: sqlite3.Cursor = con.execute(
        """SELECT redroid_image, ig_version, selector_profile, COUNT(*) AS runs,
                  SUM(error IS NULL AND COALESCE(warning, '') = '') AS clean_runs,
                  SUM(error IS NULL) AS ok_runs, COALESCE(SUM(new_posts), 0) AS new_posts,
                  MAX(started_at) AS last_run
           FROM runs WHERE ig_version IS NOT NULL
           GROUP BY redroid_image, ig_version, selector_profile
           ORDER BY redroid_image, ig_version, selector_profile"""
    )
    return [
        {
            "redroid_image": sqlrows.cell_str(r, "redroid_image"),
            "ig_version": sqlrows.must_str(r, "ig_version"),
            "selector_profile": sqlrows.cell_str(r, "selector_profile"),
            "runs": sqlrows.must_int(r, "runs"),
            "clean_runs": sqlrows.must_int(r, "clean_runs"),
            "ok_runs": sqlrows.must_int(r, "ok_runs"),
            "new_posts": sqlrows.must_int(r, "new_posts"),
            "last_run": sqlrows.must_str(r, "last_run"),
        }
        for r in sqlrows.fetch_all(cur)
    ]


def check_selector_drift(
    con: sqlite3.Connection, cards_per_screen: float, share_captioned: float, share_complete: float
) -> str | None:
    """Compare this run's parse yield against the rolling average of the last
    SELECTOR_DRIFT_BASELINE_RUNS successful runs; returns a warning string if any metric falls
    below SELECTOR_DRIFT_THRESHOLD of its baseline, else None. Requires at least
    SELECTOR_DRIFT_MIN_RUNS prior runs with a nonzero baseline for a given metric before judging
    it, so a fresh DB or a quiet account can't false-positive on its first few runs."""
    if not config.SELECTOR_DRIFT_BASELINE_RUNS:
        return None
    rows: list[sqlite3.Row] = sqlrows.fetch_all(
        con.execute(
            "SELECT cards_per_screen, share_captioned, share_complete FROM runs"
            " WHERE error IS NULL AND cards_per_screen IS NOT NULL ORDER BY id DESC LIMIT ?",
            (config.SELECTOR_DRIFT_BASELINE_RUNS,),
        )
    )
    dropped: list[str] = []
    label: str
    current: float
    key: str
    for label, current, key in (
        ("cards/screen", cards_per_screen, "cards_per_screen"),
        ("captioned", share_captioned, "share_captioned"),
        ("complete", share_complete, "share_complete"),
    ):
        v: float | None
        baseline_vals: list[float] = [v for r in rows if (v := sqlrows.cell_float(r, key)) is not None]
        if len(baseline_vals) < config.SELECTOR_DRIFT_MIN_RUNS:
            continue
        baseline: float = sum(baseline_vals) / len(baseline_vals)
        if baseline > 0 and current < baseline * config.SELECTOR_DRIFT_THRESHOLD:
            dropped.append(f"{label} {current:.2f} vs {baseline:.2f} baseline ({len(baseline_vals)} runs)")
    return "selector drift? " + "; ".join(dropped) if dropped else None


def upsert_account(con: sqlite3.Connection, username: str, avatar_file: str | None = None) -> None:
    if avatar_file:
        con.execute(
            "INSERT INTO accounts (username, avatar_file, avatar_updated_at) VALUES (?,?,?)"
            " ON CONFLICT(username) DO UPDATE SET avatar_file=excluded.avatar_file,"
            " avatar_updated_at=excluded.avatar_updated_at",
            (username, avatar_file, datetime.now(UTC).isoformat()),
        )
    else:
        con.execute("INSERT OR IGNORE INTO accounts (username) VALUES (?)", (username,))
    con.commit()


def needs_avatar_refresh(con: sqlite3.Connection, username: str) -> bool:
    row: sqlite3.Row | None = sqlrows.fetch_one(
        con.execute("SELECT avatar_updated_at FROM accounts WHERE username=?", (username,))
    )
    return common.older_than(
        sqlrows.cell(row, "avatar_updated_at") if row else None, config.AVATAR_REFRESH_DAYS
    )


def needs_following_refresh(con: sqlite3.Connection) -> bool:
    """Unlike needs_avatar_refresh(), this is a single global check, not per-account: the whole
    Following list is captured (and replaced) in one pass, so there's one "when was this last
    done" timestamp, not one per row. No rows at all means never successfully refreshed."""
    return common.older_than(
        sqlrows.scalar(con.execute("SELECT MAX(updated_at) FROM following")), config.FOLLOWING_REFRESH_DAYS
    )


def rename_account(con: sqlite3.Connection, old: str, new: str) -> int:
    """Reconcile a followed account's history after it renamed itself: repoint every post from
    `old` to `new` and fold `old`'s accounts row (avatar, stable account_id if set) into `new`.
    Nothing calls this on its own: a stored post seen under a new username only raises an alert
    (note_rename_candidate()), and `scraper.py rename` runs this once a person agrees. Note this does not
    fix up an existing `?user=old` FreshRSS subscription; re-subscribe under the new username."""
    if old == new:
        return 0
    moved: int = con.execute("UPDATE posts SET username=? WHERE username=?", (new, old)).rowcount
    query: str = "SELECT account_id, avatar_file FROM accounts WHERE username=?"
    old_row: sqlite3.Row | None = sqlrows.fetch_one(con.execute(query, (old,)))
    new_row: sqlite3.Row | None = sqlrows.fetch_one(con.execute(query, (new,)))
    old_avatar: str | None = sqlrows.cell_str(old_row, "avatar_file") if old_row else None
    account_id: str | None = (sqlrows.cell_str(new_row, "account_id") if new_row else None) or (
        sqlrows.cell_str(old_row, "account_id") if old_row else None
    )
    avatar_file: str | None = (sqlrows.cell_str(new_row, "avatar_file") if new_row else None) or old_avatar
    dropped_avatar: str | None = old_avatar if old_row and old_avatar != avatar_file else None
    con.execute(
        "INSERT INTO accounts (username, account_id, avatar_file) VALUES (?,?,?)"
        " ON CONFLICT(username) DO UPDATE SET account_id=excluded.account_id,"
        " avatar_file=COALESCE(accounts.avatar_file, excluded.avatar_file)",
        (new, account_id, avatar_file),
    )
    con.execute("DELETE FROM accounts WHERE username=?", (old,))
    # Keep the followed-accounts allowlist (if in use) in step with a rename too — otherwise every
    # post from `new` would be filtered out as "not followed" until the next scheduled refresh.
    following_row: sqlite3.Row | None = sqlrows.fetch_one(
        con.execute("SELECT updated_at FROM following WHERE username=?", (old,))
    )
    if following_row:
        con.execute(
            "INSERT INTO following (username, updated_at) VALUES (?, ?) ON CONFLICT(username) DO NOTHING",
            (new, sqlrows.cell(following_row, "updated_at")),
        )
        con.execute("DELETE FROM following WHERE username=?", (old,))
    con.execute("DELETE FROM rename_candidates WHERE old IN (?, ?) OR new IN (?, ?)", (old, new, old, new))
    con.commit()
    retention.discard_media(dropped_avatar)
    return moved


def note_rename_candidate(con: sqlite3.Connection, old: str, new: str, post_id: str) -> None:
    """Record that `post_id`, stored for `old`, was on screen as a post of `new`."""
    con.execute(
        "INSERT OR IGNORE INTO rename_candidates (old, new, post_id, seen_at) VALUES (?,?,?,?)",
        (old, new, post_id, datetime.now(UTC).isoformat()),
    )
    con.commit()


def rename_candidates(con: sqlite3.Connection) -> list[tuple[str, str, int]]:
    """(old, new, posts seen) for each suspected rename whose evidence still stands: the post is still
    stored, and still under the old username."""
    cur: sqlite3.Cursor = con.execute(
        "SELECT c.old, c.new, COUNT(*) FROM rename_candidates c"
        " JOIN posts p ON p.id = c.post_id AND p.username = c.old GROUP BY c.old, c.new ORDER BY c.old, c.new"
    )
    return [
        (sqlrows.must_str(r, 0), sqlrows.must_str(r, 1), sqlrows.cell_int(r, 2) or 0)
        for r in sqlrows.fetch_all(cur)
    ]


def _safe_parse_posted_at(
    posted_date: str | None, scraped_at_iso: sqlrows.SqlValue
) -> tuple[datetime | None, int | None]:
    """parsing.parse_posted_at(), tolerant of a malformed/legacy scraped_at that fromisoformat rejects.
    Returns (None, None) instead of raising, so one corrupt row can't abort the whole migration."""
    now: datetime | None = parse_iso(scraped_at_iso)
    return (now and posted_date and parsing.parse_posted_at(posted_date, now)) or (None, None)


def _migrate_dedupe(con: sqlite3.Connection) -> None:
    """Migration 1: backfill posted_at for rows written before that column existed, then merge any
    rows parsing.same_post() considers duplicates — the bug that let a card get stored twice when its caption
    hadn't rendered on the first pass. Uses the same merge path as a live scrape. A single corrupt
    row is skipped rather than turned into a permanent boot loop."""
    log("running one-time dedupe migration")
    r: sqlite3.Row | None  # a posts row; None once fetch_one() finds it merged away
    for r in sqlrows.fetch_all(
        con.execute("SELECT id, posted_date, scraped_at FROM posts WHERE posted_at IS NULL")
    ):
        posted_at: datetime | None
        _: int | None
        posted_at, _ = _safe_parse_posted_at(
            sqlrows.cell_str(r, "posted_date"), sqlrows.cell(r, "scraped_at")
        )
        if posted_at:
            con.execute(
                "UPDATE posts SET posted_at=? WHERE id=?", (posted_at.isoformat(), sqlrows.cell(r, "id"))
            )
    con.commit()
    merged: int = 0
    id_row: sqlite3.Row
    for id_row in sqlrows.fetch_all(con.execute("SELECT id FROM posts ORDER BY scraped_at")):
        rid: sqlrows.SqlValue = sqlrows.cell(id_row, 0)
        r = sqlrows.fetch_one(con.execute("SELECT * FROM posts WHERE id=?", (rid,)))
        if r is None:
            continue  # already merged away as another row's duplicate
        try:
            post: StoredPost = stored_post(r)
            precision: int | None
            posted_at, precision = _safe_parse_posted_at(post["posted_date"], sqlrows.cell(r, "scraped_at"))
            dup: sqlite3.Row | None = find_duplicate(
                con, post["username"], posted_at, precision, post["caption"], exclude_id=post["id"]
            )
            if not dup:
                continue
            row: StoredPost
            media_to_drop: str | None
            row, media_to_drop = merged_fields(dup, post, datetime.now(UTC))
            retention.discard_media(media_to_drop)
            if row["id"] != post["id"]:
                con.execute("DELETE FROM posts WHERE id=?", (post["id"],))
            write_merged(con, sqlrows.must_str(dup, "id"), row)
            merged += 1
        except Exception as e:  # a single corrupt/unexpected row must not block every future start
            log(f"WARN: dedupe migration skipped row {rid!r}:", repr(e))
    log(f"dedupe migration: merged {merged} duplicate row(s)")


def _migrate_accounts(con: sqlite3.Connection) -> None:
    """Migration 2: give every username already in posts an accounts row, so avatar capture and
    rename_account() have something to attach to for accounts seen before that table existed."""
    log("running one-time accounts backfill")
    username_row: sqlite3.Row
    for username_row in sqlrows.fetch_all(con.execute("SELECT DISTINCT username FROM posts")):
        username: sqlrows.SqlValue = sqlrows.cell(username_row, 0)
        try:
            con.execute("INSERT OR IGNORE INTO accounts (username) VALUES (?)", (username,))
        except Exception as e:  # a single bad username must not block every future start
            log(f"WARN: accounts backfill skipped {username!r}:", repr(e))


def _migrate_story_retention(con: sqlite3.Connection) -> None:
    """Migration 3: drop stories.expires_at now that stories share RETAIN_DAYS with posts."""
    cols: set[sqlrows.SqlValue] = {
        sqlrows.cell(r, "name") for r in sqlrows.fetch_all(con.execute("PRAGMA table_info(stories)"))
    }
    if "expires_at" in cols:
        log("running one-time story-retention migration")
        con.execute("DROP INDEX IF EXISTS stories_expires_at")
        con.execute("ALTER TABLE stories DROP COLUMN expires_at")


def _migrate_drop_media_post_index(con: sqlite3.Connection) -> None:
    """Migration 4: drop the media_post index, which duplicated the leftmost column of media's primary
    key (post_id, idx) and so only cost writes."""
    con.execute("DROP INDEX IF EXISTS media_post")


def _migrate_rehash(con: sqlite3.Connection) -> None:
    """Recompute posts.hash after parsing._post_key() changed (2026-09-16: the caption's first 40
    characters instead of 200, so a truncated and an expanded caption hash alike). The stored caption
    is what the card's caption or, failing that, its media description was, so the same rule
    (parsing.caption_key() / parsing.alt_key()) reproduces the key; without this every stored post
    would be captured once more and merged by the duplicate check instead of recognised outright."""
    log("running one-time rehash migration")
    rows: list[sqlite3.Row] = sqlrows.fetch_all(con.execute("SELECT id, username, caption FROM posts"))
    changed: int = 0
    r: sqlite3.Row
    for r in rows:
        rid: str | None = sqlrows.cell_str(r, "id")
        username: str | None = sqlrows.cell_str(r, "username")
        if rid is None or username is None:  # a corrupt legacy row must not block every later start
            log(f"WARN: rehash migration skipped a row without an id or username: {rid!r}")
            continue
        new_hash: str = _stored_caption_hash(username, sqlrows.cell_str(r, "caption"))
        cur: sqlite3.Cursor = con.execute(
            "UPDATE posts SET hash=? WHERE id=? AND hash IS NOT ?", (new_hash, rid, new_hash)
        )
        changed += cur.rowcount
    con.commit()
    log(f"rehash migration: updated {changed} of {len(rows)} row(s)")


def _stored_caption_hash(username: str, caption: str | None) -> str:
    """parsing.post_id() of a stored post, from its stored caption: what the card's caption or,
    failing that, its media description was."""
    text: str = caption or ""
    key: str = parsing.alt_key(text) if parsing.is_weak_caption(text) else parsing.caption_key(text)
    return common.digest(f"{username}|{key}")


def _migrate_alt_hash(con: sqlite3.Connection) -> None:
    """Fill posts.alt_hash where it can be worked out: the stored caption is the expanded one, so a
    row whose hash isn't that caption's was last seen collapsed, and the expanded key is its other
    form. A row stored under the expanded key gets its collapsed one the next time it's on screen."""
    log("running one-time alt-hash migration")
    filled: int = 0
    r: sqlite3.Row
    for r in sqlrows.fetch_all(
        con.execute(
            "SELECT id, username, caption, hash FROM posts"
            " WHERE alt_hash IS NULL AND id IS NOT NULL AND username IS NOT NULL"
        )
    ):
        expanded: str = _stored_caption_hash(sqlrows.must_str(r, "username"), sqlrows.cell_str(r, "caption"))
        if sqlrows.cell_str(r, "hash") not in (None, expanded):
            con.execute("UPDATE posts SET alt_hash=? WHERE id=?", (expanded, sqlrows.cell(r, "id")))
            filled += 1
    con.commit()
    log(f"alt-hash migration: filled {filled} row(s)")


def _migrate_merge_twins(con: sqlite3.Connection) -> None:
    """Merge a row stored with neither a date nor a permalink into the row that holds the same
    account's same caption: the second capture of a post whose caption had just been expanded, taken
    after its header scrolled off. The other row wins, as in a live merge; the twin's extra slides
    follow its cover, kept if that's adopted and discarded otherwise."""
    log("running one-time twin-merge migration")
    merged: int = 0
    id_row: sqlite3.Row
    for id_row in sqlrows.fetch_all(
        con.execute("SELECT id FROM posts WHERE url IS NULL AND posted_at IS NULL ORDER BY scraped_at")
    ):
        rid: sqlrows.SqlValue = sqlrows.cell(id_row, 0)
        try:
            # Read now, not with the ids: an earlier merge may have changed this row.
            r: sqlite3.Row | None = sqlrows.fetch_one(con.execute("SELECT * FROM posts WHERE id=?", (rid,)))
            twin: StoredPost | None = stored_post(r) if r else None
            dup: sqlite3.Row | None = (
                find_duplicate(con, twin["username"], None, None, twin["caption"], exclude_id=twin["id"])
                if twin
                else None
            )
            if not twin or not dup:
                continue
            keeper: str = sqlrows.must_str(dup, "id")
            row: StoredPost
            media_to_drop: str | None
            row, media_to_drop = merged_fields(dup, twin, datetime.now(UTC))
            if twin["media_file"] and row["media_file"] == twin["media_file"]:
                con.execute("UPDATE OR IGNORE media SET post_id=? WHERE post_id=?", (keeper, twin["id"]))
            slides: list[str] = [
                sqlrows.must_str(m, 0)
                for m in sqlrows.fetch_all(
                    con.execute("SELECT file FROM media WHERE post_id=?", (twin["id"],))
                )
            ]
            con.execute("DELETE FROM media WHERE post_id=?", (twin["id"],))
            con.execute("DELETE FROM posts WHERE id=?", (twin["id"],))
            write_merged(con, keeper, row)
            con.commit()
            retention.discard_media(media_to_drop, *slides)
            merged += 1
        except Exception as e:  # a single corrupt/unexpected row must not block every future start
            con.rollback()
            log(f"WARN: twin-merge migration skipped row {rid!r}:", repr(e))
    log(f"twin-merge migration: merged {merged} twin row(s)")


MIGRATIONS: tuple[Callable[[sqlite3.Connection], None], ...] = (
    _migrate_dedupe,
    _migrate_accounts,
    _migrate_story_retention,
    _migrate_drop_media_post_index,
    _migrate_rehash,
    _migrate_alt_hash,
    _migrate_merge_twins,
)


def _find_same_caption(
    con: sqlite3.Connection, username: str, caption: str | None, exclude_id: str | None
) -> sqlite3.Row | None:
    """A stored row of this account with the same caption, for a card that shows no date (the
    header-less top card), a permalinked row first. Only a caption longer than any collapsed one
    counts, and only among rows RETAIN_DAYS back: without a date, a short or recurring caption can't
    tell a repost from the post."""
    shown: str = parsing.flat_caption(caption)
    if parsing.is_weak_caption(caption) or len(shown) < parsing.CAPTION_KEY_CHARS:
        return None
    since: str = (
        (datetime.now(UTC) - timedelta(days=config.RETAIN_DAYS)).isoformat() if config.RETAIN_DAYS > 0 else ""
    )
    r: sqlite3.Row
    for r in sqlrows.fetch_all(
        con.execute(
            "SELECT * FROM posts WHERE username=? AND scraped_at >= ? ORDER BY url IS NULL, scraped_at",
            (username, since),
        )
    ):
        if exclude_id and sqlrows.cell(r, "id") == exclude_id:
            continue
        if parsing.flat_caption(sqlrows.cell_str(r, "caption"))[:200] == shown[:200]:
            return r
    return None


def find_duplicate(
    con: sqlite3.Connection,
    username: str,
    posted_at: datetime | None,
    posted_at_prec: int | None,
    caption: str | None,
    exclude_id: str | None = None,
) -> sqlite3.Row | None:
    """Look up a stored row that parsing.same_post() considers the same post as this freshly-parsed
    card, within a coarse SQL time window (same_post itself applies the exact tolerance). A card
    with no date is matched on its caption alone (_find_same_caption())."""
    if posted_at is None:
        return _find_same_caption(con, username, caption, exclude_id)
    window: timedelta = timedelta(seconds=max(posted_at_prec or 0, 3600) * 2)
    candidate: parsing.PostIdentity = {
        "username": username,
        "caption": caption,
        "posted_at": posted_at,
        "posted_at_precision": posted_at_prec,
    }
    cur: sqlite3.Cursor = con.execute(
        "SELECT * FROM posts WHERE username=? AND posted_at BETWEEN ? AND ?",
        (username, (posted_at - window).isoformat(), (posted_at + window).isoformat()),
    )
    r: sqlite3.Row
    for r in sqlrows.fetch_all(cur):
        if exclude_id and sqlrows.cell(r, "id") == exclude_id:
            continue
        stored_at: str | None = sqlrows.cell_str(r, "posted_at")
        existing: parsing.PostIdentity = {
            "username": sqlrows.must_str(r, "username"),
            "caption": sqlrows.cell_str(r, "caption"),
            "posted_at": datetime.fromisoformat(stored_at) if stored_at else None,
        }
        if parsing.same_post(existing, candidate):
            return r
    return None


def merged_fields(existing: sqlite3.Row, new: StoredPost, now: datetime) -> tuple[StoredPost, str | None]:
    """The row that should replace `existing` (a stored posts row) once `new` turns out to be the
    same post: keep a permalink id/url over a hash id, a real caption over a weak placeholder, the
    first-seen scraped_at and ig_version, and whichever media crop already exists. Returns (row,
    media file to delete). `now` becomes updated_at, so the feed's ETag notices the merge."""
    weak: Callable[[str | None], bool] = parsing.is_weak_caption
    old: StoredPost = stored_post(existing)
    media: str | None
    media_to_drop: str | None
    media, media_to_drop = old["media_file"], None
    if new["media_file"] and old["media_file"] and new["media_file"] != old["media_file"]:
        media_to_drop = new["media_file"]  # the existing crop wins
    elif new["media_file"] and not old["media_file"]:
        media = new["media_file"]
    row: StoredPost = {
        "id": new["id"] if (new["url"] and not old["url"]) else old["id"],
        "username": old["username"],
        "kind": old["kind"] or new["kind"],
        "posted_date": old["posted_date"] or new["posted_date"],
        "caption": new["caption"]
        if not weak(new["caption"]) and weak(old["caption"] or "")
        else old["caption"],
        "media_file": media,
        "scraped_at": old["scraped_at"],
        "hash": new["hash"],
        "alt_hash": next(
            (h for h in (new["alt_hash"], old["hash"], old["alt_hash"]) if h and h != new["hash"]), None
        ),
        "url": old["url"] or new["url"],
        "place": old["place"] or new["place"],
        "posted_at": old["posted_at"] or new["posted_at"],
        "updated_at": now.isoformat(),
        "ig_version": old["ig_version"] or new["ig_version"],
    }
    return row, media_to_drop


def remember_hash(con: sqlite3.Connection, post_id: str, h: str) -> None:
    """Record `h` as the hash a stored post was just seen under, keeping the one it replaces as
    alt_hash: a post has a collapsed and an expanded caption form, and either may be on screen."""
    con.execute("UPDATE posts SET alt_hash=hash, hash=? WHERE id=? AND hash IS NOT ?", (h, post_id, h))
    con.commit()


def write_merged(con: sqlite3.Connection, old_id: str, row: StoredPost) -> None:
    """Apply a merged_fields() result: replace `old_id`'s row with `row` (whose id may differ)."""
    if row["id"] != old_id:
        con.execute("DELETE FROM posts WHERE id=?", (old_id,))
    insert_row(con, "posts", row, replace=True)


def insert_row(con: sqlite3.Connection, table: str, row: StoredPost | RunRow, replace: bool = False) -> None:
    """INSERT one {column: value} row. Table and column names come from code, never from input."""
    verb: str = "INSERT OR REPLACE" if replace else "INSERT"
    con.execute(
        f"{verb} INTO {table} ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", tuple(row.values())
    )
