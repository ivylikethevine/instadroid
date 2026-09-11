"""
Instagram -> SQLite scraper driving a real Instagram app inside a redroid container via uiautomator2.

Strategy: open the chronological "Following" feed, scroll slowly, parse the accessibility
tree for post cards, store new ones, stop once we hit posts we've already seen.

Selectors live in the SELECTORS dict below. Instagram changes its UI a few times a year;
when a run fails, look at the hierarchy dump in $DEBUG_DIR and adjust them.
"""

import hashlib
import math
import os
import random
import re
import sqlite3
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import adbutils
import uiautomator2 as u2
from lxml import etree
from PIL import Image
from uiautomator2.exceptions import DeviceError as U2DeviceError

ADB_ADDR = os.environ.get("ADB_ADDR", "127.0.0.1:5555")  # redroid's forwarded ADB port
DB_PATH = os.environ.get("DB_PATH", "/db/posts.sqlite")
MEDIA_DIR = Path(os.environ.get("MEDIA_DIR", "/media"))
DEBUG_DIR = Path(os.environ.get("DEBUG_DIR", "/debug"))
POLL_MIN_H = float(os.environ.get("POLL_MIN_HOURS", "2.5"))
POLL_MAX_H = float(os.environ.get("POLL_MAX_HOURS", "4.5"))
MAX_SCROLLS = int(os.environ.get("MAX_SCROLLS", "25"))
STOP_AFTER_SEEN = int(os.environ.get("STOP_AFTER_SEEN", "4"))
RETAIN_DAYS = int(os.environ.get("RETAIN_DAYS", "60"))  # 0 disables deletion
# How long the swipe gesture itself takes (a fling scrolls several screens and skips posts) and
# how long to sit idle between scrolls (both randomized within their range, like a human thumb).
SCROLL_SWIPE_MIN = float(os.environ.get("SCROLL_SWIPE_MIN", "0.6"))
SCROLL_SWIPE_MAX = float(os.environ.get("SCROLL_SWIPE_MAX", "1.0"))
SCROLL_PAUSE_MIN = float(os.environ.get("SCROLL_PAUSE_MIN", "1.5"))
SCROLL_PAUSE_MAX = float(os.environ.get("SCROLL_PAUSE_MAX", "4.0"))
MEDIA_QUALITY = int(os.environ.get("MEDIA_QUALITY", "95"))  # JPEG quality for saved post crops
IG_USERNAME = os.environ.get("IG_USERNAME", "")
IG_PASSWORD = os.environ.get("IG_PASSWORD", "")
IG_PKG = "com.instagram.android"
DEBUG_KEEP = 12  # debug dump pairs to retain; older ones are pruned on every new dump
DEBUG_RETAIN_DAYS = float(os.environ.get("DEBUG_RETAIN_DAYS", "7"))  # 0 disables age-based pruning
_DEBUG_ARTIFACT_SUFFIXES = (".xml", ".jpg", ".png")
PERMALINK_RETRIES = int(os.environ.get("PERMALINK_RETRIES", "2"))  # extra share-sheet passes after the first
SHARE_TAP_TRIES = int(os.environ.get("SHARE_TAP_TRIES", "2"))  # taps on the share button before giving up
CLIPBOARD_TIMEOUT = float(os.environ.get("CLIPBOARD_TIMEOUT", "6.0"))  # seconds to poll the clipboard for
MAX_CAROUSEL_SLIDES = int(os.environ.get("MAX_CAROUSEL_SLIDES", "10"))
VIDEO_SETTLE_SECONDS = float(os.environ.get("VIDEO_SETTLE_SECONDS", "1.5"))  # let autoplay/overlay settle
AVATAR_REFRESH_DAYS = int(os.environ.get("AVATAR_REFRESH_DAYS", "14"))
MEDIA_MAX_MB = float(os.environ.get("MEDIA_MAX_MB", "0"))  # 0 disables the size-based retention cap
MAX_STORIES_PER_RUN = int(os.environ.get("MAX_STORIES_PER_RUN", "10"))
STORY_RETAIN_HOURS = int(os.environ.get("STORY_RETAIN_HOURS", "24"))  # matches Instagram's own expiry
TIME_DISTRIBUTION = os.environ.get("TIME_DISTRIBUTION", "uniform")  # uniform | lognormal | daynight
# "Local" time for the daynight distribution below — deliberately not applied anywhere by default
# (empty = leave the device's own clock/timezone alone). Set this to match wherever the account's
# network traffic appears to originate; see tune-android.sh, which applies the same value to the
# device itself via `service call alarm`, and README's "Fingerprint consistency" for why a mismatch
# between the two (or with a proxy/VPN's egress, once that exists) is worse than setting neither.
DEVICE_TIMEZONE = os.environ.get("DEVICE_TIMEZONE", "")
DAYNIGHT_QUIET_START = int(os.environ.get("DAYNIGHT_QUIET_START", "0"))  # local hour, inclusive
DAYNIGHT_QUIET_END = int(os.environ.get("DAYNIGHT_QUIET_END", "6"))  # local hour, exclusive
# Consecutive screens with no identifiable post before a run treats the feed as lost: dump, reopen
# the feed once, and stop the run if it happens again. 0 disables.
EMPTY_SCREEN_LIMIT = int(os.environ.get("EMPTY_SCREEN_LIMIT", "3"))
# Minutes to wait before retrying after a transient device failure (see is_transient()) instead of
# sleeping a whole poll interval — one entry per retry, comma-separated; empty disables.
RETRY_DELAYS_MINUTES = [
    float(x) for x in os.environ.get("RETRY_DELAYS_MINUTES", "2,5,15").split(",") if x.strip()
]

# --- Selectors (the fragile part) ---------------------------------------------------
SELECTORS = {
    # Every feed card has a header ViewGroup whose content-desc reads e.g.
    #   "nykkyhex posted a video in Rich's San Diego 21 hours ago"
    #   "clubsabbat posted a carousel in San Diego, California 3 days ago"
    #   "someone posted a photo August 29"
    "header_id": "row_feed_profile_header",
    "header_desc": re.compile(
        r"^(?P<user>[\w.]+) posted (?:an? )?(?P<kind>\w+)(?: in (?P<place>.+?))?"
        r" (?P<date>\d+ (?:second|minute|hour|day|week)s? ago|[A-Z][a-z]+ \d{1,2}(?:, \d{4})?|Yesterday)$"
    ),
    # Resource-id substrings marking the media area of a card (used for the screenshot crop).
    "media_ids": (
        "carousel_media_group",
        "media_group",
        "row_feed_photo_imageview",
        "zoomable_view_container",
    ),
    # Content-desc on the media itself ("Reel by Nykky Hex, Liked by ..., August 29" / "Photo 1 of 7 by ...").
    "media_alt": re.compile(r"^(Photo|Video|Reel|Image|Carousel)\b", re.I),
    "username_id": "row_feed_photo_profile_name",
    # Caption widget ("<user> text… more"), share button, and the share sheet's Copy link entry.
    "caption_class": "com.instagram.ui.widget.textview.IgTextLayoutView",
    "timestamp": re.compile(
        r"^(\d+ (?:second|minute|hour|day|week)s? ago|[A-Z][a-z]+ \d{1,2}(?:, \d{4})?|Yesterday)$"
    ),
    # Map the media description's leading word to the header's kind vocabulary.
    "alt_kind": {
        "reel": "video",
        "video": "video",
        "photo": "photo",
        "image": "photo",
        "carousel": "carousel",
    },
    "share_id": "row_feed_button_share",
    "copy_link_desc": "Copy link",
    # The Home feed's story tray (not present on the Following screen). Each item's content-desc
    # is "<user>'s story, <index> of <total>, Unseen."/"...Seen." — index 0 is always the logged-in
    # account's own story.
    "story_tray_id": "reels_tray_container",
    "story_item_desc": re.compile(
        r"^(?P<user>[\w.]+)'s story, (?P<index>\d+) of (?P<total>\d+), (?P<seen>\w+)\.$"
    ),
    "story_viewer_id": "reel_viewer_root",
    "story_media_id": "reel_viewer_media_container",
    # The gradient behind the username/timestamp header, overlaid on the media itself — its bottom
    # edge is where the crop should start, so the saved image doesn't bake in timestamp text that
    # changes hour to hour (see capture_story_media()).
    "story_shadow_id": "reel_viewer_top_shadow",
    "story_timestamp_id": "reel_viewer_timestamp",
    # Anything that means a share/bottom sheet is open. We never interact inside one except to
    # tap "Copy link"; a stray tap there could message a contact.
    "sheet_markers_text": ["Write a message…"],
    "sheet_markers_desc": ["New group"],
    "permalink": re.compile(r"https://www\.instagram\.com/(?P<type>p|reel|reels|tv)/(?P<code>[\w-]+)"),
    # The "Home ⌄" title button at the top of the feed opens the Following/Favorites chooser.
    "feed_switcher_desc": "Instagram Home Feed",
    "following_text": "Following",
    # The Following feed is its own screen: Back button + action_bar_title "Following".
    "following_title_id": "action_bar_title",
    # Login screen. Instagram renames these occasionally; several candidates each.
    "login_username_ids": ["login_username", "username"],
    "login_username_hints": [
        "Mobile number or email",
        "Username, email or mobile number",
        "Phone number, username or email",
        "Username, email address or mobile number",
    ],
    "login_password_ids": ["password"],
    "login_password_hints": ["Password"],
    "login_button_texts": ["Log in", "Log In"],
    "login_page_markers": ["Log in", "Log In", "Forgot password?"],
    # The logged-out "Join Instagram" welcome screen (shown before the actual login form, e.g.
    # after a fresh install or an invalidated session) has neither a login form nor the markers
    # above, so it must be detected and tapped through separately.
    "welcome_existing_profile_text": "I already have a profile",
    # Post-login interstitials and the buttons that dismiss them.
    "dismiss_texts": ["Not now", "Not Now", "Skip", "Save", "Continue", "Don’t allow", "Cancel", "OK"],
    # Anything matching these means a human has to intervene.
    "challenge_texts": [
        "confirmation code",
        "Confirm it's you",
        "Suspicious login",
        "security code",
        "Enter the code",
        "We Detected An Unusual Login",
        "Help us confirm it's you",
    ],
}
# ------------------------------------------------------------------------------------

