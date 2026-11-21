"""Account groups: a TOML file naming groups of accounts, which the posts feeds serve as `?group=` and
/opml as one category each. Read again whenever the file changes, so an edit needs no restart.

    # GROUPS_FILE, local/data/db/groups.toml by default
    friends = ["alice", "bob"]
    "news and media" = ["somepaper", "@othersite"]

An account may be in several groups; a group feed holds every member's posts, while /opml lists the
account under the first group the file names it in, since a reader keeps a feed in one category."""

import os
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path

from fastapi import HTTPException

from . import settings

# Seconds, with the 503 a broken file answers: what /opml's not-ready answer sends too, so FreshRSS keeps
# a Dynamic OPML category as it was rather than muting its feeds.
RETRY_AFTER: str = "60"

type Toml = str | int | float | bool | datetime | date | time | list[Toml] | dict[str, Toml]
# tomllib.loads seen as returning TOML values, not Any: those are all it builds.
_decode: Callable[[str], dict[str, Toml]] = tomllib.loads


class GroupsError(ValueError):
    """GROUPS_FILE exists but isn't a valid groups file."""


@dataclass(frozen=True)
class Groups:
    """{group: its members, in file order}, groups in file order too."""

    members: dict[str, tuple[str, ...]]

    def first_group(self) -> dict[str, str]:
        """{username: the first group naming it}: where /opml lists that account."""
        out: dict[str, str] = {}
        name: str
        users: tuple[str, ...]
        for name, users in self.members.items():
            u: str
            for u in users:
                out.setdefault(u, name)
        return out


def username(value: str) -> str:
    """An account as the database stores it: lower case, without a leading @."""
    return value.strip().removeprefix("@").lower()


def parse(text: str) -> Groups:
    """The groups `text` names; GroupsError for TOML that isn't `name = [usernames]` lines."""
    try:
        data: dict[str, Toml] = _decode(text)
    except tomllib.TOMLDecodeError as e:
        raise GroupsError(f"not TOML: {e}") from None
    members: dict[str, tuple[str, ...]] = {}
    name: str
    value: Toml
    for name, value in data.items():
        if not name.strip():
            raise GroupsError("a group has an empty name")
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise GroupsError(f"group {name!r} is not a list of usernames")
        users: list[str] = [username(v) for v in value if isinstance(v, str)]
        if not all(users):
            raise GroupsError(f"group {name!r} has an empty username")
        members[name] = tuple(dict.fromkeys(users))
    return Groups(members)


_cache: tuple[tuple[str, int, int], Groups] | None = None


def read(path: Path) -> Groups:
    """The groups at `path`, parsed again only when its mtime or size changed; none without the file."""
    global _cache
    st: os.stat_result
    try:
        st = path.stat()
    except FileNotFoundError:
        return Groups({})
    except OSError as e:
        raise GroupsError(f"unreadable: {e}") from None
    key: tuple[str, int, int] = (str(path), st.st_mtime_ns, st.st_size)
    if _cache is None or _cache[0] != key:
        try:
            _cache = (key, parse(path.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError) as e:
            raise GroupsError(f"unreadable: {e}") from None
    return _cache[1]


def load() -> Groups:
    """The configured groups, for a route: a broken file is a 503 naming the problem."""
    try:
        return read(settings.GROUPS_FILE)
    except GroupsError as e:
        raise HTTPException(
            503, f"GROUPS_FILE {settings.GROUPS_FILE}: {e}", {"Retry-After": RETRY_AFTER}
        ) from None


def members(groups: Groups, name: str) -> tuple[str, ...]:
    """One group's members; 404 for a group the file doesn't name."""
    if name not in groups.members:
        known: str = ", ".join(groups.members) or "none configured"
        raise HTTPException(404, f"unknown group {name!r} (groups: {known})")
    return groups.members[name]
