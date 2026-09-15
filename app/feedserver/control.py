"""/control: the manual lock and scrape-now requests, as files the scraper's poll loop reads
(instadroid/control.py)."""

import time
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import queries, render, settings

router = APIRouter()


class ControlState(BaseModel):
    locked: bool  # scheduled runs are held
    scrape_now: bool  # a scrape-now request is waiting for the poll loop


def current_state() -> ControlState:
    lock_file = settings.CONTROL_DIR / "manual.lock"
    try:
        age = time.time() - lock_file.stat().st_mtime
        locked = settings.LOCK_MAX_HOURS <= 0 or age < settings.LOCK_MAX_HOURS * 3600
    except OSError:
        locked = False
    return ControlState(locked=locked, scrape_now=(settings.CONTROL_DIR / "scrape-now").exists())


@router.get("/control", summary="Manual control state")
def control_state() -> ControlState:
    """Whether the manual lock holds scheduled runs, and whether a scrape-now request is pending."""
    return current_state()


@router.post("/control/lock", summary="Hold scheduled runs")
def lock() -> ControlState:
    """Stop the scraper starting runs while the device is driven by hand. A run already going finishes.
    The lock is ignored once it's LOCK_MAX_HOURS old."""
    settings.CONTROL_DIR.mkdir(parents=True, exist_ok=True)
    (settings.CONTROL_DIR / "manual.lock").touch()
    return current_state()


@router.delete("/control/lock", summary="Release the lock")
def unlock() -> ControlState:
    (settings.CONTROL_DIR / "manual.lock").unlink(missing_ok=True)
    return current_state()


@router.post(
    "/control/scrape-now",
    status_code=202,
    summary="Ask for a run now",
    responses={409: {"description": "The manual lock is in place."}, 429: {"description": "Too soon."}},
)
def scrape_now() -> ControlState:
    """Ask the poll loop to start a run within about 30 seconds instead of waiting out its sleep.
    Refused while locked, and within RUN_NOW_MIN_MINUTES of the last run finishing."""
    if current_state().locked:
        raise HTTPException(409, "the manual lock is in place; release it first")
    if finished := render.parse_dt(queries.last_finished()):
        since = (datetime.now(UTC) - finished).total_seconds() / 60
        wait = settings.RUN_NOW_MIN_MINUTES
        if since < wait:
            raise HTTPException(
                429, f"the last run finished {since:.0f} min ago; try again in {wait - since:.0f} min"
            )
    settings.CONTROL_DIR.mkdir(parents=True, exist_ok=True)
    (settings.CONTROL_DIR / "scrape-now").touch()
    return current_state()
