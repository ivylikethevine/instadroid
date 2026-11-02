"""new_profile's host state, as the tests of its subcommands stage it."""

import dataclasses
from typing import TypedDict, Unpack

from devtools import new_profile


class HostChanges(TypedDict, total=False):
    """Any subset of new_profile.HostState's fields."""

    redroid_running: bool
    app_running: bool
    redroid_mem: str
    host_available_mib: int


def host_state(**changes: Unpack[HostChanges]) -> new_profile.HostState:
    """A healthy host, but for `changes`."""
    healthy: new_profile.HostState = new_profile.HostState(
        redroid_running=True, app_running=False, redroid_mem="1.0GiB / 3GiB", host_available_mib=8000
    )
    return dataclasses.replace(healthy, **changes)


def decline(prompt: str) -> bool:
    """new_profile._confirm answered no."""
    return False
