"""The manual-control files both processes read and write: the scraper's poll loop
(instadroid/control.py) and the feed server's /control routes (feedserver/control.py).

Three plain files in a control directory (the database directory by default): manual.lock holds
scheduled runs back until it's removed or LOCK_MAX_HOURS old; needs-human.hold does the same but
never expires, written by the scraper itself when Instagram wants a person (a challenge, a login
form it couldn't get past) and cleared only by an unlock; and scrape-now asks the poll loop to start
a run once RUN_NOW_MIN_MINUTES have passed since the last one finished. Each process reads its own
settings from the environment; the functions here take them as arguments."""

import os
import time
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .sqlrows import SqlValue
from .timestamps import parse_iso

LOCK = "manual.lock"
HOLD = "needs-human.hold"  # its text is the error that raised it
RUN_NOW = "scrape-now"


def env_control_dir(db_path: str, environ: Mapping[str, str] = os.environ) -> Path:
    """CONTROL_DIR, or the database's directory, which the feed server and the host share."""
    return Path(environ.get("CONTROL_DIR", "") or Path(db_path).parent)


def env_lock_max_hours(environ: Mapping[str, str] = os.environ) -> float:
    """LOCK_MAX_HOURS: an older lock counts as forgotten; 0 (the default) = never. A person who
    locked, hit a challenge and stepped away must not have the scraper resume under them."""
    return float(environ.get("LOCK_MAX_HOURS", "0"))


def env_run_now_min_minutes(environ: Mapping[str, str] = os.environ) -> float:
    """RUN_NOW_MIN_MINUTES: the rate limit for scrape-now, counted from the last run's finish."""
    return float(environ.get("RUN_NOW_MIN_MINUTES", "30"))


def lock_age(control_dir: str | Path, now: float | None = None) -> timedelta | None:
    """How long the lock has been in place, or None when there is none."""
    try:
        mtime = (Path(control_dir) / LOCK).stat().st_mtime
    except OSError:
        return None
    return timedelta(seconds=max((now or time.time()) - mtime, 0))


def locked(control_dir: str | Path, max_hours: float, now: float | None = None) -> bool:
    """True while a lock younger than `max_hours` (any age, for 0 or less) is in place, or a hold
    (which has no age limit) is."""
    if hold_reason(control_dir) is not None:
        return True
    age = lock_age(control_dir, now)
    return age is not None and (max_hours <= 0 or age < timedelta(hours=max_hours))


def set_lock(control_dir: str | Path, on: bool) -> None:
    """Put the manual lock in place, or release it. Releasing also clears a hold: unlocking is the
    one way a person says "I've dealt with it"."""
    path = Path(control_dir) / LOCK
    if on:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    else:
        path.unlink(missing_ok=True)
        (Path(control_dir) / HOLD).unlink(missing_ok=True)


def hold_reason(control_dir: str | Path) -> str | None:
    """The error text a hold was raised with, or None when there is no hold."""
    try:
        return (Path(control_dir) / HOLD).read_text().strip()
    except OSError:
        return None


def set_hold(control_dir: str | Path, reason: str) -> None:
    """Hold every run until a person unlocks: unlike the manual lock, this never expires."""
    path = Path(control_dir) / HOLD
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(reason + "\n")


def request_run_now(control_dir: str | Path) -> None:
    path = Path(control_dir) / RUN_NOW
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def run_now_requested(control_dir: str | Path) -> bool:
    return (Path(control_dir) / RUN_NOW).exists()


def minutes_since(finished_at: SqlValue, now: datetime | None = None) -> float | None:
    """Minutes since the stored timestamp `finished_at` (the last run's), or None when it's missing."""
    finished = parse_iso(finished_at)
    if finished is None:
        return None
    return ((now or datetime.now(UTC)) - finished).total_seconds() / 60


def run_now_due(since: float | None, min_minutes: float) -> bool:
    """Whether a scrape-now request may start a run, `since` minutes (minutes_since()) after the last one
    finished: once `min_minutes` have passed, or when no run has finished yet."""
    return since is None or since >= min_minutes
