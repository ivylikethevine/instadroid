"""Failure alerts: tell someone when the scraper needs attention, instead of waiting for them to notice
the feed went quiet.

After every recorded run, `update()` works out which conditions hold (see `conditions()`), and keeps
the `alerts` table in step: a condition that just became true is inserted and announced, one that no
longer holds is deleted and announced as resolved, and one still open stays quiet. So a login
challenge that nobody answers for a day notifies once, not every retry.

Announcements go to each URL in ALERT_URL (whitespace-separated). An http(s) URL gets a plain POST:
the message as the body, and ntfy's Title, Tags and Priority headers, so an ntfy topic URL
(https://ntfy.sh/<topic>, or self-hosted) works as is and any other webhook still gets the text. Any
other scheme is an Apprise URL (discord://, tgram://, matrix://, gotify://, mailto://, ...), sent
through Apprise. With no ALERT_URL, open alerts still show up where someone already looks: an entry at
the top of /instagram.xml and a line on /status (feedserver reads the table).
"""

import logging
import sqlite3
import urllib.request
from datetime import UTC, datetime, timedelta
from typing import Protocol
from urllib.parse import urlsplit

import igprofiles
from shared import sqlrows
from shared.errors import short_error
from shared.timestamps import parse_iso

from . import common, config, db
from .common import log

LOGIN: str = "login"
RESTRICTED: str = "restricted"
FAILING: str = "failing"
NO_POSTS: str = "no_posts"
RENAME: str = "rename"
FOLLOWING: str = "following"
NEWER_BUILD: str = "newer_build"
TITLES: dict[str, str] = {
    RENAME: "an account seems to have a new username",
    FOLLOWING: "the Following list came back much shorter",
    NEWER_BUILD: "a newer validated Instagram build exists",
    LOGIN: "Instagram wants a human",
    RESTRICTED: "Instagram is limiting the account",
    FAILING: "scrape runs keep failing",
    NO_POSTS: "no new posts",
}
# The error navigation.check_restricted() raises when Instagram is limiting the account.
_RESTRICTED: str = "restricted the account"
# Errors that need a person to step in: a restriction, and what navigation.ensure_logged_in() raises
# for a challenge, a login form it couldn't fill or get past, or missing credentials.
_NEEDS_HUMAN: tuple[str, ...] = (
    _RESTRICTED,
    "wants a human",
    "login page shown",
    "login screen shown",
    "still on login screen",
)


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
    if latest_error is not None and _RESTRICTED in latest_error:
        found[RESTRICTED] = (
            f"leave the account alone for a day or more, then `scraper.py unlock`: {short_error(latest_error, 300)}"
        )
    elif latest_error is not None and needs_human(latest_error):
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
    held: tuple[int, int] | None
    if held := db.held_following(con):
        found[FOLLOWING] = (
            f"a refresh found {held[0]} of the {held[1]} stored accounts, so the stored list was kept;"
            " if you did unfollow them, `scraper.py following --accept`"
        )
    newer: str | None
    if newer := _newer_validated(con):
        found[NEWER_BUILD] = newer
    renames: list[tuple[str, str, int]] = db.rename_candidates(con)
    if renames:
        found[RENAME] = "; ".join(
            f"{old} now posts as {new} ({n} stored post(s) seen under it): `scraper.py rename {old} {new}`"
            for old, new, n in renames
        )
    return found


def _newer_validated(con: sqlite3.Connection) -> str | None:
    """The alert text when the installed Instagram (the latest run's) is older than the newest validated
    build, or None. Quiet when IG_APK_VERSION pins a build, or asks for the latest from APKPure."""
    if config.IG_APK_VERSION:
        return None
    installed: sqlrows.SqlValue = sqlrows.scalar(
        con.execute("SELECT ig_version FROM runs WHERE ig_version IS NOT NULL ORDER BY id DESC LIMIT 1")
    )
    newest: str | None = igprofiles.newest_build()
    if (
        not isinstance(installed, str)
        or not newest
        or igprofiles.version_key(newest) <= igprofiles.version_key(installed)
    ):
        return None
    return (
        f"Instagram {installed} is installed and {newest} is validated: snapshot /data"
        f" (scripts/snapshot-android-data.sh), then `scraper.py install {newest}`"
    )


def notify(kind: str, message: str, resolved: bool = False) -> str | None:
    """Send one announcement to every ALERT_URL entry. Best-effort: returns the failures as one short
    error instead of raising, and never logs a URL's query string, credentials or, for an Apprise URL,
    anything past its scheme. None when disabled or delivered."""
    title: str = f"instadroid: {'resolved: ' if resolved else ''}{TITLES[kind]}"
    errors: list[str] = []
    url: str
    for url in config.ALERT_URL.split():
        error: str | None = (
            _post(url, kind, title, message, resolved)
            if urlsplit(url).scheme in ("http", "https")
            else _apprise(url, title, message, resolved)
        )
        if error:
            errors.append(error)
    if config.ALERT_URL and not errors:
        log(f"sent alert: {title}")
    return "; ".join(errors) or None


def _post(url: str, kind: str, title: str, message: str, resolved: bool) -> str | None:
    """POST the announcement to a web URL: the message as the body, with ntfy's Title, Tags and Priority
    headers, so an ntfy topic URL works as is and any other webhook still gets the text. A user:password
    in the URL is sent as basic auth, which an access-controlled ntfy topic takes."""
    request: urllib.request.Request = common.web_request(url, "POST")
    request.data = message.encode()
    request.add_header("Title", title)
    request.add_header("Tags", "white_check_mark" if resolved else "warning")
    request.add_header("Priority", "high" if not resolved and kind in (LOGIN, RESTRICTED) else "default")
    request.add_header("Content-Type", "text/plain; charset=utf-8")
    return common.send_best_effort("alert", request, config.ALERT_TIMEOUT)


class _Sender(Protocol):
    """The part of apprise.Apprise used here, typed (its signatures carry untyped dicts)."""

    def add(self, services: str) -> bool: ...

    def notify(self, body: str, *, title: str, notify_type: str, timeout: float) -> object: ...


def _sender() -> _Sender:
    """A new apprise.Apprise, typed by the Protocol. Imported here, not at the top: Apprise costs the
    memory-capped app container ~18MB, which only a setup that uses it should pay."""
    import apprise

    return apprise.Apprise()


def _apprise(url: str, title: str, message: str, resolved: bool) -> str | None:
    """Send the announcement through Apprise, for any other scheme it knows (discord://, tgram://,
    matrix://, gotify://, mailto://, ...). Apprise's own log is silenced, since its messages can quote
    the URL, and this one names the scheme alone."""
    logging.getLogger("apprise").setLevel(logging.CRITICAL + 1)
    where: str = f"{urlsplit(url).scheme}://"
    sender: _Sender = _sender()
    if not sender.add(url):
        error: str = f"alert to {where} failed: not a URL Apprise can send to"
    elif not sender.notify(
        body=message,
        title=title,
        notify_type="success" if resolved else "warning",
        timeout=config.ALERT_TIMEOUT,
    ):
        error = f"alert to {where} failed"
    else:
        return None
    log("WARN:", error)
    return error


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
    kind: str
    message: str
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
