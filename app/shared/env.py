"""The settings both processes read from the environment, the scraper (instadroid/config.py) and the feed
server (feedserver/settings.py), so the two can't drift apart on a default."""

import os
from collections.abc import Mapping
from pathlib import Path


def env_db_path(environ: Mapping[str, str] = os.environ) -> str:
    """DB_PATH: the SQLite database the scraper writes and the feed server reads."""
    return environ.get("DB_PATH", "/db/posts.sqlite")


def env_media_dir(environ: Mapping[str, str] = os.environ) -> Path:
    """MEDIA_DIR: the saved images, which the database refers to by name relative to it."""
    return Path(environ.get("MEDIA_DIR", "/media"))


def env_poll_max_hours(environ: Mapping[str, str] = os.environ) -> float:
    """POLL_MAX_HOURS: the longest sleep between runs, which the feed server's health check allows for."""
    return float(environ.get("POLL_MAX_HOURS", "4.5"))
