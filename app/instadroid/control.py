"""Manual control of the poll loop: a lock that holds scheduled runs back while someone drives the
device by hand, and a "scrape now" request that cuts the wait short.

Both are plain files in CONTROL_DIR (the database directory by default, local/data/db on the host),
so they work from anywhere that can reach it:

    touch local/data/db/manual.lock      # or: scraper.py lock, or POST /control/lock
    rm local/data/db/manual.lock         # or: scraper.py unlock, or DELETE /control/lock
    touch local/data/db/scrape-now       # or: scraper.py scrape-now, or POST /control/scrape-now

The lock only stops a run from *starting*: one already in progress finishes (stopping it halfway
would leave sheets open and Instagram resident). A lock older than LOCK_MAX_HOURS counts as forgotten
and is ignored, with a warning. A scrape-now request is honoured once RUN_NOW_MIN_MINUTES have passed
since the last run finished, then deleted.
"""

import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import common, config
from .common import log

LOCK = "manual.lock"
RUN_NOW = "scrape-now"


def _path(name: str) -> Path:
    return Path(config.CONTROL_DIR) / name


def lock_age(now: float | None = None) -> timedelta | None:
    """How long the lock has been in place, or None when there is none."""
    try:
        mtime = _path(LOCK).stat().st_mtime
    except OSError:
        return None
    return timedelta(seconds=max((now or time.time()) - mtime, 0))


def locked(now: float | None = None) -> bool:
    """True while a lock younger than LOCK_MAX_HOURS is in place."""
    age = lock_age(now)
    return age is not None and (config.LOCK_MAX_HOURS <= 0 or age < timedelta(hours=config.LOCK_MAX_HOURS))


def set_lock(on: bool) -> None:
    path = _path(LOCK)
    if on:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    else:
        path.unlink(missing_ok=True)


def request_run_now() -> None:
    path = _path(RUN_NOW)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def minutes_since_last_run(con: sqlite3.Connection, now: datetime | None = None) -> float | None:
    finished = common.parse_iso(con.execute("SELECT MAX(finished_at) FROM runs").fetchone()[0])
    if finished is None:
        return None
    return ((now or datetime.now(UTC)) - finished).total_seconds() / 60


def take_run_now(con: sqlite3.Connection) -> bool:
    """True, deleting the request, when a scrape-now request exists and the last run finished at least
    RUN_NOW_MIN_MINUTES ago. A request that's too early stays for a later check."""
    path = _path(RUN_NOW)
    if not path.exists():
        return False
    since = minutes_since_last_run(con)
    if since is not None and since < config.RUN_NOW_MIN_MINUTES:
        return False
    path.unlink(missing_ok=True)
    log("scrape-now requested; starting a run")
    return True


def wait(con: sqlite3.Connection, seconds: float) -> None:
    """Sleep up to `seconds`, in CONTROL_POLL_SECONDS steps, returning early for a scrape-now request."""
    remaining = seconds
    while remaining > 0:
        if take_run_now(con):
            return
        step = min(remaining, config.CONTROL_POLL_SECONDS)
        time.sleep(step)
        remaining -= step


def wait_while_locked() -> None:
    """Hold here while the lock is in place, logging once; warn about a lock left long enough to ignore."""
    if locked():
        log(f"{_path(LOCK)} is in place; holding scheduled runs until it's removed")
        while locked():
            time.sleep(config.CONTROL_POLL_SECONDS)
        log("lock removed; resuming")
    elif (age := lock_age()) is not None:
        log(f"WARN: ignoring {_path(LOCK)}: it's {age.total_seconds() / 3600:.1f}h old (LOCK_MAX_HOURS)")
