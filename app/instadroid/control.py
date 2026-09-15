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
since the last run finished, then deleted. The file protocol itself is shared/control.py, which the
feed server uses too.
"""

import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path

from shared import control as files
from shared import sqlrows

from . import config
from .common import log


def _path(name: str) -> Path:
    return Path(config.CONTROL_DIR) / name


def lock_age(now: float | None = None) -> timedelta | None:
    """How long the lock has been in place, or None when there is none."""
    return files.lock_age(config.CONTROL_DIR, now)


def locked(now: float | None = None) -> bool:
    """True while a lock younger than LOCK_MAX_HOURS is in place."""
    return files.locked(config.CONTROL_DIR, config.LOCK_MAX_HOURS, now)


def set_lock(on: bool) -> None:
    files.set_lock(config.CONTROL_DIR, on)


def request_run_now() -> None:
    files.request_run_now(config.CONTROL_DIR)


def minutes_since_last_run(con: sqlite3.Connection, now: datetime | None = None) -> float | None:
    return files.minutes_since(sqlrows.scalar(con.execute("SELECT MAX(finished_at) FROM runs")), now)


def take_run_now(con: sqlite3.Connection) -> bool:
    """True, deleting the request, when a scrape-now request exists and the last run finished at least
    RUN_NOW_MIN_MINUTES ago. A request that's too early stays for a later check."""
    path = _path(files.RUN_NOW)
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
        log(f"{_path(files.LOCK)} is in place; holding scheduled runs until it's removed")
        while locked():
            time.sleep(config.CONTROL_POLL_SECONDS)
        log("lock removed; resuming")
    elif (age := lock_age()) is not None:
        log(
            f"WARN: ignoring {_path(files.LOCK)}: it's {age.total_seconds() / 3600:.1f}h old (LOCK_MAX_HOURS)"
        )
