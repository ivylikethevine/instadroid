"""Failure alerts: tell someone when the scraper needs attention, instead of waiting for them to notice
the feed went quiet.

After every recorded run, `update()` works out which conditions hold (see `conditions()`), and keeps
the `alerts` table in step: a condition that just became true is inserted and announced, one that no
longer holds is deleted and announced as resolved, and one still open stays quiet. So a login
challenge that nobody answers for a day notifies once, not every retry.

Announcements are a plain HTTP POST to ALERT_URL: the message as the body, and ntfy's Title, Tags
and Priority headers, so an ntfy topic URL (https://ntfy.sh/<topic>, or self-hosted) works as is and
any other webhook still gets the text. With no ALERT_URL, open alerts still show up where someone
already looks: an entry at the top of /instagram.xml and a line on /status (feedserver reads the table).
"""

import sqlite3
import urllib.request
from datetime import UTC, datetime, timedelta

from shared import sqlrows
from shared.errors import short_error
from shared.timestamps import parse_iso

from . import common, config
from .common import log

LOGIN, FAILING, NO_POSTS = "login", "failing", "no_posts"
TITLES = {
    LOGIN: "Instagram wants a human",
    FAILING: "scrape runs keep failing",
    NO_POSTS: "no new posts",
}
# Errors navigation.ensure_logged_in() raises when a person has to step in: a challenge, a login form
# it couldn't fill or get past, or missing credentials.
_NEEDS_HUMAN = ("wants a human", "login page shown", "login screen shown", "still on login screen")


def needs_human(error: str | None) -> bool:
    """Whether a run's recorded error is one only a person can clear (so the scraper holds itself,
    see instadroid/control.py, rather than retrying it every poll)."""
    return error is not None and any(marker in error for marker in _NEEDS_HUMAN)


def conditions(con: sqlite3.Connection, now: datetime | None = None) -> dict[str, str]:
    """{kind: message} for every alert condition that holds right now."""
    now = now or datetime.now(UTC)
    found: dict[str, str] = {}
    window: int = max(config.ALERT_FAILED_RUNS, 1)
    runs: list[str | None] = [
        sqlrows.cell_str(r, 0)
        for r in sqlrows.fetch_all(con.execute("SELECT error FROM runs ORDER BY id DESC LIMIT ?", (window,)))
    ]
    latest_error: str | None = runs[0] if runs else None
    if latest_error is not None and needs_human(latest_error):
        found[LOGIN] = f"finish it in scrcpy: {short_error(latest_error, 300)}"
    if config.ALERT_FAILED_RUNS > 0 and len(runs) >= config.ALERT_FAILED_RUNS and all(runs):
        found[FAILING] = (
            f"the last {config.ALERT_FAILED_RUNS} runs failed; latest: {short_error(latest_error or '', 300)}"
        )
    if config.ALERT_NO_POSTS_HOURS > 0:
        cutoff: datetime = now - timedelta(hours=config.ALERT_NO_POSTS_HOURS)
        first_run: datetime | None = parse_iso(
            sqlrows.scalar(con.execute("SELECT MIN(started_at) FROM runs"))
        )
        newest_post: datetime | None = parse_iso(
            sqlrows.scalar(con.execute("SELECT MAX(scraped_at) FROM posts"))
        )
        # Only once the scraper has been running for the whole window, so a fresh install is quiet.
        if first_run and first_run < cutoff and (newest_post is None or newest_post < cutoff):
            found[NO_POSTS] = f"no new post stored in {config.ALERT_NO_POSTS_HOURS:g}h"
    return found


def notify(kind: str, message: str, resolved: bool = False) -> str | None:
    """POST one announcement to ALERT_URL. Best-effort: returns a short error instead of raising, and
    never logs the URL's query string or credentials. None when disabled or delivered."""
    if not config.ALERT_URL:
        return None
    title: str = f"instadroid: {'resolved: ' if resolved else ''}{TITLES[kind]}"
    request: urllib.request.Request = urllib.request.Request(
        config.ALERT_URL,
        data=message.encode(),
        method="POST",
        headers={
            "Title": title,
            "Tags": "white_check_mark" if resolved else "warning",
            "Priority": "default" if resolved or kind != LOGIN else "high",
            "Content-Type": "text/plain; charset=utf-8",
        },
    )
    error: str | None
    if error := common.send_best_effort("alert", request, config.ALERT_TIMEOUT):
        return error
    log(f"sent alert: {title}")
    return None


def update(con: sqlite3.Connection, now: datetime | None = None) -> list[str]:
    """Bring the alerts table in line with `conditions()`, announcing each change. Returns the
    delivery errors, if any."""
    now = now or datetime.now(UTC)
    current: dict[str, str] = conditions(con, now)
    open_alerts: dict[str, str] = {
        sqlrows.must_str(r, "kind"): sqlrows.must_str(r, "message")
        for r in sqlrows.fetch_all(con.execute("SELECT kind, message FROM alerts"))
    }
    errors: list[str | None] = []
    for kind, message in current.items():
        if kind not in open_alerts:
            con.execute(
                "INSERT INTO alerts (kind, message, raised_at) VALUES (?, ?, ?)",
                (kind, message, now.isoformat()),
            )
            log(f"ALERT: {TITLES[kind]}: {message}")
            errors.append(notify(kind, message))
        elif open_alerts[kind] != message:
            con.execute("UPDATE alerts SET message = ? WHERE kind = ?", (message, kind))
    for kind in open_alerts.keys() - current.keys():
        con.execute("DELETE FROM alerts WHERE kind = ?", (kind,))
        log(f"alert resolved: {TITLES[kind]}")
        errors.append(notify(kind, f"{TITLES[kind]}: resolved", resolved=True))
    con.commit()
    return [e for e in errors if e]
