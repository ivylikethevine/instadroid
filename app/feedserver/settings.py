"""The feed server's settings, read from the environment once, at import. Read as settings.NAME; tests
re-import the package under a different environment (tests/feedclient.py)."""

import os

from shared import control, env
from shared.fileenv import env_secret

DB_PATH = env.env_db_path()
MEDIA_DIR = env.env_media_dir()
PUBLIC_URL = os.environ.get("PUBLIC_URL", "http://localhost:8000").rstrip("/")
POLL_MAX_HOURS = env.env_poll_max_hours()
FEED_HOST = os.environ.get("FEED_HOST", "127.0.0.1")
# Required on every request except /health once set: as a bearer token, as the password of HTTP
# basic auth (any username), or as ?token=. Media URLs in the feeds are signed with it instead of
# carrying it, so a reader's image loads work without exposing the token itself. Empty = no auth.
FEED_TOKEN = env_secret("FEED_TOKEN")
MAX_LIMIT = 500
# The scraper's manual-control files (shared/control.py), read from the same environment the same way.
CONTROL_DIR = control.env_control_dir(DB_PATH)
LOCK_MAX_HOURS = control.env_lock_max_hours()
RUN_NOW_MIN_MINUTES = control.env_run_now_min_minutes()
