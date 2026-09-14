"""Settings, read from the environment once at import. Code reads them as config.NAME at call
time, so tests can monkeypatch any of them."""

import os
from pathlib import Path

from fileenv import env_secret

ADB_ADDR = os.environ.get("ADB_ADDR", "127.0.0.1:5555")  # redroid's forwarded ADB port
DB_PATH = os.environ.get("DB_PATH", "/db/posts.sqlite")
MEDIA_DIR = Path(os.environ.get("MEDIA_DIR", "/media"))
DEBUG_DIR = Path(os.environ.get("DEBUG_DIR", "/debug"))
POLL_MIN_H = float(os.environ.get("POLL_MIN_HOURS", "2.5"))
POLL_MAX_H = float(os.environ.get("POLL_MAX_HOURS", "4.5"))
# Which feed to scrape. "chrono" (default): the real chronological Following feed, reached via the
# switcher — see navigation.open_following_feed(). "home": deliberately stay on the algorithmic Home feed
# instead and skip the switcher navigation entirely — e.g. pair with FOLLOWING_REFRESH_DAYS's
# allowlist to filter Home's suggested content rather than fighting the switcher for it. Any other
# value falls back to "chrono" (logged once at import).
FEED_MODE = os.environ.get("FEED_MODE", "chrono").strip().lower()
if FEED_MODE not in ("chrono", "home"):
    print(f"WARN: unknown FEED_MODE {FEED_MODE!r}; falling back to chrono", flush=True)
    FEED_MODE = "chrono"
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
MEDIA_FORMAT = os.environ.get("MEDIA_FORMAT", "webp").strip().lower()
if MEDIA_FORMAT not in ("webp", "jpeg"):
    print(f"WARN: unknown MEDIA_FORMAT {MEDIA_FORMAT!r}; falling back to webp", flush=True)
    MEDIA_FORMAT = "webp"
MEDIA_EXTS = (".jpg", ".webp")  # every extension this scraper has ever written
# Either can come from a file instead (IG_USERNAME_FILE / IG_PASSWORD_FILE, e.g. a Docker secret).
IG_USERNAME = env_secret("IG_USERNAME")
IG_PASSWORD = env_secret("IG_PASSWORD")
IG_PKG = "com.instagram.android"
# If the device has no Instagram installed, navigation.ensure_logged_in() fetches it with apkeep (built into
# the image, see Dockerfile) and adb-installs it, instead of just raising — this is what lets the
# service recover on its own from a fresh /data volume or the /data/system-reset scenario in
# CLAUDE.md, where Instagram's package registration was orphaned but the app itself wasn't touched.
# 0/false/empty falls back to the original behavior: raise and require a manual `adb install`.
IG_AUTO_INSTALL = os.environ.get("IG_AUTO_INSTALL", "1").strip().lower() not in ("0", "false", "")
# Which Instagram version profile to run: a directory under igprofiles/ ("v445", or just "445").
# Everything version-specific (selectors, the APK build to install, behavior overrides) lives there.
# Empty = igprofiles.DEFAULT_PROFILE. See docs/NEXT.md.
IG_PROFILE = os.environ.get("IG_PROFILE", "").strip()
# Override the Instagram build auto-install and `scraper.py install` fetch. Empty = the active
# profile's own apk_version; "latest" = whatever apkeep resolves as latest on APKPure.
IG_APK_VERSION = os.environ.get("IG_APK_VERSION", "").strip()
APK_CACHE_DIR = Path(os.environ.get("APK_CACHE_DIR", "/apk"))
APK_FETCH_TIMEOUT = float(os.environ.get("APK_FETCH_TIMEOUT", "300"))  # apkeep's own download
DEBUG_KEEP = 12  # debug dump pairs to retain; older ones are pruned on every new dump
# Profile development (scripts/new_profile.py baseline): when set, every screen the scraper visits
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
SHARE_TAP_TRIES = int(os.environ.get("SHARE_TAP_TRIES", "2"))  # taps on the share button before giving up
CAPTION_EXPAND_TRIES = int(
    os.environ.get("CAPTION_EXPAND_TRIES", "2")
)  # taps on a truncated caption's "more"
CLIPBOARD_TIMEOUT = float(os.environ.get("CLIPBOARD_TIMEOUT", "6.0"))  # seconds to poll the clipboard for
MAX_CAROUSEL_SLIDES = int(os.environ.get("MAX_CAROUSEL_SLIDES", "10"))
VIDEO_SETTLE_SECONDS = float(os.environ.get("VIDEO_SETTLE_SECONDS", "1.5"))  # let autoplay/overlay settle
AVATAR_REFRESH_DAYS = int(os.environ.get("AVATAR_REFRESH_DAYS", "14"))
MEDIA_MAX_MB = float(os.environ.get("MEDIA_MAX_MB", "0"))  # 0 disables the size-based retention cap
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
# against the host's RAM, see CLAUDE.md), so the scraper has to back off itself: on 2026-09-14 a run
# at the old 2g limit OOM-killed Android processes and froze the host. 0 disables.
MEMORY_GUARD_PERCENT = float(os.environ.get("MEMORY_GUARD_PERCENT", "85"))
FRESHRSS_REFRESH_TIMEOUT = float(os.environ.get("FRESHRSS_REFRESH_TIMEOUT", "10.0"))
