"""Stored timestamps, shared by the scraper and the feed server."""

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from .sqlrows import SqlValue


def parse_iso(value: SqlValue) -> datetime | None:
    """A stored ISO timestamp as an aware datetime (naive = UTC), or None if missing/malformed.
    `value` is whatever a sqlite column happened to hold, not necessarily text; anything else counts
    as malformed. Never raises, so one bad row can't fail a whole run or feed (and feedgen rejects
    naive datetimes)."""
    if not isinstance(value, str):
        return None
    try:
        parsed: datetime = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def window_starts(stamps: Iterable[SqlValue], now: datetime) -> list[datetime]:
    """The stored timestamps inside the day before `now`, parsed; a malformed one is left out."""
    cutoff: datetime = now - timedelta(days=1)
    return [t for t in map(parse_iso, stamps) if t is not None and t >= cutoff]


def next_allowed(starts: list[datetime], limit: int) -> datetime:
    """When a run is next allowed with at least `limit` of `starts` (oldest first) in the window: once
    enough have aged out to leave the count under the limit."""
    return starts[-limit] + timedelta(days=1)
