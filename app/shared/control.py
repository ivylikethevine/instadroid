"""The manual-control files both processes read and write: the scraper's poll loop
(instadroid/control.py) and the feed server's /control routes (feedserver/control.py).

Three plain files in a control directory (the database directory by default): manual.lock holds
scheduled runs back until it's removed or LOCK_MAX_HOURS old; needs-human.hold does the same but
never expires, written by the scraper itself when Instagram wants a person (a challenge, a login
form it couldn't get past) and cleared only by an unlock; and scrape-now asks the poll loop to start
a run once RUN_NOW_MIN_MINUTES have passed since the last one finished. A fourth, next-run, is the
poll loop telling /status when it means to start the next run. Each process reads its own settings
from the environment; the functions here take them as arguments."""

import os
import sqlite3
import time
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .sqlrows import SqlValue, scalar
from .timestamps import parse_iso

LOCK: str = "manual.lock"
HOLD: str = "needs-human.hold"  # its text is the error that raised it
RUN_NOW: str = "scrape-now"
NEXT_RUN: str = "next-run"  # its text is an ISO timestamp


def _read(control_dir: str | Path, name: str) -> str | None:
    """A control file's stripped text, or None when there's no such file."""
    try:
        return (Path(control_dir) / name).read_text().strip()
    except OSError:
        return None


def _write(control_dir: str | Path, name: str, text: str = "") -> None:
    """Write a control file, making the control directory if it isn't there yet."""
    path: Path = Path(control_dir) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


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
        mtime: float = (Path(control_dir) / LOCK).stat().st_mtime
    except OSError:
        return None
    return timedelta(seconds=max((now or time.time()) - mtime, 0))


def locked(control_dir: str | Path, max_hours: float, now: float | None = None) -> bool:
    """True while a lock younger than `max_hours` (any age, for 0 or less) is in place, or a hold
    (which has no age limit) is."""
    if hold_reason(control_dir) is not None:
        return True
    age: timedelta | None = lock_age(control_dir, now)
    return age is not None and (max_hours <= 0 or age < timedelta(hours=max_hours))


def set_lock(control_dir: str | Path, on: bool) -> None:
    """Put the manual lock in place, or release it. Releasing also clears a hold: unlocking is the
    one way a person says "I've dealt with it"."""
    if on:
        _write(control_dir, LOCK)
    else:
        (Path(control_dir) / LOCK).unlink(missing_ok=True)
        (Path(control_dir) / HOLD).unlink(missing_ok=True)


def hold_reason(control_dir: str | Path) -> str | None:
    """The error text a hold was raised with, or None when there is no hold."""
    return _read(control_dir, HOLD)


def set_hold(control_dir: str | Path, reason: str) -> None:
    """Hold every run until a person unlocks: unlike the manual lock, this never expires."""
    _write(control_dir, HOLD, reason + "\n")


def request_run_now(control_dir: str | Path) -> None:
    _write(control_dir, RUN_NOW)


def run_now_requested(control_dir: str | Path) -> bool:
    return (Path(control_dir) / RUN_NOW).exists()


def clear_run_now(control_dir: str | Path) -> None:
    (Path(control_dir) / RUN_NOW).unlink(missing_ok=True)


def set_next_run(control_dir: str | Path, at: datetime) -> None:
    """Record when the poll loop means to start its next run (now, when one is starting)."""
    _write(control_dir, NEXT_RUN, at.isoformat() + "\n")


def next_run(control_dir: str | Path) -> datetime | None:
    """When the poll loop last said its next run starts, or None when it hasn't said."""
    return parse_iso(_read(control_dir, NEXT_RUN))


def minutes_since(finished_at: SqlValue, now: datetime | None = None) -> float | None:
    """Minutes since the stored timestamp `finished_at` (the last run's), or None when it's missing."""
    finished: datetime | None = parse_iso(finished_at)
    if finished is None:
        return None
    return ((now or datetime.now(UTC)) - finished).total_seconds() / 60


def minutes_since_last_run(con: sqlite3.Connection, now: datetime | None = None) -> float | None:
    """minutes_since() the latest run finished, or None before any has or the runs table exists. Runs
    are inserted as they finish, so the newest id is the latest finish."""
    try:
        return minutes_since(
            scalar(con.execute("SELECT finished_at FROM runs ORDER BY id DESC LIMIT 1")), now
        )
    except sqlite3.OperationalError:
        return None


def run_now_due(since: float | None, min_minutes: float) -> bool:
    """Whether a scrape-now request may start a run, `since` minutes (minutes_since()) after the last one
    finished: once `min_minutes` have passed, or when no run has finished yet."""
    return since is None or since >= min_minutes
