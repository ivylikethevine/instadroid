"""Stored timestamps, shared by the scraper and the feed server."""

from datetime import UTC, datetime

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
