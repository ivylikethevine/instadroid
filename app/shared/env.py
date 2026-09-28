"""The settings both processes read from the environment, the scraper (instadroid/config.py) and the feed
server (feedserver/settings.py), so the two can't drift apart on a default."""

import os
from collections.abc import Mapping
from datetime import UTC, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def env_db_path(environ: Mapping[str, str] = os.environ) -> str:
    """DB_PATH: the SQLite database the scraper writes and the feed server reads."""
    return environ.get("DB_PATH", "/db/posts.sqlite")


def env_media_dir(environ: Mapping[str, str] = os.environ) -> Path:
    """MEDIA_DIR: the saved images, which the database refers to by name relative to it."""
    return Path(environ.get("MEDIA_DIR", "/media"))


def env_poll_max_hours(environ: Mapping[str, str] = os.environ) -> float:
    """POLL_MAX_HOURS: the longest sleep between runs, which the feed server's health check allows for."""
    return float(environ.get("POLL_MAX_HOURS", "4.5"))


def _zone(name: str, value: str) -> str:
    """`value` when it's an IANA zone this system knows (a leading ":", libc's spelling of a zone file,
    is dropped); otherwise "", with a warning for a value that isn't."""
    key: str = value.strip().removeprefix(":")
    if not key:
        return ""
    try:
        _: ZoneInfo = ZoneInfo(key)
    except ValueError, OSError, ZoneInfoNotFoundError:
        print(f"WARN: unknown {name} {value!r}; falling back to UTC", flush=True)
        return ""
    return key


def env_timezone(environ: Mapping[str, str] = os.environ) -> str:
    """TZ: the IANA zone the feeds show times in and the device is set to; "" (UTC, and the device
    left alone) when unset."""
    return _zone("TZ", environ.get("TZ", ""))


def env_device_timezone(environ: Mapping[str, str] = os.environ) -> str:
    """DEVICE_TIMEZONE: the device's zone where it differs from TZ, which it defaults to."""
    return _zone("DEVICE_TIMEZONE", environ.get("DEVICE_TIMEZONE", "")) or env_timezone(environ)


def zone(name: str) -> tzinfo:
    """The zone one of the settings above names; UTC for ""."""
    return ZoneInfo(name) if name else UTC
