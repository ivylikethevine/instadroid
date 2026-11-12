"""The feed server's settings, read from the environment once, at import. Read as settings.NAME; tests
re-import the package under a different environment (tests/feedclient.py)."""

import os
from datetime import tzinfo
from pathlib import Path

from shared import control, env
from shared.fileenv import env_secret

DB_PATH: str = env.env_db_path()
MEDIA_DIR: Path = env.env_media_dir()
PUBLIC_URL: str = os.environ.get("PUBLIC_URL", "http://127.0.0.1:8000").rstrip("/")
# Unset, media URLs still start with the loopback default above, while the subscription URLs in /opml
# and on /subscribe start with the address the request came in on (render.feed_base).
PUBLIC_URL_SET: bool = bool(os.environ.get("PUBLIC_URL"))
POLL_MAX_HOURS: float = env.env_poll_max_hours()
MAX_RUNS_PER_DAY: int = env.env_max_runs_per_day()
TIMEZONE: str = env.env_timezone()  # TZ: the zone the feeds spell times out in; "" = UTC
ZONE: tzinfo = env.zone(TIMEZONE)
FEED_HOST: str = os.environ.get("FEED_HOST", "127.0.0.1")
FEED_PORT: str = os.environ.get("FEED_PORT") or "8000"  # what entrypoint.sh starts the server on
# Required on every request except /health once set: as a bearer token, as the password of HTTP
# basic auth (any username), or as ?token=. Media URLs in the feeds are signed with it instead of
# carrying it, so a reader's image loads work without exposing the token itself. Empty = no auth.
FEED_TOKEN: str = env_secret("FEED_TOKEN")
# Written into /opml as each feed's FreshRSS refresh interval (frss:ttl, seconds). FreshRSS skips a feed
# whose own interval hasn't passed since its last fetch, even when the scraper's refresh ping asks, and
# its default is an hour; a short value here makes every ping fetch. 0 = leave FreshRSS's default.
OPML_FEED_TTL: int = int(os.environ.get("OPML_FEED_TTL") or "0")
MAX_LIMIT: int = 500
# The scraper's manual-control files (shared/control.py), read from the same environment the same way.
CONTROL_DIR: Path = control.env_control_dir(DB_PATH)
LOCK_MAX_HOURS: float = control.env_lock_max_hours()
RUN_NOW_MIN_MINUTES: float = control.env_run_now_min_minutes()
