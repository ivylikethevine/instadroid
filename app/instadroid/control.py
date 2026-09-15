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

from shared import control as files
from shared import sqlrows

from . import config
from .common import log


def locked(now: float | None = None) -> bool:
    """True while a lock younger than LOCK_MAX_HOURS is in place."""
    return files.locked(config.CONTROL_DIR, config.LOCK_MAX_HOURS, now)


def set_lock(on: bool) -> None:
    files.set_lock(config.CONTROL_DIR, on)


def request_run_now() -> None:
    files.request_run_now(config.CONTROL_DIR)


def take_run_now(con: sqlite3.Connection) -> bool:
    """True, deleting the request, when a scrape-now request exists and the last run finished at least
    RUN_NOW_MIN_MINUTES ago. A request that's too early stays for a later check."""
    if not files.run_now_requested(config.CONTROL_DIR):
        return False
    # Runs are inserted as they finish, so the newest id is the latest finish (feedserver/queries.py).
    finished_at = sqlrows.scalar(con.execute("SELECT finished_at FROM runs ORDER BY id DESC LIMIT 1"))
    if not files.run_now_due(files.minutes_since(finished_at), config.RUN_NOW_MIN_MINUTES):
        return False
    (config.CONTROL_DIR / files.RUN_NOW).unlink(missing_ok=True)
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
    lock = config.CONTROL_DIR / files.LOCK
    if locked():
        log(f"{lock} is in place; holding scheduled runs until it's removed")
        while locked():
            time.sleep(config.CONTROL_POLL_SECONDS)
        log("lock removed; resuming")
    elif (age := files.lock_age(config.CONTROL_DIR)) is not None:
        log(f"WARN: ignoring {lock}: it's {age.total_seconds() / 3600:.1f}h old (LOCK_MAX_HOURS)")