# A caption is "weak" when it's really just the media description Instagram shows before the
# real caption has rendered ("Photo 1 of 2 by X, 113 likes, 10 comments"), or empty. Two cards
# with a weak caption on either side are treated as the same post if the time/author also match;
# real, differing captions never are. See same_post().
_WEAK_CAPTION = re.compile(r"^(Photo|Video|Reel|Image|Carousel)\b.*\bby\b", re.I)

_RELATIVE_AGO = re.compile(r"^(\d+) (second|minute|hour|day|week)s? ago$")
_ABSOLUTE_DATE = re.compile(r"^([A-Z][a-z]+) (\d{1,2})(?:, (\d{4}))?$")
_UNIT_SECONDS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 604800}


def log(*a):
    print(datetime.now().strftime("%H:%M:%S"), *a, flush=True)


class DeviceNotReady(RuntimeError):
    """The device or app isn't in a state to be driven yet (e.g. Instagram won't come to the
    foreground). Unlike a login challenge, a retry a few minutes later usually just works."""


def is_transient(e: BaseException) -> bool:
    """Device-side failures that a short wait usually fixes: redroid still booting, adb briefly
    offline, or the uiautomator server failing to attach to an accessibility manager that isn't up
    yet (seen live as LaunchUiAutomationError 'server quit unexpectly', five runs in a row). Login
    challenges and parsing/logic errors are deliberately not transient: retrying those early either
    can't help or, for a challenge, looks worse to Instagram."""
    return isinstance(e, (DeviceNotReady, adbutils.AdbError, adbutils.AdbTimeout, U2DeviceError))


def next_sleep_seconds(error: BaseException | None, attempt: int) -> tuple[float, int]:
    """(seconds to sleep before the next run, updated retry count). A transient failure retries
    after RETRY_DELAYS_MINUTES[attempt] (jittered up to +50%) until the list runs out; anything
    else — success, a non-transient error, retries exhausted — sleeps a normal poll interval."""
    if error is not None and is_transient(error) and attempt < len(RETRY_DELAYS_MINUTES):
        minutes = RETRY_DELAYS_MINUTES[attempt]
        return sample_duration(minutes, minutes * 1.5) * 60, attempt + 1
    return sample_duration(POLL_MIN_H, POLL_MAX_H) * 3600, 0


def parse_posted_at(text: str, now: datetime) -> tuple[datetime, int] | None:
    """Convert a header/timestamp string ("3 days ago", "August 29", "Yesterday") into an
    absolute UTC instant plus the granularity of that instant in seconds (e.g. 3600 for an
    hours-ago value, 86400 for a bare date). Returns None if the text isn't a format we know."""
    if not text:
        return None
    text = text.strip()
    if text == "Yesterday":
        return now - timedelta(days=1), 86400
    if m := _RELATIVE_AGO.match(text):
        unit = m.group(2)
        seconds = _UNIT_SECONDS[unit]
        return now - timedelta(seconds=int(m.group(1)) * seconds), seconds
    if m := _ABSOLUTE_DATE.match(text):
        try:
            month = datetime.strptime(m.group(1), "%B").month
        except ValueError:
            return None
        year = int(m.group(3)) if m.group(3) else now.year
        try:
            dt = datetime(year, month, int(m.group(2)), tzinfo=UTC)
        except ValueError:
            return None
        if not m.group(3) and dt > now:  # bare "Month Day" with no year: assume the past
            try:
                dt = dt.replace(year=year - 1)
            except ValueError:  # "February 29" rolled back into a non-leap year
                return None
        return dt, 86400
    return None


def _is_weak_caption(caption: str) -> bool:
    return not caption or bool(_WEAK_CAPTION.match(caption))


def same_post(existing: dict, candidate: dict) -> bool:
    """True if `existing` (a stored post: username, caption, posted_at) and `candidate` (a
    freshly parsed card: username, caption, posted_at, posted_at_precision) are the same
    Instagram post seen twice — typically because a card was captured before its caption widget
    had rendered, so it got identified by the media description instead. Requires the same
    author and posted times within the candidate's own granularity (floor 1h); captions must
    then agree, or one side must be a weak/placeholder caption."""
    if existing["username"] != candidate["username"]:
        return False
    ea, ca = existing.get("posted_at"), candidate.get("posted_at")
    if ea is None or ca is None:
        return False
    tolerance = max(candidate.get("posted_at_precision") or 0, 3600)
    if abs((ea - ca).total_seconds()) > tolerance:
        return False
    ecap, ccap = existing.get("caption") or "", candidate.get("caption") or ""
    if _is_weak_caption(ecap) or _is_weak_caption(ccap):
        return True
    return ecap[:200] == ccap[:200]


def db_init():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
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
    cols = {r["name"] for r in con.execute("PRAGMA table_info(posts)")}
    for col in ("hash", "url", "place", "posted_at", "updated_at"):
        if col not in cols:
            con.execute(f"ALTER TABLE posts ADD COLUMN {col} TEXT")
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
        # id is a content hash of the captured crop (see capture_story_media()) — stories have no
        # public permalink/shortcode the way posts do, so there's no other stable identity to key
        # on. No pre-existing data to migrate: this table starts empty on every DB.
        """CREATE TABLE IF NOT EXISTS stories (
            id TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            media_file TEXT,
            kind TEXT,
            posted_date TEXT,
            scraped_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )"""
    )
    con.execute("CREATE INDEX IF NOT EXISTS stories_username ON stories(username)")
    con.execute("CREATE INDEX IF NOT EXISTS stories_expires_at ON stories(expires_at)")
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
    run_cols = {r["name"] for r in con.execute("PRAGMA table_info(runs)")}
    for col in ("link_sheet_failures", "link_clipboard_failures", "new_stories"):
        if col not in run_cols:
            con.execute(f"ALTER TABLE runs ADD COLUMN {col} INTEGER")
    for col in ("warning", "ig_version", "redroid_image"):
        if col not in run_cols:
            con.execute(f"ALTER TABLE runs ADD COLUMN {col} TEXT")
    con.commit()
    _migrate_dedupe(con)
    _migrate_accounts(con)
    return con


def _device_snapshot(d) -> dict:
    """Best-effort device/app versions for the runs table and status page: ro.build.* props, the
    installed Instagram versionName, and the redroid image tag compose passes in — so "which
    Instagram update broke the selectors" is a lookup. Uses plain adb shell calls rather than
    uiautomator2's jsonrpc info, so a wedged automation service can't also blank this out."""

    def prop(name):
        try:
            return d.shell(f"getprop {name}").output.strip() or None
        except Exception:
            return None

    def ig_version():
        try:
            m = re.search(r"versionName=(\S+)", d.shell(["dumpsys", "package", IG_PKG]).output or "")
        except Exception:
            return None
        return m.group(1) if m else None

    return {
        "android_release": prop("ro.build.version.release"),
        "android_sdk": prop("ro.build.version.sdk"),
        "device_product": prop("ro.product.name") or prop("ro.build.product"),
        "ig_version": ig_version(),
        "redroid_image": os.environ.get("REDROID_IMAGE") or None,
    }


def record_run(
    con,
    started_at,
    finished_at,
    new_posts,
    error,
    snapshot,
    link_sheet_failures=0,
    link_clipboard_failures=0,
    new_stories=0,
    warning=None,
):
    con.execute(
        "INSERT INTO runs (started_at, finished_at, new_posts, error, android_release, android_sdk,"
        " device_product, link_sheet_failures, link_clipboard_failures, new_stories, warning, ig_version,"
        " redroid_image) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            started_at,
            finished_at,
            new_posts,
            error,
            snapshot.get("android_release"),
            snapshot.get("android_sdk"),
            snapshot.get("device_product"),
            link_sheet_failures,
            link_clipboard_failures,
            new_stories,
            warning,
            snapshot.get("ig_version"),
            snapshot.get("redroid_image"),
        ),
    )
    con.commit()


def _upsert_account(con, username: str, avatar_file: str | None = None):
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


def _needs_avatar_refresh(con, username: str) -> bool:
    row = con.execute("SELECT avatar_updated_at FROM accounts WHERE username=?", (username,)).fetchone()
    if not row or not row["avatar_updated_at"]:
        return True
    try:
        updated = datetime.fromisoformat(row["avatar_updated_at"])
    except ValueError:
        return True
    return datetime.now(UTC) - updated > timedelta(days=AVATAR_REFRESH_DAYS)


