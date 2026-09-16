"""/control: the manual lock and scrape-now requests, as files the scraper's poll loop reads
(shared/control.py, instadroid/control.py)."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from shared import control as files

from . import queries, settings

router = APIRouter()


class ControlState(BaseModel):
    locked: bool  # scheduled runs are held, by the manual lock or by a hold
    scrape_now: bool  # a scrape-now request is waiting for the poll loop
    hold: str | None = None  # why the scraper holds itself (Instagram needs a person), else None


def current_state() -> ControlState:
    return ControlState(
        locked=files.locked(settings.CONTROL_DIR, settings.LOCK_MAX_HOURS),
        scrape_now=files.run_now_requested(settings.CONTROL_DIR),
        hold=files.hold_reason(settings.CONTROL_DIR),
    )


@router.get("/control", summary="Manual control state")
def control_state() -> ControlState:
    """Whether the manual lock holds scheduled runs, and whether a scrape-now request is pending."""
    return current_state()


@router.post("/control/lock", summary="Hold scheduled runs")
def lock() -> ControlState:
    """Stop the scraper starting runs while the device is driven by hand. A run already going finishes.
    The lock is ignored once it's LOCK_MAX_HOURS old; a hold the scraper raised itself never is."""
    files.set_lock(settings.CONTROL_DIR, True)
    return current_state()


@router.delete("/control/lock", summary="Release the lock")
def unlock() -> ControlState:
    """Release the manual lock and any hold: the scraper resumes on its schedule."""
    files.set_lock(settings.CONTROL_DIR, False)
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
    with queries.connection() as con:
        since = files.minutes_since(queries.last_finished(con))
    wait = settings.RUN_NOW_MIN_MINUTES
    if since is not None and not files.run_now_due(since, wait):
        raise HTTPException(
            429, f"the last run finished {since:.0f} min ago; try again in {wait - since:.0f} min"
        )
    files.request_run_now(settings.CONTROL_DIR)
    return current_state()
