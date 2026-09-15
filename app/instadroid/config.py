"""Settings, read from the environment once at import. Code reads them as config.NAME at call
time, so tests can monkeypatch any of them."""

import os
from pathlib import Path

from shared.fileenv import env_secret


def _choice(name: str, default: str, allowed: tuple[str, ...]) -> str:
    """The environment variable `name`, trimmed and lowercased, when it's one of `allowed`; otherwise
    `default`, with a warning for a value that isn't."""
    value = os.environ.get(name, default).strip().lower()
    if value not in allowed:
        print(f"WARN: unknown {name} {value!r}; falling back to {default}", flush=True)
        return default
    return value


ADB_ADDR = os.environ.get("ADB_ADDR", "127.0.0.1:5555")  # redroid's forwarded ADB port
DB_PATH = os.environ.get("DB_PATH", "/db/posts.sqlite")
# Manual control files (instadroid/control.py): manual.lock holds scheduled runs back, scrape-now cuts
# the wait short. Defaults to the database directory, which the feed server and the host share.
CONTROL_DIR = os.environ.get("CONTROL_DIR", "") or str(Path(DB_PATH).parent)
LOCK_MAX_HOURS = float(os.environ.get("LOCK_MAX_HOURS", "6"))  # an older lock counts as forgotten; 0 = never
RUN_NOW_MIN_MINUTES = float(os.environ.get("RUN_NOW_MIN_MINUTES", "30"))  # rate limit for scrape-now
CONTROL_POLL_SECONDS = 30.0  # how often a sleeping or locked loop checks the control files
MEDIA_DIR = Path(os.environ.get("MEDIA_DIR", "/media"))
DEBUG_DIR = Path(os.environ.get("DEBUG_DIR", "/debug"))
POLL_MIN_H = float(os.environ.get("POLL_MIN_HOURS", "2.5"))
POLL_MAX_H = float(os.environ.get("POLL_MAX_HOURS", "4.5"))
# Which feed to scrape. "chrono" (default): the real chronological Following feed, reached via the
# switcher — see navigation.open_following_feed(). "home": deliberately stay on the algorithmic Home feed
# instead and skip the switcher navigation entirely — e.g. pair with FOLLOWING_REFRESH_DAYS's
# allowlist to filter Home's suggested content rather than fighting the switcher for it. Any other
# value falls back to "chrono" (logged once at import).
FEED_MODE = _choice("FEED_MODE", "chrono", ("chrono", "home"))
MAX_SCROLLS = int(os.environ.get("MAX_SCROLLS", "25"))
STOP_AFTER_SEEN = int(os.environ.get("STOP_AFTER_SEEN", "4"))
RETAIN_DAYS = int(os.environ.get("RETAIN_DAYS", "60"))  # 0 disables deletion
# How long the swipe gesture itself takes (a fling scrolls several screens and skips posts) and
# how long to sit idle between scrolls (both randomized within their range, like a human thumb).
SCROLL_SWIPE_MIN = float(os.environ.get("SCROLL_SWIPE_MIN", "0.6"))
SCROLL_SWIPE_MAX = float(os.environ.get("SCROLL_SWIPE_MAX", "1.0"))
SCROLL_PAUSE_MIN = float(os.environ.get("SCROLL_PAUSE_MIN", "1.5"))
SCROLL_PAUSE_MAX = float(os.environ.get("SCROLL_PAUSE_MAX", "4.0"))
MEDIA_QUALITY = int(os.environ.get("MEDIA_QUALITY", "95"))  # encoder quality for saved images
# Format for every saved image (post crops, carousel slides, avatars, stories). WebP measured ~36%
# smaller than JPEG at quality 95 and ~56% smaller at 90, re-encoding real crops (2026-09-14). Files
# already written keep their extension and stay valid after a switch, since the database stores each
# file name. An unrecognized value falls back to webp.
MEDIA_FORMAT = _choice("MEDIA_FORMAT", "webp", ("webp", "jpeg"))
MEDIA_EXTS = (".jpg", ".webp")  # every extension this scraper has ever written
# Either can come from a file instead (IG_USERNAME_FILE / IG_PASSWORD_FILE, e.g. a Docker secret).
IG_USERNAME = env_secret("IG_USERNAME")
IG_PASSWORD = env_secret("IG_PASSWORD")
IG_PKG = "com.instagram.android"
# If the device has no Instagram installed, navigation.ensure_logged_in() fetches it with apkeep (built into
# the image, see Dockerfile) and adb-installs it, instead of just raising — this is what lets the
# service recover on its own from a fresh /data volume or the /data/system-reset scenario in
# docs/INCIDENTS.md, where Instagram's package registration was orphaned but the app itself wasn't touched.
# 0/false/empty falls back to the original behavior: raise and require a manual `adb install`.
IG_AUTO_INSTALL = os.environ.get("IG_AUTO_INSTALL", "1").strip().lower() not in ("0", "false", "")
# Force one Instagram version profile: a directory under igprofiles/ ("v424", or just "424"). Empty (the
# default) = the highest profile at or below the installed Instagram version, chosen on every connect.
# Profiles exist only where Instagram changed something. See docs/PROFILES.md.
IG_PROFILE = os.environ.get("IG_PROFILE", "").strip()
# Override the Instagram build auto-install and `scraper.py install` fetch. Empty = igprofiles.DEFAULT_BUILD
# (see igprofiles.default_build()); "latest" = the newest on APKPure.
IG_APK_VERSION = os.environ.get("IG_APK_VERSION", "").strip()
APK_CACHE_DIR = Path(os.environ.get("APK_CACHE_DIR", "/apk"))
APK_FETCH_TIMEOUT = float(os.environ.get("APK_FETCH_TIMEOUT", "300"))  # apkeep's own download
DEBUG_KEEP = 12  # debug dump pairs to retain; older ones are pruned on every new dump
# Profile development (devtools/new_profile.py baseline): when set, every screen the scraper visits
# is also saved here as a numbered hierarchy + screenshot pair (up to CAPTURE_PER_SCREEN of each
# screen, plus every failure dump), never pruned, for `new_profile.py check`. Empty = off.
PROFILE_CAPTURE_DIR = os.environ.get("PROFILE_CAPTURE_DIR", "").strip()
CAPTURE_PER_SCREEN = 3
DEBUG_RETAIN_DAYS = float(os.environ.get("DEBUG_RETAIN_DAYS", "7"))  # 0 disables age-based pruning
_DEBUG_ARTIFACT_SUFFIXES = (".xml", ".jpg", ".png", ".txt")
# How much of a filtered logcat to keep per device failure (see diagnostics.save_failure_logcat()).
LOGCAT_TAIL_LINES = int(os.environ.get("LOGCAT_TAIL_LINES", "2000"))
LOGCAT_TIMEOUT = 30.0  # seconds for `adb logcat -d`
PERMALINK_RETRIES = int(os.environ.get("PERMALINK_RETRIES", "2"))  # extra share-sheet passes after the first
# Backfill: when a post stored under a hash id (no permalink) is back on screen, try Copy link again, at
# most PERMALINK_BACKFILL_PER_RUN times a run (each is a share-sheet round trip; 0 disables) and
# PERMALINK_BACKFILL_TRIES times per post across runs (posts.permalink_attempts).
PERMALINK_BACKFILL_PER_RUN = int(os.environ.get("PERMALINK_BACKFILL_PER_RUN", "3"))
PERMALINK_BACKFILL_TRIES = int(os.environ.get("PERMALINK_BACKFILL_TRIES", "3"))
SHARE_TAP_TRIES = int(os.environ.get("SHARE_TAP_TRIES", "2"))  # taps on the share button before giving up
CAPTION_EXPAND_TRIES = int(
    os.environ.get("CAPTION_EXPAND_TRIES", "2")
)  # taps on a truncated caption's "more"
CLIPBOARD_TIMEOUT = float(os.environ.get("CLIPBOARD_TIMEOUT", "6.0"))  # seconds to poll the clipboard for
MAX_CAROUSEL_SLIDES = int(os.environ.get("MAX_CAROUSEL_SLIDES", "10"))
VIDEO_SETTLE_SECONDS = float(os.environ.get("VIDEO_SETTLE_SECONDS", "1.5"))  # let autoplay/overlay settle
AVATAR_REFRESH_DAYS = int(os.environ.get("AVATAR_REFRESH_DAYS", "14"))
MEDIA_MAX_MB = float(os.environ.get("MEDIA_MAX_MB", "0"))  # 0 disables the size-based retention cap
# Database backups (instadroid/backup.py): at the end of a run, when the newest backup in BACKUP_DIR is
# at least BACKUP_EVERY_HOURS old, keeping the newest BACKUP_KEEP. 0 hours disables the automatic ones.
# The default directory sits next to the database, which guards against corruption and bad
# migrations, not disk loss: point it at another mount for that.
BACKUP_DIR = os.environ.get("BACKUP_DIR", "/db/backups")
BACKUP_EVERY_HOURS = float(os.environ.get("BACKUP_EVERY_HOURS", "24"))
BACKUP_KEEP = int(os.environ.get("BACKUP_KEEP", "7"))
MAX_STORIES_PER_RUN = int(os.environ.get("MAX_STORIES_PER_RUN", "10"))
TIME_DISTRIBUTION = os.environ.get("TIME_DISTRIBUTION", "uniform")  # uniform | lognormal | daynight
# "Local" time for the daynight distribution below — deliberately not applied anywhere by default
# (empty = leave the device's own clock/timezone alone). Set this to match wherever the account's
# network traffic appears to originate; see tune-android.sh, which applies the same value to the
# device itself via `service call alarm`, and README's "Staying under the radar" for why a timezone
# that doesn't match the network's egress is worse than setting neither.
DEVICE_TIMEZONE = os.environ.get("DEVICE_TIMEZONE", "")
DAYNIGHT_QUIET_START = int(os.environ.get("DAYNIGHT_QUIET_START", "0"))  # local hour, inclusive
DAYNIGHT_QUIET_END = int(os.environ.get("DAYNIGHT_QUIET_END", "6"))  # local hour, exclusive
# Consecutive screens with no identifiable post before a run treats the feed as lost: dump, reopen
# the feed once, and stop the run if it happens again. 0 disables.
EMPTY_SCREEN_LIMIT = int(os.environ.get("EMPTY_SCREEN_LIMIT", "3"))
# Followed-accounts allowlist: periodically re-scrape the logged-in account's own Following list
# and drop any post from a username not on it — filters the suggested/algorithmic posts that leak
# in when navigation.open_following_feed() can't open the switcher and falls back to Home (see README). 0
# disables the whole feature (no navigation, no filtering) — it costs extra in-app navigation per
# refresh and is a real behavior change (dropping posts), so it's opt-in. Once enabled, filtering
# only takes effect after the first successful refresh — an empty/never-populated list means "not
# initialized yet," not "you follow nobody," so nothing is dropped until real data exists.
FOLLOWING_REFRESH_DAYS = int(os.environ.get("FOLLOWING_REFRESH_DAYS", "0"))
# Higher than MAX_SCROLLS: device.human_scroll_list()'s deliberately shorter swipe (see its docstring)
# means more screens are needed to cover the same list.
MAX_FOLLOWING_SCROLLS = int(os.environ.get("MAX_FOLLOWING_SCROLLS", "60"))
# Consecutive Following-list screens with no new username before the scroll stops (list exhausted).
FOLLOWING_LIST_EMPTY_LIMIT = int(os.environ.get("FOLLOWING_LIST_EMPTY_LIMIT", "3"))
# Minutes to wait before retrying after a transient device failure (see device.is_transient()) instead of
# sleeping a whole poll interval — one entry per retry, comma-separated; empty disables.
RETRY_DELAYS_MINUTES = [
    float(x) for x in os.environ.get("RETRY_DELAYS_MINUTES", "2,5,15").split(",") if x.strip()
]
# 0 (default): on startup, wait out whatever's left of the poll interval since the last recorded run
# before scraping, so a `docker compose up`, recreate or crash-restart isn't an extra off-schedule
# scrape (see scrape._startup_wait_seconds()). 1: scrape immediately on every start, the old behavior.
SCRAPE_ON_STARTUP = os.environ.get("SCRAPE_ON_STARTUP", "0").strip().lower() in ("1", "true", "yes")
# FreshRSS's own "online cron" actualize URL (https://.../i/?c=feed&a=actualize&user=...&token=...),
# GETed after a run stores something new so it fetches now instead of waiting out its own poll
# interval or per-feed TTL. Empty disables. Any reader with an equivalent plain-GET refresh webhook
# works here too, not just FreshRSS.
# It carries an API token, so it can come from FRESHRSS_REFRESH_URL_FILE instead.
FRESHRSS_REFRESH_URL = env_secret("FRESHRSS_REFRESH_URL")
# Selector-drift canary: every run records cards/screen, the share of cards with a real (non-weak)
# caption, and the share flagged "complete" (see parsing.is_weak_caption(), post["complete"]). If a run's
# numbers fall below SELECTOR_DRIFT_THRESHOLD of the rolling average over the last
# SELECTOR_DRIFT_BASELINE_RUNS successful runs, a run warning is raised — catching an Instagram UI
# change (a moved resource-id, a changed card layout) well before parsing goes fully blank. Needs at
# least SELECTOR_DRIFT_MIN_RUNS prior successful runs with a nonzero baseline before it judges
# anything, so a fresh DB or a quiet account doesn't false-positive on its first few runs. 0 disables.
SELECTOR_DRIFT_BASELINE_RUNS = int(os.environ.get("SELECTOR_DRIFT_BASELINE_RUNS", "10"))
SELECTOR_DRIFT_MIN_RUNS = int(os.environ.get("SELECTOR_DRIFT_MIN_RUNS", "3"))
SELECTOR_DRIFT_THRESHOLD = float(os.environ.get("SELECTOR_DRIFT_THRESHOLD", "0.5"))
# Stop a run early once redroid's container memory reaches this percent of its mem_limit, read from
# the device's own cgroup (see device._redroid_memory()). Android's lmkd never reclaims here (it judges
# against the host's RAM, see docs/INCIDENTS.md), so the scraper has to back off itself: on 2026-09-14 a run
# at the old 2g limit OOM-killed Android processes and froze the host. 0 disables.
MEMORY_GUARD_PERCENT = float(os.environ.get("MEMORY_GUARD_PERCENT", "85"))
# Failure alerts (instadroid/alerts.py). ALERT_URL receives a POST per alert raised or resolved (an ntfy
# topic URL works as is); it can carry a token, so ALERT_URL_FILE works too. Empty = no push, but open
# alerts still appear in /instagram.xml and on /status. An alert is raised for a login challenge, for
# ALERT_FAILED_RUNS failed runs in a row (0 disables), and for no new post in ALERT_NO_POSTS_HOURS
# (0, the default, disables: a quiet feed isn't necessarily a broken one).
ALERT_URL = env_secret("ALERT_URL")
ALERT_FAILED_RUNS = int(os.environ.get("ALERT_FAILED_RUNS", "3"))
ALERT_NO_POSTS_HOURS = float(os.environ.get("ALERT_NO_POSTS_HOURS", "0"))
ALERT_TIMEOUT = float(os.environ.get("ALERT_TIMEOUT", "10.0"))
FRESHRSS_REFRESH_TIMEOUT = float(os.environ.get("FRESHRSS_REFRESH_TIMEOUT", "10.0"))