def rename_account(con, old: str, new: str) -> int:
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
    con.commit()
    if dropped_avatar:
        (MEDIA_DIR / dropped_avatar).unlink(missing_ok=True)
    return moved


def _safe_parse_posted_at(posted_date, scraped_at_iso):
    """parse_posted_at(), tolerant of a malformed/legacy scraped_at that fromisoformat rejects.
    Returns (None, None) instead of raising, so one corrupt row can't abort the whole migration."""
    try:
        now = datetime.fromisoformat(scraped_at_iso)
    except TypeError, ValueError:
        return None, None
    return parse_posted_at(posted_date, now) or (None, None)


def _migrate_dedupe(con):
    """One-time cleanup, guarded by PRAGMA user_version so it runs exactly once: backfill
    posted_at for rows written before that column existed, then merge any rows same_post()
    considers duplicates — the bug that let a card get stored twice when its caption hadn't
    rendered on the first pass. Uses the same merge path as a live scrape (_find_duplicate /
    _merged_fields / _write_merged). Each row is handled defensively: a single corrupt row must
    not turn into a permanent boot loop (user_version is only bumped once every row is done)."""
    if con.execute("PRAGMA user_version").fetchone()[0] >= 1:
        return
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
            dup = _find_duplicate(con, r["username"], posted_at, precision, r["caption"], exclude_id=r["id"])
            if not dup:
                continue
            final_id, fields, media_to_drop = _merged_fields(
                dup,
                r["id"],
                r["url"],
                r["hash"],
                r["kind"],
                r["posted_date"],
                r["place"],
                r["caption"],
                r["media_file"],
                posted_at,
                datetime.now(UTC),
            )
            if media_to_drop:
                (MEDIA_DIR / media_to_drop).unlink(missing_ok=True)
            if final_id != r["id"]:
                con.execute("DELETE FROM posts WHERE id=?", (r["id"],))
            _write_merged(con, dup["id"], final_id, fields)
            merged += 1
        except Exception as e:  # a single corrupt/unexpected row must not block every future start
            log(f"WARN: dedupe migration skipped row {rid!r}:", repr(e))
    con.execute("PRAGMA user_version = 1")
    con.commit()
    log(f"dedupe migration: merged {merged} duplicate row(s)")


def _migrate_accounts(con):
    """One-time backfill, guarded like _migrate_dedupe(): give every username already in posts an
    accounts row, so avatar capture and rename_account() have something to attach to for accounts
    seen before this table existed. No posts.media_file/media data is touched."""
    if con.execute("PRAGMA user_version").fetchone()[0] >= 2:
        return
    log("running one-time accounts backfill")
    for (username,) in con.execute("SELECT DISTINCT username FROM posts").fetchall():
        try:
            con.execute("INSERT OR IGNORE INTO accounts (username) VALUES (?)", (username,))
        except Exception as e:  # a single bad username must not block every future start
            log(f"WARN: accounts backfill skipped {username!r}:", repr(e))
    con.execute("PRAGMA user_version = 2")
    con.commit()
    log("accounts backfill complete")


def _find_duplicate(con, username, posted_at, posted_at_prec, caption, exclude_id=None):
    """Look up a stored row that same_post() considers the same post as this freshly-parsed
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
        if same_post(existing, candidate):
            return r
    return None


def _merged_fields(existing, pid, url, h, kind, posted_date, place, caption, media, posted_at, now):
    """Compute the row that should replace `existing` (a sqlite3.Row from posts) once a
    duplicate for the same post is found: keep the permalink id/url over a hash id, a real
    caption over a weak/placeholder one, and whichever media crop already exists. Returns
    (final_id, fields_dict, media_to_drop) — the caller deletes media_to_drop and applies the
    write via _write_merged. `now` becomes the row's updated_at, so the feed's ETag notices the
    merge even though scraped_at (when it was first seen) doesn't change."""
    final_id = pid if (url and not existing["url"]) else existing["id"]
    final_caption = (
        caption
        if not _is_weak_caption(caption) and _is_weak_caption(existing["caption"] or "")
        else existing["caption"]
    )
    final_media, media_to_drop = existing["media_file"], None
    if media and existing["media_file"] and media != existing["media_file"]:
        media_to_drop = media  # existing crop wins; the new one is redundant
    elif media and not existing["media_file"]:
        final_media = media
    fields = {
        "username": existing["username"],
        "kind": existing["kind"] or kind,
        "posted_date": existing["posted_date"] or posted_date,
        "caption": final_caption,
        "media_file": final_media,
        "scraped_at": existing["scraped_at"],
        "hash": h,
        "url": existing["url"] or url,
        "place": existing["place"] or place,
        "posted_at": existing["posted_at"] or (posted_at.isoformat() if posted_at else None),
        "updated_at": now.isoformat(),
    }
    return final_id, fields, media_to_drop


def _write_merged(con, old_id, final_id, fields):
    """Apply a _merged_fields() result: replace `old_id`'s row with one at `final_id`."""
    if final_id != old_id:
        con.execute("DELETE FROM posts WHERE id=?", (old_id,))
    cols = ["id", *fields.keys()]
    con.execute(
        f"INSERT OR REPLACE INTO posts ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
        (final_id, *fields.values()),
    )


def connect_device():
    log("connecting to", ADB_ADDR)
    adbutils.adb.connect(ADB_ADDR, timeout=30)
    d = u2.connect(ADB_ADDR)
    d.implicitly_wait(10)
    log("device:", d.info.get("productName"), d.window_size())
    return d


def _launch_app(d):
    """Bring IG_PKG to the foreground. uiautomator2's app_start() defaults to `monkey -c
    LAUNCHER` when no activity is given, which on this device silently no-ops (exit code 251,
    launcher stays focused) — Instagram ships many enabled/disabled activity-aliases for seasonal
    icon themes (`.activity.MainTabActivity.kpop`, `.flame`, `.slime`, ...), and category-based
    resolution (`am start -c LAUNCHER` too) can't disambiguate them. `pm resolve-activity` returns
    the one actually enabled, so start that explicit component instead — falling back to monkey
    only if resolution itself fails."""
    try:
        out = d.shell(["cmd", "package", "resolve-activity", "--brief", IG_PKG]).output
        activity = out.strip().splitlines()[-1].split("/", 1)[1]
        d.app_start(IG_PKG, activity=activity, stop=False)
    except Exception as e:
        log(f"WARN: resolve-activity failed ({e!r}); falling back to monkey launch")
        d.app_start(IG_PKG, stop=False)


def _in_quiet_hours(now: datetime) -> bool:
    """True during the configured local quiet window (only consulted by the "daynight"
    distribution below). Uses DEVICE_TIMEZONE so "local" reflects the account's apparent timezone,
    not the container's own clock, which stays UTC regardless of DEVICE_TIMEZONE."""
    tz = ZoneInfo(DEVICE_TIMEZONE) if DEVICE_TIMEZONE else UTC
    local_hour = now.astimezone(tz).hour
    if DAYNIGHT_QUIET_START <= DAYNIGHT_QUIET_END:
        return DAYNIGHT_QUIET_START <= local_hour < DAYNIGHT_QUIET_END
    return local_hour >= DAYNIGHT_QUIET_START or local_hour < DAYNIGHT_QUIET_END  # wraps midnight


def sample_duration(lo: float, hi: float, now: datetime | None = None) -> float:
    """Draw a duration in [lo, hi] per TIME_DISTRIBUTION. Used for every pause (human_pause,
    human_scroll's swipe duration) and the inter-run poll interval — the two things a
    timing-analysis detector could actually observe, per the roadmap's "configurable time-fuzzing".

      - "uniform" (default): random.uniform(lo, hi) — the original, unchanged behavior.
      - "lognormal": a heavier-tailed, more human-like shape than a flat range — most draws cluster
        near the midpoint, with an occasional longer outlier, instead of every value in [lo, hi]
        being equally likely.
      - "daynight": like "lognormal", but during DAYNIGHT_QUIET_START..DAYNIGHT_QUIET_END local
        hours (default 0-6, i.e. "asleep") the top of the range is stretched, so activity actually
        thins out overnight instead of keeping the same rhythm around the clock.
    """
    if TIME_DISTRIBUTION == "uniform" or hi <= lo:
        return random.uniform(lo, hi)
    effective_hi = hi
    if TIME_DISTRIBUTION == "daynight" and _in_quiet_hours(now or datetime.now(UTC)):
        effective_hi = hi + (hi - lo)
    mid = (lo + effective_hi) / 2
    mu, sigma = math.log(max(mid, 1e-6)), 0.5
    for _ in range(8):  # resample a rare out-of-range draw rather than bias the shape by clipping
        v = random.lognormvariate(mu, sigma)
        if lo <= v <= effective_hi:
            return v
    return min(max(v, lo), effective_hi)  # give up after 8 tries, clip instead


def human_pause(lo=1.0, hi=3.0):
    time.sleep(sample_duration(lo, hi))


def human_scroll(d):
    """Scroll up by a random amount at a random speed, like a thumb would."""
    w, h = d.window_size()
    x = random.randint(int(w * 0.3), int(w * 0.7))
    y1 = random.randint(int(h * 0.65), int(h * 0.8))
    y2 = y1 - random.randint(int(h * 0.3), int(h * 0.45))
    # Slow enough not to fling: a fling scrolls several screens and skips whole posts.
    d.swipe(x, y1, x, y2, duration=sample_duration(SCROLL_SWIPE_MIN, SCROLL_SWIPE_MAX))


