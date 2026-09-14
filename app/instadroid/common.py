"""Small pure helpers shared by every module: logging, timestamps, bounds, hashing, filenames."""

import hashlib
import re
from datetime import UTC, datetime, timedelta


def log(*a):
    print(datetime.now().strftime("%H:%M:%S"), *a, flush=True)


def parse_iso(value) -> datetime | None:
    """A stored ISO timestamp as an aware datetime (naive = UTC), or None if missing/malformed."""
    try:
        parsed = datetime.fromisoformat(value)
    except TypeError, ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def older_than(value, days: float) -> bool:
    """True if the stored timestamp is missing, malformed, or more than `days` old."""
    parsed = parse_iso(value)
    return parsed is None or datetime.now(UTC) - parsed > timedelta(days=days)


_BOUNDS = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


def parse_bounds(bounds: str | None) -> tuple[int, int, int, int] | None:
    """(x1, y1, x2, y2) from a uiautomator bounds string "[x1,y1][x2,y2]", or None."""
    m = _BOUNDS.match(bounds or "")
    if not m:
        return None
    x1, y1, x2, y2 = map(int, m.groups())
    return x1, y1, x2, y2


def bounds_center(bounds: str | None) -> tuple[int, int] | None:
    if not (b := parse_bounds(bounds)):
        return None
    x1, y1, x2, y2 = b
    return (x1 + x2) // 2, (y1 + y2) // 2


def bounds_bottom_right(bounds: str | None, inset: int = 10) -> tuple[int, int] | None:
    """Point near a node's bottom-right corner: where a truncated, left-aligned caption's
    trailing "... more" span sits, on its last (and typically fullest) line."""
    if not (b := parse_bounds(bounds)):
        return None
    x1, y1, x2, y2 = b
    return max(x1, x2 - inset), max(y1, y2 - inset)


def digest(data: str | bytes) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()[:16]


_SAFE_USERNAME = re.compile(r"^[A-Za-z0-9._]+$")


def safe_filename(username: str) -> str | None:
    """Reject anything that isn't a plausible Instagram handle before it's used as a filename —
    username is parsed from screen content, not trusted input."""
    if not username or username in (".", "..") or not _SAFE_USERNAME.match(username):
        return None
    return username
