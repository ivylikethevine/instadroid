"""The SQLite schema, numbered migrations, runs, accounts, and duplicate-post merging."""

import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TypedDict

from . import common, config, parsing, retention
from .common import log


class PostRow(TypedDict):
    """A posts table row."""

    id: str  # the permalink shortcode, or the card's hash when no permalink was captured
    username: str
    kind: str
    posted_date: str
    caption: str
    media_file: str | None
    scraped_at: str  # first seen
    hash: str  # parsing.post_id() of the card
    url: str | None
    place: str
    posted_at: str | None
    updated_at: str  # last changed (e.g. merged); drives the feed ETag
    ig_version: str | None


def db_init() -> sqlite3.Connection:
    Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.DB_PATH)
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
    # Backfill for rows written before updated_at existed, and a no-op once that's done.
    con.execute("UPDATE posts SET updated_at = scraped_at WHERE updated_at IS NULL")
    con.execute("CREATE INDEX IF NOT EXISTS posts_hash ON posts(hash)")
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
    con.execute("CREATE INDEX IF NOT EXISTS media_post ON media(post_id)")
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
        # The whole table is replaced atomically on every successful refresh (see
        # navigation.refresh_following_list()) rather than upserted row by row, so an unfollow is reflected
        # simply by that username's row no longer existing after the next refresh — every row
        # shares the same updated_at, which also doubles as "when was this list last refreshed."
        # Open failure alerts, one row per kind (see alerts.update()); the feed server shows them.
        """CREATE TABLE IF NOT EXISTS alerts (
            kind TEXT PRIMARY KEY,
            message TEXT NOT NULL,
            raised_at TEXT NOT NULL
        )"""
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS following (
            username TEXT PRIMARY KEY,
            updated_at TEXT NOT NULL
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
    done = con.execute("PRAGMA user_version").fetchone()[0]
    for version, migration in enumerate(MIGRATIONS[done:], start=done + 1):
        migration(con)
        con.execute(f"PRAGMA user_version = {version}")
        con.commit()


def _add_columns(con: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    """ALTER TABLE ADD COLUMN for each {name: type} the table doesn't have yet."""
    existing = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
    for col, kind in columns.items():
        if col not in existing:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {kind}")


def record_run(
    con: sqlite3.Connection,
    started_at: str,
    finished_at: str,
    new_posts: int,
    error: str | None,
    snapshot: Mapping[str, object],
    **stats: object,
) -> None:
    """Insert one runs row. `snapshot` (see device.device_snapshot()) and `stats` are keyed by runs column
    name, so a new metric only needs its column in db_init() and a key in scrape._scrape_feed()'s result."""
    row = {
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


def version_pairs(con: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every redroid image / Instagram build / profile combination this database has run, with its
    run count, clean runs (no error, no warning), posts stored and last run date: the raw data for
    docs/COMPATIBILITY.md (`scraper.py compat`). Runs that never read the device are left out."""
    return con.execute(
        """SELECT redroid_image, ig_version, selector_profile, COUNT(*) AS runs,
                  SUM(error IS NULL AND COALESCE(warning, '') = '') AS clean_runs,
                  SUM(error IS NULL) AS ok_runs, COALESCE(SUM(new_posts), 0) AS new_posts,
                  MAX(started_at) AS last_run
           FROM runs WHERE ig_version IS NOT NULL
           GROUP BY redroid_image, ig_version, selector_profile
           ORDER BY redroid_image, ig_version, selector_profile"""
    ).fetchall()


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
    rows = con.execute(
        "SELECT cards_per_screen, share_captioned, share_complete FROM runs"
        " WHERE error IS NULL AND cards_per_screen IS NOT NULL ORDER BY id DESC LIMIT ?",
        (config.SELECTOR_DRIFT_BASELINE_RUNS,),
    ).fetchall()
    dropped = []
    for label, current, key in (
        ("cards/screen", cards_per_screen, "cards_per_screen"),
        ("captioned", share_captioned, "share_captioned"),
        ("complete", share_complete, "share_complete"),
    ):
        baseline_vals = [r[key] for r in rows if r[key] is not None]
        if len(baseline_vals) < config.SELECTOR_DRIFT_MIN_RUNS:
            continue
        baseline = sum(baseline_vals) / len(baseline_vals)
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
    row = con.execute("SELECT avatar_updated_at FROM accounts WHERE username=?", (username,)).fetchone()
    return common.older_than(row and row["avatar_updated_at"], config.AVATAR_REFRESH_DAYS)


def needs_following_refresh(con: sqlite3.Connection) -> bool:
    """Unlike _needs_avatar_refresh, this is a single global check, not per-account: the whole
    Following list is captured (and replaced) in one pass, so there's one "when was this last
    done" timestamp, not one per row. No rows at all means never successfully refreshed."""
    return common.older_than(
        con.execute("SELECT MAX(updated_at) FROM following").fetchone()[0], config.FOLLOWING_REFRESH_DAYS
    )


def rename_account(con: sqlite3.Connection, old: str, new: str) -> int:
    """Reconcile a followed account's history after it renamed itself: repoint every post from
    `old` to `new` and fold `old`'s accounts row (avatar, stable account_id if set) into `new`.
    There is no detection here — Instagram's numeric user id is never present in the feed's
    accessibility tree, so this is invoked by hand once a rename is noticed. Note this does not
    fix up an existing `?user=old` FreshRSS subscription; re-subscribe under the new username."""
    if old == new:
        return 0
    moved = con.execute("UPDATE posts SET username=? WHERE username=?", (new, old)).rowcount
    old_row = con.execute("SELECT account_id, avatar_file FROM accounts WHERE username=?", (old,)).fetchone()
    new_row = con.execute("SELECT account_id, avatar_file FROM accounts WHERE username=?", (new,)).fetchone()
    account_id = (new_row and new_row["account_id"]) or (old_row and old_row["account_id"])
    avatar_file = (new_row and new_row["avatar_file"]) or (old_row and old_row["avatar_file"])
    dropped_avatar = old_row["avatar_file"] if old_row and old_row["avatar_file"] != avatar_file else None
    con.execute(
        "INSERT INTO accounts (username, account_id, avatar_file) VALUES (?,?,?)"
        " ON CONFLICT(username) DO UPDATE SET account_id=excluded.account_id,"
        " avatar_file=COALESCE(accounts.avatar_file, excluded.avatar_file)",
        (new, account_id, avatar_file),
    )
    con.execute("DELETE FROM accounts WHERE username=?", (old,))
    # Keep the followed-accounts allowlist (if in use) in step with a rename too — otherwise every
    # post from `new` would be filtered out as "not followed" until the next scheduled refresh.
    following_row = con.execute("SELECT updated_at FROM following WHERE username=?", (old,)).fetchone()
    if following_row:
        con.execute(
            "INSERT INTO following (username, updated_at) VALUES (?, ?) ON CONFLICT(username) DO NOTHING",
            (new, following_row["updated_at"]),
        )
        con.execute("DELETE FROM following WHERE username=?", (old,))
    con.commit()
    if dropped_avatar:
        (config.MEDIA_DIR / dropped_avatar).unlink(missing_ok=True)
    return moved


def _safe_parse_posted_at(
    posted_date: str | None, scraped_at_iso: str | None
) -> tuple[datetime | None, int | None]:
    """parsing.parse_posted_at(), tolerant of a malformed/legacy scraped_at that fromisoformat rejects.
    Returns (None, None) instead of raising, so one corrupt row can't abort the whole migration."""
    now = common.parse_iso(scraped_at_iso)
    return (now and posted_date and parsing.parse_posted_at(posted_date, now)) or (None, None)


def _migrate_dedupe(con: sqlite3.Connection) -> None:
    """Migration 1: backfill posted_at for rows written before that column existed, then merge any
    rows parsing.same_post() considers duplicates — the bug that let a card get stored twice when its caption
    hadn't rendered on the first pass. Uses the same merge path as a live scrape. A single corrupt
    row is skipped rather than turned into a permanent boot loop."""
    log("running one-time dedupe migration")
    for r in con.execute("SELECT id, posted_date, scraped_at FROM posts WHERE posted_at IS NULL"):
        posted_at, _ = _safe_parse_posted_at(r["posted_date"], r["scraped_at"])
        if posted_at:
            con.execute("UPDATE posts SET posted_at=? WHERE id=?", (posted_at.isoformat(), r["id"]))
    con.commit()
    merged = 0
    for (rid,) in con.execute("SELECT id FROM posts ORDER BY scraped_at").fetchall():
        r = con.execute("SELECT * FROM posts WHERE id=?", (rid,)).fetchone()
        if r is None:
            continue  # already merged away as another row's duplicate
        try:
            posted_at, precision = _safe_parse_posted_at(r["posted_date"], r["scraped_at"])
            dup = find_duplicate(con, r["username"], posted_at, precision, r["caption"], exclude_id=r["id"])
            if not dup:
                continue
            row, media_to_drop = merged_fields(dup, dict(r), datetime.now(UTC))  # type: ignore[arg-type]
            retention.discard_media(media_to_drop)
            if row["id"] != r["id"]:
                con.execute("DELETE FROM posts WHERE id=?", (r["id"],))
            write_merged(con, dup["id"], row)
            merged += 1
        except Exception as e:  # a single corrupt/unexpected row must not block every future start
            log(f"WARN: dedupe migration skipped row {rid!r}:", repr(e))
    log(f"dedupe migration: merged {merged} duplicate row(s)")


def _migrate_accounts(con: sqlite3.Connection) -> None:
    """Migration 2: give every username already in posts an accounts row, so avatar capture and
    rename_account() have something to attach to for accounts seen before that table existed."""
    log("running one-time accounts backfill")
    for (username,) in con.execute("SELECT DISTINCT username FROM posts").fetchall():
        try:
            con.execute("INSERT OR IGNORE INTO accounts (username) VALUES (?)", (username,))
        except Exception as e:  # a single bad username must not block every future start
            log(f"WARN: accounts backfill skipped {username!r}:", repr(e))


def _migrate_story_retention(con: sqlite3.Connection) -> None:
    """Migration 3: drop stories.expires_at now that stories share RETAIN_DAYS with posts."""
    cols = {r["name"] for r in con.execute("PRAGMA table_info(stories)")}
    if "expires_at" in cols:
        log("running one-time story-retention migration")
        con.execute("DROP INDEX IF EXISTS stories_expires_at")
        con.execute("ALTER TABLE stories DROP COLUMN expires_at")


MIGRATIONS = (_migrate_dedupe, _migrate_accounts, _migrate_story_retention)


def find_duplicate(
    con: sqlite3.Connection,
    username: str,
    posted_at: datetime | None,
    posted_at_prec: int | None,
    caption: str,
    exclude_id: str | None = None,
) -> sqlite3.Row | None:
    """Look up a stored row that parsing.same_post() considers the same post as this freshly-parsed
    card, within a coarse SQL time window (same_post itself applies the exact tolerance)."""
    if posted_at is None:
        return None
    window = timedelta(seconds=max(posted_at_prec or 0, 3600) * 2)
    candidate = {
        "username": username,
        "caption": caption,
        "posted_at": posted_at,
        "posted_at_precision": posted_at_prec,
    }
    for r in con.execute(
        "SELECT * FROM posts WHERE username=? AND posted_at BETWEEN ? AND ?",
        (username, (posted_at - window).isoformat(), (posted_at + window).isoformat()),
    ):
        if exclude_id and r["id"] == exclude_id:
            continue
        existing = {
            "username": r["username"],
            "caption": r["caption"],
            "posted_at": datetime.fromisoformat(r["posted_at"]) if r["posted_at"] else None,
        }
        if parsing.same_post(existing, candidate):
            return r
    return None


def merged_fields(existing: sqlite3.Row, new: PostRow, now: datetime) -> tuple[PostRow, str | None]:
    """The row that should replace `existing` (a stored posts row) once `new` turns out to be the
    same post: keep a permalink id/url over a hash id, a real caption over a weak placeholder, the
    first-seen scraped_at and ig_version, and whichever media crop already exists. Returns (row,
    media file to delete). `now` becomes updated_at, so the feed's ETag notices the merge."""
    weak = parsing.is_weak_caption
    media, media_to_drop = existing["media_file"], None
    if new["media_file"] and existing["media_file"] and new["media_file"] != existing["media_file"]:
        media_to_drop = new["media_file"]  # the existing crop wins
    elif new["media_file"] and not existing["media_file"]:
        media = new["media_file"]
    row: PostRow = {
        "id": new["id"] if (new["url"] and not existing["url"]) else existing["id"],
        "username": existing["username"],
        "kind": existing["kind"] or new["kind"],
        "posted_date": existing["posted_date"] or new["posted_date"],
        "caption": new["caption"]
        if not weak(new["caption"]) and weak(existing["caption"] or "")
        else existing["caption"],
        "media_file": media,
        "scraped_at": existing["scraped_at"],
        "hash": new["hash"],
        "url": existing["url"] or new["url"],
        "place": existing["place"] or new["place"],
        "posted_at": existing["posted_at"] or new["posted_at"],
        "updated_at": now.isoformat(),
        "ig_version": existing["ig_version"] or new["ig_version"],
    }
    return row, media_to_drop


def write_merged(con: sqlite3.Connection, old_id: str, row: PostRow) -> None:
    """Apply a merged_fields() result: replace `old_id`'s row with `row` (whose id may differ)."""
    if row["id"] != old_id:
        con.execute("DELETE FROM posts WHERE id=?", (old_id,))
    insert_row(con, "posts", row, replace=True)


def insert_row(con: sqlite3.Connection, table: str, row: Mapping[str, object], replace: bool = False) -> None:
    """INSERT one {column: value} row. Table and column names come from code, never from input."""
    verb = "INSERT OR REPLACE" if replace else "INSERT"
    con.execute(
        f"{verb} INTO {table} ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", tuple(row.values())
    )