def _first(d, **kinds):
    """Return the first existing selector among the given candidate lists."""
    for kind, values in kinds.items():
        for v in values:
            sel = d(**{kind: v})
            if sel.exists(timeout=1):
                return sel
    return None


def _challenge_present(d):
    for t in SELECTORS["challenge_texts"]:
        if d(textContains=t).exists(timeout=0.5):
            return t
    return None


def _prune_debug_dumps():
    """Keep only the newest DEBUG_KEEP hierarchy+screenshot pairs, and delete any debug artifact
    (.xml/.jpg/.png, top level only) older than DEBUG_RETAIN_DAYS — manual dumps and one-off
    screenshots don't belong to a pair and otherwise never age out. Anything else in DEBUG_DIR
    (e.g. a scratch test*.sqlite a DB_PATH may still point at) is left alone. Best-effort: a file
    that can't be removed is logged and skipped, never raised."""
    try:
        doomed = []
        hierarchies = sorted(DEBUG_DIR.glob("*_hierarchy.xml"), key=lambda p: p.stat().st_mtime)
        for old in hierarchies[:-DEBUG_KEEP]:
            stem = old.name.removesuffix("_hierarchy.xml")
            doomed += [old, DEBUG_DIR / f"{stem}_screen.jpg", DEBUG_DIR / f"{stem}_screen.png"]
        if DEBUG_RETAIN_DAYS > 0:
            cutoff = time.time() - DEBUG_RETAIN_DAYS * 86400
            doomed += [
                f
                for f in DEBUG_DIR.iterdir()
                if f.suffix.lower() in _DEBUG_ARTIFACT_SUFFIXES and f.is_file() and f.stat().st_mtime < cutoff
            ]
    except OSError as e:  # e.g. DEBUG_DIR doesn't exist yet, or a file vanished mid-scan
        log("WARN: could not scan debug dumps for pruning:", repr(e))
        return
    for f in doomed:
        try:
            f.unlink(missing_ok=True)
        except OSError as e:
            log(f"WARN: could not prune debug file {f.name!r}:", repr(e))


def _dump_debug(d, name, xml=None):
    """Save a hierarchy + screenshot pair for later inspection. Pass `xml` when the caller
    already has a fresh dump, to avoid a redundant device round-trip. Best-effort: a debug dump is
    a diagnostic aid, not part of the scrape itself, so a write failure here (e.g. a stale file
    left owned by a different uid from a `docker exec -u root` session) must not crash the whole
    run — it just means this one dump is missing from $DEBUG_DIR."""
    try:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        (DEBUG_DIR / f"{name}_hierarchy.xml").write_text(xml if xml is not None else d.dump_hierarchy())
        d.screenshot().convert("RGB").save(DEBUG_DIR / f"{name}_screen.jpg", quality=70)
        _prune_debug_dumps()
    except OSError as e:
        log(f"WARN: could not write debug dump {name!r}:", repr(e))


def _dismiss_interstitials(d, rounds=4):
    for _ in range(rounds):
        btn = _first(d, text=SELECTORS["dismiss_texts"])
        if not btn:
            return
        btn.click()
        human_pause(1.5, 3)


def _login_form(d):
    """Return (username_field, password_field) or None. Instagram's login screen is Jetpack
    Compose: the labels are plain Views and the two EditTexts carry no id, so we go by order."""
    if not _first(d, text=SELECTORS["login_username_hints"]) and not _first(
        d, text=SELECTORS["login_password_hints"]
    ):
        return None
    edits = d(className="android.widget.EditText")
    if edits.count < 2:
        return None
    return edits[0], edits[1]


def ensure_logged_in(d):
    """If the login screen is showing, fill credentials from the environment and log in.

    Returns True if we are (or became) logged in. Raises RuntimeError on a 2FA/challenge
    screen so the caller can abort and a human can finish it.
    """
    if IG_PKG not in d.app_list():
        raise RuntimeError(f"{IG_PKG} is not installed on the device; adb install it first")
    _launch_app(d)
    human_pause(4, 6)
    for attempt in range(3):
        if d.app_current().get("package") == IG_PKG:
            break
        log(f"WARN: {IG_PKG} not foregrounded yet (attempt {attempt}); retrying launch")
        _launch_app(d)
        human_pause(3, 5)
    else:
        # Without this check, every screen-detection call below trivially finds nothing (we're
        # still on the home screen) and the function falls through to "no login screen; assume
        # session is live" — a false positive that leaves the caller thinking it's logged in.
        _dump_debug(d, "login")
        raise DeviceNotReady(f"could not bring {IG_PKG} to the foreground; see {DEBUG_DIR}")
    ok = d(text="OK")  # stray "Enter your password" style alert from a previous attempt
    if ok.exists(timeout=1):
        ok.click()
        human_pause()
    if c := _challenge_present(d):
        _dump_debug(d, "login")
        raise RuntimeError(f"Instagram wants a human: '{c}' screen; see {DEBUG_DIR}")
    existing = d(text=SELECTORS["welcome_existing_profile_text"])
    if existing.exists(timeout=1):
        # Logged-out "Join Instagram" welcome screen (fresh install, or an invalidated session) —
        # neither a login form nor a login_page_marker, so it must be tapped through first or the
        # check below mistakes it for an already-live session.
        log("logged-out welcome screen detected; tapping through to the login form")
        existing.click()
        human_pause(1.5, 2.5)
    form = _login_form(d)
    if not form:
        if _first(d, text=SELECTORS["login_page_markers"]):
            _dump_debug(d, "login")
            raise RuntimeError(f"login page shown but form not recognised; see {DEBUG_DIR}")
        return True  # no login screen; assume session is live
    if not (IG_USERNAME and IG_PASSWORD):
        _dump_debug(d, "login")
        raise RuntimeError("login screen shown but IG_USERNAME/IG_PASSWORD not set")
    user_field, pw_field = form
    log("login screen detected; entering credentials as", IG_USERNAME)
    user_field.click()
    human_pause(0.5, 1.2)
    user_field.set_text(IG_USERNAME)
    human_pause(1, 2)
    pw_field.click()
    human_pause(0.5, 1.2)
    pw_field.set_text(IG_PASSWORD)
    human_pause(1, 2)
    btn = _first(d, description=SELECTORS["login_button_texts"]) or _first(
        d, text=SELECTORS["login_button_texts"]
    )
    if btn:
        btn.click()
    else:
        d.press("enter")
    log("submitted login; waiting")
    human_pause(10, 14)
    if c := _challenge_present(d):
        _dump_debug(d, "login")
        raise RuntimeError(f"Instagram wants a human: '{c}' screen; see {DEBUG_DIR}")
    _dismiss_interstitials(d)
    if _login_form(d):
        _dump_debug(d, "login")
        raise RuntimeError(f"still on login screen after submit (wrong password?); see {DEBUG_DIR}")
    log("logged in")
    return True


def _on_following_feed(d):
    t = d(resourceIdMatches=f".*:id/{SELECTORS['following_title_id']}$", text=SELECTORS["following_text"])
    return t.exists(timeout=1)


def open_following_feed(d):
    ensure_logged_in(d)
    if d.app_current().get("package") != IG_PKG:
        _launch_app(d)
        human_pause(3, 5)
    _dismiss_interstitials(d)  # notification / location / "set up on new device" prompts
    close_sheets(d)
    w, h = d.window_size()
    sw = d(description=SELECTORS["feed_switcher_desc"])
    for attempt in range(4):
        # The action bar hides while scrolled; pull back to the top so we can see where we are.
        for _ in range(12):
            if sw.exists(timeout=1) or _on_following_feed(d):
                break
            d.swipe(w // 2, int(h * 0.3), w // 2, int(h * 0.8), duration=0.3)
            human_pause(0.8, 1.5)
        if _on_following_feed(d):
            if attempt > 0:
                return True  # we navigated here a moment ago; the screen just took a while
            # Left over from the last run: leave and re-enter so the feed is fresh.
            d.press("back")
            human_pause(2, 3)
            continue
        if sw.exists(timeout=3):
            try:
                f = d(text=SELECTORS["following_text"])
                for tap in range(4):  # taps get swallowed while the app is still warming up
                    sw.click()
                    if f.exists(timeout=5):
                        break
                    log(f"feed switch attempt {attempt}: switcher tap {tap} opened nothing")
                if f.exists(timeout=1):
                    f.click()
                    human_pause(3, 5)
                    for _ in range(6):  # cold starts can take a while to build the screen
                        if _on_following_feed(d):
                            return True
                        time.sleep(2)
                    log(f"feed switch attempt {attempt}: clicked Following but title not found")
                else:
                    _dump_debug(d, f"feed_switch_menu{attempt}")
                    d.press("back")
            except Exception as e:  # the header can scroll away between exists() and click()
                log("WARN: feed switcher click failed, retrying:", repr(e))
        else:
            d.press("back")  # some other screen; step out and retry
            human_pause(1, 2)
    log("WARN: could not open Following feed; scraping whatever feed is showing (dump saved)")
    _dump_debug(d, "feed_switch")
    return False


def _new_post(user, kind, date, place, clip_top):
    return {
        "kind": kind,
        "username": user,
        "posted_date": date,
        "place": place,
        "caption": "",
        "bounds": None,
        "share_bounds": None,
        "header_bounds": None,
        "clip_top": clip_top,
        "alt": "",
        "headless": False,
        "complete": False,
    }


def parse_hierarchy(xml: str):
    """Return a list of post dicts found in the current screen's accessibility tree.

    A post is several sibling rows in the feed RecyclerView (header+media, buttons, caption...),
    so we walk the tree in document order: a header starts a post and everything up to the next
    header belongs to it. Rows that appear before the first header belong to a post whose header
    has already scrolled off the top; we identify that one from its caption ("<user> text") and
    media description instead."""
    root = etree.fromstring(xml.encode())
    posts, cur = [], None
    # The action bar floats over the list; remember where it ends so crops can skip it.
    clip_top = 0
    for n in root.iter("node"):
        if (n.get("resource-id") or "").endswith("action_bar_container"):
            m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", n.get("bounds") or "")
            if m:
                clip_top = int(m.group(4))
            break
    in_list = False
    for n in root.iter("node"):
        rid = (n.get("resource-id") or "").split("/")[-1]
        desc = n.get("content-desc") or ""
        text = n.get("text") or ""
        if n.get("resource-id") == "android:id/list":
            in_list = True
            cur = _new_post("", "", "", "", clip_top)  # provisional: the header-less top card
            cur["headless"] = True
            posts.append(cur)
            continue
        if not in_list:
            continue
        if rid == SELECTORS["header_id"]:
            if cur is not None and cur["share_bounds"]:
                cur["complete"] = True  # we saw the whole bottom of the previous card
            m = SELECTORS["header_desc"].match(desc)
            cur = None
            if m:  # sponsored / suggested cards have a different header and are skipped
                cur = _new_post(
                    m.group("user"),
                    m.group("kind").lower(),
                    m.group("date"),
                    m.group("place") or "",
                    clip_top,
                )
                cur["header_bounds"] = n.get("bounds")  # this node *is* the header
                posts.append(cur)
            continue
        if cur is None:
            continue
        if cur["bounds"] is None and rid in SELECTORS["media_ids"]:
            cur["bounds"] = n.get("bounds")
        elif cur["share_bounds"] is None and rid == SELECTORS["share_id"]:
            cur["share_bounds"] = n.get("bounds")
        elif cur["headless"] and cur["kind"] in ("", "post") and desc.startswith("Turn sound"):
            cur["kind"] = "video"  # reels have a mute toggle and no media description
        elif not cur["alt"] and SELECTORS["media_alt"].match(desc):
            cur["alt"] = desc
            if cur["headless"] and not cur["kind"]:
                cur["kind"] = SELECTORS["alt_kind"].get(desc.split()[0].lower(), "")
        elif not cur["caption"] and text and n.get("class") == SELECTORS["caption_class"]:
            if cur["headless"] and not cur["username"]:
                cur["username"] = text.split(" ", 1)[0]
            cur["caption"] = clean_caption(text, cur["username"])
            if cur["share_bounds"]:
                cur["complete"] = True
        elif SELECTORS["timestamp"].match(text) and cur["share_bounds"]:
            if cur["headless"] and not cur["posted_date"]:
                cur["posted_date"] = text
            cur["complete"] = True  # the timestamp row sits below the caption
    # The provisional top card only counts if we could identify it.
    for p in posts:
        if p["headless"] and not p["kind"]:
            p["kind"] = "post"
    # A card with neither a caption nor a media description can't be identified (post_id() would
    # hash nothing but the username); leave it out and it will be picked up on a later dump once
    # more of it has rendered, instead of being stored as an empty placeholder.
    return [p for p in posts if p["username"] and (p["caption"] or p["alt"])]


def clean_caption(text: str, user: str) -> str:
    """'user Caption text… more' -> 'Caption text…'. The app truncates long captions itself."""
    text = text.replace(" ", " ").strip()
    if text.startswith(user + " "):
        text = text[len(user) + 1 :]
    return re.sub(r"\s*(?:…|\.\.\.)?\s*more$", "…", text).strip()


def bounds_center(bounds: str):
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds or "")
    if not m:
        return None
    x1, y1, x2, y2 = map(int, m.groups())
    return (x1 + x2) // 2, (y1 + y2) // 2


def parse_story_tray(xml: str) -> list[dict]:
    """Return the Home feed's story tray items (empty if the tray isn't on screen — it only
    appears on Home, not on Following), skipping index 0 (always the logged-in account's own
    story). Each item's content-desc doubles as a compact seen-state signal:
    "<user>'s story, <index> of <total>, Unseen." — used only to prioritize which accounts to
    open, since the stories table (keyed by a content hash, not this label) is the actual record
    of what's already been captured."""
    root = etree.fromstring(xml.encode())
    tray = next(
        (n for n in root.iter("node") if (n.get("resource-id") or "").endswith(SELECTORS["story_tray_id"])),
        None,
    )
    if tray is None:
        return []
    items = []
    for n in tray.iter("node"):
        # The avatar image inside shares the same content-desc as its parent Button; restrict to
        # the Button itself so each tray item is matched exactly once.
        if not (n.get("class") or "").endswith("Button"):
            continue
        m = SELECTORS["story_item_desc"].match(n.get("content-desc") or "")
        if not m or int(m.group("index")) == 0:
            continue
        items.append(
            {
                "username": m.group("user"),
                "seen": m.group("seen") != "Unseen",
                "bounds": n.get("bounds"),
            }
        )
    return items


def _sheet_open(d):
    if d(description=SELECTORS["copy_link_desc"]).exists(timeout=0.3):
        return True
    if any(d(text=t).exists(timeout=0.3) for t in SELECTORS["sheet_markers_text"]):
        return True
    return any(d(description=t).exists(timeout=0.3) for t in SELECTORS["sheet_markers_desc"])


def close_sheets(d, max_back=2):
    """Back out of any open share/bottom sheet without touching its contents."""
    for i in range(max_back):
        if not _sheet_open(d) or d.app_current().get("package") != IG_PKG:
            break
        if i > 0 and _on_feed(d):
            break  # feed rows visible: the marker is a false positive, another Back would exit
        d.press("back")
        human_pause(1.5, 2)
    if d.app_current().get("package") != IG_PKG:
        log("WARN: left Instagram while closing a sheet; relaunching")
        _launch_app(d)
        human_pause(3, 5)
    return not _sheet_open(d)


def _on_feed(d):
    """True when a feed list with post rows is showing (title bars hide while scrolled, so
    they are not a reliable signal)."""
    return d(resourceIdMatches=f".*:id/{SELECTORS['share_id']}").exists(timeout=0.5) or d(
        resourceIdMatches=f".*:id/{SELECTORS['header_id']}"
    ).exists(timeout=0.5)


def _on_home_feed(d):
    """The Home feed keeps the bottom tab bar; the Following screen does not."""
    return d(resourceIdMatches=".*:id/feed_tab").exists(timeout=0.5)


def _back_to_feed(d, tries=2):
    """If a tap opened a profile/hashtag/etc., back out until a feed is showing again. Never
    backs out of the app: if we somehow left it, relaunch instead."""
    for _ in range(tries):
        if d.app_current().get("package") != IG_PKG:
            _launch_app(d)
            human_pause(3, 5)
            return _on_feed(d)
        if _on_feed(d) and not _sheet_open(d):
            return True
        d.press("back")
        human_pause(1, 1.5)
    return _on_feed(d)


_last_url = ""


def fetch_permalink(d, post_hash: str) -> tuple[str | None, str | None]:
    """Open the share sheet for the post with this hash, pick 'Copy link', read the clipboard.

    Re-dumps the hierarchy right before tapping and clicks the share *element* (not stale
    coordinates) because the feed can shift a few hundred px between a dump and a tap.
    Returns (url, None) on success, or (None, reason) where reason is "sheet" (the share sheet
    never opened, or the card/button vanished before it could) or "clipboard" (the sheet opened
    and Copy link was tapped, but the clipboard never carried a fresh permalink) — the two need
    different recoveries, so the caller counts them separately."""
    if not close_sheets(d):
        log("WARN: a sheet is stuck open; skipping permalink")
        return None, "sheet"
    fresh = next((p for p in parse_hierarchy(d.dump_hierarchy()) if post_id(p) == post_hash), None)
    if not fresh or not fresh["share_bounds"]:
        log("WARN: card moved before the share tap; no permalink")
        return None, "sheet"
    link = d(description=SELECTORS["copy_link_desc"])
    for _tap in range(SHARE_TAP_TRIES):  # the first tap is occasionally swallowed by the video overlay
        # Coordinate tap from the fresh dump: element-based clicks on this (non-clickable)
        # ViewGroup are unreliable on video cards.
        d.click(*bounds_center(fresh["share_bounds"]))
        human_pause(2, 3)
        if link.exists(timeout=10):
            break
    if not link.exists(timeout=1):
        log("WARN: no share sheet with 'Copy link'; dump saved")
        _dump_debug(d, "share_sheet")
        close_sheets(d)
        _back_to_feed(d)
        return None, "sheet"
    # Note: clearing the clipboard first (d.set_clipboard) makes the next read come back empty.
    # Staleness is caught below by comparing with the last link we handed out.
    human_pause(0.8, 1.2)  # let the sheet finish animating
    try:
        b = link.info.get("bounds") or {}
        cx, cy = (b["left"] + b["right"]) // 2, (b["top"] + b["bottom"]) // 2
    except Exception as e:  # the sheet re-rendered and the node vanished
        log("WARN: Copy link vanished before click:", repr(e))
        close_sheets(d)
        _back_to_feed(d)
        return None, "sheet"
    d.click(cx, cy)
    # Poll instead of a single fixed-delay read: the clipboard write can lag the tap by more
    # than a beat, and the old one-shot read missed it more often than not.
    global _last_url
    url = ""
    deadline = time.time() + CLIPBOARD_TIMEOUT
    while time.time() < deadline:
        time.sleep(0.4)
        try:
            candidate = d.clipboard or ""
        except Exception as e:
            log("WARN: clipboard read failed:", repr(e))
            continue
        if candidate and candidate != _last_url and SELECTORS["permalink"].match(candidate):
            url = candidate
            break
    if url and url == _last_url:
        log("WARN: clipboard still holds the previous post's link; copy failed")
        url = ""
    elif url:
        _last_url = url
    close_sheets(d)  # sheet usually closes itself after Copy link; make sure
    _back_to_feed(d)
    m = SELECTORS["permalink"].match(url)
    if not m:
        log("WARN: clipboard did not contain a permalink:", repr(url[:80]))
        return None, "clipboard"
    return f"https://www.instagram.com/{m.group('type')}/{m.group('code')}/", None


def post_id(p):
    """Cheap identity for the first-pass 'have we stored this' check. Must not depend on anything
    that changes while the post sits in the feed: like counts, relative dates, carousel index."""
    # Caption if there is one, else the media description up to the first comma ("Photo  of  by X").
    key = p["caption"][:200] if p["caption"] else re.sub(r"\d+", "", p["alt"].split(",")[0])
    # No kind here: a header-less video card has no media description to infer it from.
    raw = f"{p['username']}|{key}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def crop_media(d, bounds: str, pid: str, clip_top: int = 0, settle: float = 0):
    """Screenshot the visible post image and save it as "{pid}.jpg". Returns filename or None.
    `settle` delays the shot (e.g. for a video/Reel, so autoplay has started and the initial
    audio-label overlay has faded) before it's taken — still one image, just a better-timed one."""
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds or "")
    if not m:
        return None
    x1, y1, x2, y2 = map(int, m.groups())
    w, h = d.window_size()
    full = y2 - y1
    y1, y2 = max(y1, clip_top), min(y2, h)  # trim the floating action bar / screen edge
    if full < 200 or (y2 - y1) < 0.4 * full:
        log(f"no crop: media bounds {bounds} mostly off-screen")
        return None
    if settle:
        human_pause(settle, settle * 1.4)
    img: Image.Image = d.screenshot()
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    fn = f"{pid}.jpg"
    img.crop((x1, y1, x2, y2)).convert("RGB").save(MEDIA_DIR / fn, quality=MEDIA_QUALITY)
    return fn


_SLIDE_INDEX = re.compile(r"^(?:Photo|Video)\s+(\d+)\s+of\s+(\d+)\b", re.I)


def carousel_count(alt: str) -> int:
    """Parse the slide total from a carousel card's media description ("Photo 1 of 7 by X, 317
    likes, 10 comments"). Returns 1 (not a carousel, or the format drifted) if it can't be parsed."""
    m = _SLIDE_INDEX.match(alt or "")
    return int(m.group(2)) if m else 1


def capture_carousel(d, p: dict, pid: str) -> list[str]:
    """Swipe through a carousel's remaining slides in place and crop each one. `pid` already has
    slide 1 captured by the caller via crop_media(); this walks slides 2..total (capped at
    MAX_CAROUSEL_SLIDES), stopping as soon as a swipe doesn't land on the next slide (the swipe was
    read as a vertical scroll instead, or Instagram simply has nothing further) rather than risk
    storing the same slide twice. Returns the extra slides' filenames, in order."""
    total = min(carousel_count(p["alt"]), MAX_CAROUSEL_SLIDES)
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", p["bounds"] or "")
    if total < 2 or not m:
        return []
    x1, y1, x2, y2 = map(int, m.groups())
    cy = (y1 + y2) // 2
    inset = max(int((x2 - x1) * 0.1), 1)
    key = post_id(p)
    files = []
    for slide in range(2, total + 1):
        d.swipe(x2 - inset, cy, x1 + inset, cy, duration=random.uniform(SCROLL_SWIPE_MIN, SCROLL_SWIPE_MAX))
        human_pause(0.8, 1.6)
        fresh = next((c for c in parse_hierarchy(d.dump_hierarchy()) if post_id(c) == key), None)
        sm = _SLIDE_INDEX.match(fresh["alt"]) if fresh else None
        if not fresh or not sm or int(sm.group(1)) != slide:
            log(f"carousel: swipe didn't land on slide {slide}; stopping with {len(files)} extra")
            break
        fn = crop_media(d, fresh["bounds"] or p["bounds"], f"{pid}_{slide - 1}", p.get("clip_top", 0))
        if not fn:
            break
        files.append(fn)
    return files


_SAFE_USERNAME = re.compile(r"^[A-Za-z0-9._]+$")


def _safe_filename(username: str) -> str | None:
    """Reject anything that isn't a plausible Instagram handle before it's used as a filename —
    username is parsed from screen content, not trusted input."""
    if not username or username in (".", "..") or not _SAFE_USERNAME.match(username):
        return None
    return username


def _avatar_bounds(header_bounds: str):
    """The header's own bounds crop to a leading square avatar with a small inset — the avatar
    ImageView has no addressable node (row_feed_profile_header is a collapsed leaf in the
    accessibility tree), so this crops positionally rather than by resource-id. Needs a live check
    against a real device to confirm the inset actually lands on the avatar."""
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", header_bounds or "")
    if not m:
        return None
    x1, y1, x2, y2 = map(int, m.groups())
    size = y2 - y1
    pad = max(size // 8, 1)
    return x1 + pad, y1 + pad, x1 + pad + (size - 2 * pad), y2 - pad


def capture_avatar(d, header_bounds: str, username: str) -> str | None:
    """Crop the account's avatar from its own feed header and save it once per account, in its own
    subdirectory so the retention orphan sweep (which only globs MEDIA_DIR's top level) never
    touches it. Returns the path relative to MEDIA_DIR, or None."""
    box = _avatar_bounds(header_bounds)
    safe_user = _safe_filename(username)
    if not box or not safe_user:
        return None
    avatar_dir = MEDIA_DIR / "avatars"
    avatar_dir.mkdir(parents=True, exist_ok=True)
    img: Image.Image = d.screenshot()
    fn = f"{safe_user}.jpg"
    img.crop(box).convert("RGB").save(avatar_dir / fn, quality=MEDIA_QUALITY)
    return f"avatars/{fn}"


def _delete_post(con, post_id: str):
    """Delete one post row, its extra-slide media rows, and unlink every file involved (cover +
    slides). Reads media_file straight off the posts row rather than only the media table, since a
    row can predate carousel capture (or be inserted directly, as tests do) with no media rows."""
    row = con.execute("SELECT media_file FROM posts WHERE id=?", (post_id,)).fetchone()
    files = {row["media_file"]} if row and row["media_file"] else set()
    files |= {r[0] for r in con.execute("SELECT file FROM media WHERE post_id=?", (post_id,))}
    con.execute("DELETE FROM posts WHERE id=?", (post_id,))
    con.execute("DELETE FROM media WHERE post_id=?", (post_id,))
    con.commit()
    for fn in files:
        (MEDIA_DIR / fn).unlink(missing_ok=True)


def _media_and_db_size_mb() -> float:
    """Live disk footprint: the media tree plus the database's *live* size. A raw file-size stat on
    DB_PATH would overcount once rows have been deleted — SQLite returns freed pages to an internal
    freelist rather than shrinking the file — which would make the size cap below keep deleting
    posts chasing a floor the file can never reach."""
    total = 0
    if Path(DB_PATH).exists():
        con = sqlite3.connect(DB_PATH)
        try:
            page_count = con.execute("PRAGMA page_count").fetchone()[0]
            freelist = con.execute("PRAGMA freelist_count").fetchone()[0]
            page_size = con.execute("PRAGMA page_size").fetchone()[0]
            total += (page_count - freelist) * page_size
        finally:
            con.close()
    if MEDIA_DIR.exists():
        total += sum(f.stat().st_size for f in MEDIA_DIR.rglob("*") if f.is_file())
    return total / (1024 * 1024)


def _enforce_size_cap(con):
    """Delete the oldest posts, one at a time, until total size is back under MEDIA_MAX_MB or
    there's nothing left to delete. 0 disables. Runs after age-based pruning and the orphan sweep,
    so it only ever has to make up the difference."""
    if MEDIA_MAX_MB <= 0:
        return
    removed = 0
    while _media_and_db_size_mb() > MEDIA_MAX_MB:
        row = con.execute(
            "SELECT id FROM posts ORDER BY COALESCE(posted_at, scraped_at) ASC LIMIT 1"
        ).fetchone()
        if not row:
            log(f"retention: still over MEDIA_MAX_MB={MEDIA_MAX_MB} with no posts left to remove")
            break
        _delete_post(con, row["id"])
        removed += 1
    if removed:
        log(f"retention: removed {removed} additional post(s) to stay under {MEDIA_MAX_MB}MB")


def capture_story_media(img: Image.Image, media_bounds: str, clip_top: int, tmp_name: str) -> Path | None:
    """Crop a story's current frame — from a screenshot already taken by the caller, not one taken
    here — into MEDIA_DIR/stories. clip_top skips the username/timestamp header overlay so the
    saved image doesn't bake in text that changes hour to hour (that text would otherwise make the
    same still-active story hash differently across runs — see scrape_stories())."""
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", media_bounds or "")
    if not m:
        return None
    x1, y1, x2, y2 = map(int, m.groups())
    y1 = max(y1, clip_top)
    if (y2 - y1) < 200:
        return None
    stories_dir = MEDIA_DIR / "stories"
    stories_dir.mkdir(parents=True, exist_ok=True)
    path = stories_dir / f"{tmp_name}.jpg"
    img.crop((x1, y1, x2, y2)).convert("RGB").save(path, quality=MEDIA_QUALITY)
    return path


def capture_story(d, item: dict) -> dict | None:
    """Open one tray item's story, capture its current frame, and always exit back to the Home
    feed via Back. Never tap forward inside the viewer (past this one open tap): the message/like/
    reshare/profile-picture/menu targets are all real actions on someone else's story, and on this
    host, tapping to advance past a story's last frame has been observed to eject the app to the OS
    launcher entirely — unlike Back, which reliably returns to the tray.

    A single-frame story can also auto-advance (and eject the app the same way) on its own, within
    a few seconds of opening, whether or not we ever tap — an earlier version of this function took
    the screenshot after a hierarchy-walk following a multi-second polling loop, and was seen to
    capture the *launcher's* wallpaper (or a black transition frame) instead of the story, having
    fallen behind the story's own display timer. So every device round-trip here is on the clock:
    a single cheap exists() (not a repeated dump_hierarchy()) confirms the viewer opened, then the
    screenshot is taken and Back is pressed immediately — cropping and saving happen afterward,
    off-device, where they can't race anything.

    Returns {username, posted_date, path} or None if the story didn't open, closed before the
    screenshot, or nothing could be cropped."""
    if not item["bounds"]:
        return None
    d.click(*bounds_center(item["bounds"]))
    if not d(resourceIdMatches=f".*:id/{SELECTORS['story_viewer_id']}").exists(timeout=5):
        log(f"WARN: story for {item['username']} didn't open; skipping")
        d.press("back")
        human_pause(1, 1.5)
        return None
    xml = d.dump_hierarchy()
    if SELECTORS["story_viewer_id"] not in xml:  # exists() can win a race against a fast auto-exit
        log(f"WARN: story for {item['username']} closed before it could be read; dump saved")
        _dump_debug(d, f"story_{_safe_filename(item['username']) or 'unknown'}", xml=xml)
        d.press("back")
        human_pause(1, 1.5)
        return None
    img = d.screenshot()
    d.press("back")  # off the device from here on; cropping/saving below never risks the timer
    human_pause(1, 1.5)
    root = etree.fromstring(xml.encode())
    media_bounds = clip_top = None
    posted_date = ""
    for n in root.iter("node"):
        rid = (n.get("resource-id") or "").split("/")[-1]
        if rid == SELECTORS["story_media_id"] and media_bounds is None:
            media_bounds = n.get("bounds")
        elif rid == SELECTORS["story_shadow_id"] and clip_top is None:
            m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", n.get("bounds") or "")
            if m:
                clip_top = int(m.group(4))
        elif rid == SELECTORS["story_timestamp_id"] and not posted_date:
            posted_date = n.get("text") or ""
    path = capture_story_media(
        img, media_bounds, clip_top or 0, f"tmp_{item['username']}_{int(time.time() * 1000)}"
    )
    return {"username": item["username"], "posted_date": posted_date, "path": path} if path else None


def scrape_stories(d, con) -> int:
    """Visit each not-yet-seen account's story from the Home feed's tray, capture its current
    frame, and return to the Following feed afterward. Stories have no stable public id the way
    posts do (no permalink/shortcode), so identity is a content hash of the captured crop, checked
    against the DB only after capture — a duplicate is simply discarded, not prevented up front."""
    for _ in range(3):
        if _on_home_feed(d):
            break
        d.press("back")
        human_pause(1, 1.5)
    else:
        log("WARN: could not reach the Home feed for stories; skipping this run")
        return 0
    items = [i for i in parse_story_tray(d.dump_hierarchy()) if not i["seen"]]
    new = 0
    for item in items[:MAX_STORIES_PER_RUN]:
        if not _on_home_feed(d):
            # A prior story's own auto-exit can eject the app entirely (see capture_story()); tapping
            # this item's now-stale tray coordinates against whatever's currently on screen would be
            # a shot in the dark, so recover onto Home first.
            log("WARN: not on the Home feed any more; recovering before the next story")
            for _ in range(3):
                if _on_home_feed(d):
                    break
                _launch_app(d)
                human_pause(2, 3)
            else:
                log("WARN: could not recover the Home feed; stopping story capture for this run")
                break
        captured = capture_story(d, item)
        if not captured:
            continue
        digest = hashlib.sha256(captured["path"].read_bytes()).hexdigest()[:16]
        now = datetime.now(UTC)
        expires = (now + timedelta(hours=STORY_RETAIN_HOURS)).isoformat()
        cur = con.execute(
            "INSERT OR IGNORE INTO stories (id, username, media_file, kind, posted_date, scraped_at,"
            " expires_at) VALUES (?,?,?,?,?,?,?)",
            (
                digest,
                captured["username"],
                f"stories/{digest}.jpg",
                "story",
                captured["posted_date"],
                now.isoformat(),
                expires,
            ),
        )
        con.commit()
        if cur.rowcount:
            captured["path"].rename(MEDIA_DIR / "stories" / f"{digest}.jpg")
            new += 1
            log(f"new story: {captured['username']}")
        else:
            captured["path"].unlink(missing_ok=True)  # byte-identical to one already stored
    open_following_feed(d)
    return new


def _prune_expired_stories(con):
    """Stories always expire STORY_RETAIN_HOURS after capture, regardless of RETAIN_DAYS — they
    model Instagram's own ~24h ephemerality, not the post-retention policy."""
    now = datetime.now(UTC).isoformat()
    gone = con.execute("SELECT media_file FROM stories WHERE expires_at < ?", (now,)).fetchall()
    cur = con.execute("DELETE FROM stories WHERE expires_at < ?", (now,))
    con.commit()
    for (fn,) in gone:
        if fn:
            (MEDIA_DIR / fn).unlink(missing_ok=True)
    if cur.rowcount:
        log(f"retention: removed {cur.rowcount} expired stor{'y' if cur.rowcount == 1 else 'ies'}")


def _prune_old_posts(con):
    """Delete posts older than RETAIN_DAYS (0 disables), any media file no row references any
    more, and (if MEDIA_MAX_MB is set) additional oldest posts until total size is back under the
    cap — in that order, so disk use stays flat instead of growing forever."""
    if RETAIN_DAYS > 0:
        cutoff = (datetime.now(UTC) - timedelta(days=RETAIN_DAYS)).isoformat()
        old_ids = [
            r[0]
            for r in con.execute("SELECT id FROM posts WHERE COALESCE(posted_at, scraped_at) < ?", (cutoff,))
        ]
        for pid in old_ids:
            _delete_post(con, pid)
        if old_ids:
            log(f"retention: removed {len(old_ids)} post(s) older than {RETAIN_DAYS}d")
    if MEDIA_DIR.exists():
        kept = {r[0] for r in con.execute("SELECT media_file FROM posts WHERE media_file IS NOT NULL")}
        kept |= {r[0] for r in con.execute("SELECT file FROM media")}
        # Only ever written media_file names are *.jpg (crop_media()), and this glob is
        # non-recursive, so pointing MEDIA_DIR at the wrong directory can't delete unrelated files
        # and avatars/ (its own subdirectory) is never touched by this sweep.
        orphans = [f for f in MEDIA_DIR.glob("*.jpg") if f.name not in kept]
        for f in orphans:
            f.unlink(missing_ok=True)
        if orphans:
            log(f"retention: removed {len(orphans)} orphaned media file(s)")
    _enforce_size_cap(con)


def scrape_once(d, con) -> dict:
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    global _last_url
    try:
        _last_url = d.clipboard or ""  # whatever is there now is stale by definition
    except Exception:
        _last_url = ""
    open_following_feed(d)
    try:
        new_stories = scrape_stories(d, con)
    except Exception as e:  # a stories-viewer surprise must not sink the whole run
        log("WARN: story capture failed, continuing with posts:", repr(e))
        new_stories = 0
        open_following_feed(d)  # best-effort recovery back onto the screen the post loop expects
    if new_stories:
        log(f"stories: {new_stories} new")
    new, seen_streak, this_run = 0, 0, set()
    link_failures: dict[str, int] = {}  # hash -> failed share-sheet attempts
    link_sheet_failures = link_clipboard_failures = 0
    accounts_seen: set[str] = set()  # usernames already upserted this run
    avatars_checked: set[str] = set()  # usernames whose avatar has been considered this run
    warnings: list[str] = []
    screens = empty_streak = 0
    feed_reopened = False
    while screens < MAX_SCROLLS:
        xml = d.dump_hierarchy()
        posts = parse_hierarchy(xml)
        if screens == 0 and not posts:
            _dump_debug(d, "last", xml=xml)
            log("no posts parsed on first screen — selectors probably need updating; dump saved")
        empty_streak = 0 if posts else empty_streak + 1
        if EMPTY_SCREEN_LIMIT and empty_streak >= EMPTY_SCREEN_LIMIT:
            # Scrolling on regardless was seen live: 2 cards on screen 0, then 24 straight empty
            # screens, with nothing saved to show what was on screen. Whatever it is (a screen we
            # landed on by accident, the end of the feed, a UI change), more swipes won't fix it:
            # reopen the feed once, and stop the run if that doesn't help either.
            _dump_debug(d, f"empty_feed{int(feed_reopened)}", xml=xml)
            if feed_reopened:
                log(f"{empty_streak} empty screens again after reopening the feed; stopping (dump saved)")
                warnings.append(f"stopped early: {empty_streak} empty screens in a row after reopening")
                break
            log(f"{empty_streak} empty screens in a row; reopening the feed (dump saved)")
            warnings.append(f"reopened the feed after {empty_streak} empty screens in a row")
            feed_reopened, empty_streak = True, 0
            open_following_feed(d)
            continue
        for p in posts:
            u = p["username"]
            if not u:
                continue
            if u not in accounts_seen:
                accounts_seen.add(u)
                _upsert_account(con, u)
            # Only mark an avatar "checked" once a header is actually on screen for this account —
            # the header-less top card (its header already scrolled off) would otherwise block a
            # retry for the rest of the run even though its avatar was never actually captured.
            if u not in avatars_checked and p["header_bounds"]:
                avatars_checked.add(u)
                if _needs_avatar_refresh(con, u):
                    avatar = capture_avatar(d, p["header_bounds"], u)
                    if avatar:
                        _upsert_account(con, u, avatar)
        touched = False
        for p in posts:
            h = post_id(p)
            if h in this_run:
                continue  # still on screen from the previous scroll
            if not p["complete"]:
                continue  # wait until the whole bottom of the card is on screen (stable identity)
            if con.execute("SELECT 1 FROM posts WHERE hash=? OR id=?", (h, h)).fetchone():
                this_run.add(h)
                seen_streak += 1
                continue
            settle = VIDEO_SETTLE_SECONDS if p["kind"] == "video" else 0
            media = crop_media(
                d, p["bounds"], h, p.get("clip_top", 0), settle=settle
            )  # before any sheet opens
            if not media and not p["bounds"]:
                log("no crop: media node not found for card")
            extra_media = capture_carousel(d, p, h) if media and p["kind"] == "carousel" else []
            url, fail_reason = fetch_permalink(d, h)
            if fail_reason == "sheet":
                link_sheet_failures += 1
            elif fail_reason == "clipboard":
                link_clipboard_failures += 1
            touched = True
            if not url and link_failures.get(h, 0) < PERMALINK_RETRIES:
                # The sheet sometimes fails to open; try again on a later screen.
                link_failures[h] = link_failures.get(h, 0) + 1
                if media:
                    (MEDIA_DIR / media).unlink(missing_ok=True)
                for fn in extra_media:
                    (MEDIA_DIR / fn).unlink(missing_ok=True)
                break
            this_run.add(h)
            if _on_home_feed(d) or not _on_feed(d):
                log("WARN: not on the Following feed any more; reopening it")
                open_following_feed(d)
                this_run.discard(h)  # let the card be handled again where it appears
            pid = url.rstrip("/").rsplit("/", 1)[-1] if url else h
            row = con.execute("SELECT username FROM posts WHERE id=?", (pid,)).fetchone() if url else None
            if row and row[0] != p["username"]:
                log(
                    f"WARN: permalink {pid} belongs to {row[0]}, not {p['username']}; stale clipboard, dropping it"
                )
                url, pid = None, h
            elif row:
                seen_streak += 1  # same post, caption edited since we stored it
                con.execute("UPDATE posts SET hash=? WHERE id=?", (h, pid))
                con.commit()
                if media:
                    (MEDIA_DIR / media).unlink(missing_ok=True)
                for fn in extra_media:
                    (MEDIA_DIR / fn).unlink(missing_ok=True)
                break
            seen_streak = 0
            store_caption = p["caption"] or p["alt"]
            parsed = parse_posted_at(p["posted_date"], datetime.now(UTC))
            posted_at, posted_at_prec = parsed if parsed else (None, None)
            dup = _find_duplicate(con, p["username"], posted_at, posted_at_prec, store_caption)
            if dup:
                final_id, fields, media_to_drop = _merged_fields(
                    dup,
                    pid,
                    url,
                    h,
                    p["kind"],
                    p["posted_date"],
                    p["place"],
                    store_caption,
                    media,
                    posted_at,
                    datetime.now(UTC),
                )
                if media_to_drop:
                    (MEDIA_DIR / media_to_drop).unlink(missing_ok=True)
                _write_merged(con, dup["id"], final_id, fields)
                con.commit()
                for fn in extra_media:  # the existing row's cover wins; extra slides are redundant
                    (MEDIA_DIR / fn).unlink(missing_ok=True)
                seen_streak += 1
                log(f"merged duplicate: {p['username']} -> {final_id}")
                break
            if media and pid != h:
                (MEDIA_DIR / media).rename(MEDIA_DIR / f"{pid}.jpg")
                media = f"{pid}.jpg"
                renamed = []
                for i, fn in enumerate(extra_media, start=1):
                    new_fn = f"{pid}_{i}.jpg"
                    (MEDIA_DIR / fn).rename(MEDIA_DIR / new_fn)
                    renamed.append(new_fn)
                extra_media = renamed
            now_iso = datetime.now(UTC).isoformat()
            con.execute(
                "INSERT INTO posts (id, username, kind, posted_date, caption, media_file, scraped_at,"
                " hash, url, place, posted_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    pid,
                    p["username"],
                    p["kind"],
                    p["posted_date"],
                    store_caption,
                    media,
                    now_iso,
                    h,
                    url,
                    p["place"],
                    posted_at.isoformat() if posted_at else None,
                    now_iso,
                ),
            )
            for i, fn in enumerate(extra_media, start=1):
                con.execute(
                    "INSERT OR REPLACE INTO media (post_id, idx, file) VALUES (?, ?, ?)", (pid, i, fn)
                )
            con.commit()
            new += 1
            log(f"new post: {p['username']} ({p['kind']}) {p['posted_date']} {url or '(no permalink)'}")
            break  # the screen may have shifted; re-dump before handling the next card
        if touched:
            continue
        log(f"screen {screens}: {len(posts)} cards, {new} new so far, seen-streak {seen_streak}")
        if seen_streak >= STOP_AFTER_SEEN:
            log("hit already-seen posts; stopping")
            break
        human_scroll(d)
        human_pause(SCROLL_PAUSE_MIN, SCROLL_PAUSE_MAX)
        screens += 1
    _prune_old_posts(con)
    _prune_expired_stories(con)
    _prune_debug_dumps()  # age-based pruning shouldn't depend on a new dump happening to be taken
    # Leave the app in a natural state
    d.press("home")
    return {
        "new": new,
        "new_stories": new_stories,
        "link_sheet_failures": link_sheet_failures,
        "link_clipboard_failures": link_clipboard_failures,
        "warning": "; ".join(warnings) or None,
    }


def main():
    con = db_init()
    attempt = 0  # consecutive transient-failure retries so far
    while True:
        started_at = datetime.now(UTC).isoformat()
        snapshot, stats, error, exc = {}, {}, None, None
        try:
            d = connect_device()
            snapshot = _device_snapshot(d)
            stats = scrape_once(d, con)
            log(f"run complete: {stats['new']} new posts, {stats['new_stories']} new stories")
        except Exception as e:  # keep the loop alive; log for debugging
            exc, error = e, repr(e)
            log("ERROR:", error)
        record_run(
            con,
            started_at,
            datetime.now(UTC).isoformat(),
            stats.get("new", 0),
            error,
            snapshot,
            stats.get("link_sheet_failures", 0),
            stats.get("link_clipboard_failures", 0),
            stats.get("new_stories", 0),
            stats.get("warning"),
        )
        seconds, attempt = next_sleep_seconds(exc, attempt)
        if attempt:
            log(
                f"transient device failure; retry {attempt}/{len(RETRY_DELAYS_MINUTES)} in {seconds / 60:.1f}m"
            )
        else:
            log(f"sleeping {seconds / 3600:.2f}h")
        time.sleep(seconds)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "once":
        con = db_init()
        stats = scrape_once(connect_device(), con)
        print(stats["new"], "new posts,", stats["new_stories"], "new stories")
    elif len(sys.argv) > 1 and sys.argv[1] == "login":
        print("logged in:", ensure_logged_in(connect_device()))
    elif len(sys.argv) > 1 and sys.argv[1] == "dump":
        d = connect_device()
        _dump_debug(d, "manual")
        print("wrote", DEBUG_DIR)
    elif len(sys.argv) > 1 and sys.argv[1] == "rename":
        if len(sys.argv) != 4:
            print("usage: scraper.py rename <old_username> <new_username>")
            sys.exit(1)
        n = rename_account(db_init(), sys.argv[2], sys.argv[3])
        print(f"moved {n} post(s) from {sys.argv[2]!r} to {sys.argv[3]!r}")
    else:
        main()
