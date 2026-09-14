"""The active Instagram version profile (see igprofiles/ and docs/NEXT.md) and the @versioned
hook that lets a profile replace any UI-dependent function."""

import functools
from collections.abc import Callable
from typing import Any

from igprofiles import BaseProfile, covering, major_of, version_key
from igprofiles import select as select_profile

from . import config
from .common import log

# PROFILE is the profile covering the installed Instagram (or IG_PROFILE), reloaded by activate_profile()
# on connect and after an install. Until a device is connected it's provisionally the newest profile.
PROFILE: BaseProfile = select_profile(config.IG_PROFILE)[0]
PROFILE_WARNING: str | None = None
_VERSIONED: set[str] = set()  # names of every @versioned function


class _ActiveSelectors:
    """SELECTORS["key"] always reads the active PROFILE's selectors, so switching profiles is just
    reassigning PROFILE."""

    def __getitem__(self, key: str) -> Any:
        return PROFILE.selectors[key]


SELECTORS = _ActiveSelectors()


def versioned(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Let an Instagram version profile replace this function: if the active PROFILE defines a method
    with the same name, calls go there instead, with this implementation passed first as `base` so
    the override can wrap or replace it. Mark anything that depends on Instagram's UI."""
    name = fn.__name__
    _VERSIONED.add(name)

    @functools.wraps(fn)
    def dispatch(*args: Any, **kwargs: Any) -> Any:
        override = getattr(PROFILE, name, None)
        if override is None:
            return fn(*args, **kwargs)
        return override(fn, *args, **kwargs)

    dispatch.base = fn  # pyright: ignore[reportAttributeAccessIssue]
    return dispatch


def _unknown_hooks(profile: BaseProfile) -> list[str]:
    """Public methods a profile defines that don't match any @versioned function: almost certainly a
    typo, and otherwise silently never called."""
    base = set(dir(BaseProfile))
    return sorted(
        a
        for a in dir(profile)
        if not a.startswith("_") and a not in base and callable(getattr(profile, a)) and a not in _VERSIONED
    )


def activate_profile(installed: str | None) -> None:
    """(Re)load PROFILE for the `installed` Instagram: the profile covering it, or IG_PROFILE. Sets
    PROFILE_WARNING for a bad IG_PROFILE, an IG_PROFILE that isn't the one covering the installed
    version, an installed major version no build of which has been validated with its profile, or a
    misnamed override."""
    global PROFILE, PROFILE_WARNING
    PROFILE, select_warning = select_profile(config.IG_PROFILE, installed)
    warnings = [select_warning] if select_warning else []
    installed_major = major_of(installed)
    if installed and config.IG_PROFILE and (expected := covering(installed_major)) != PROFILE.name:
        warnings.append(
            f"IG_PROFILE={PROFILE.name} is set, but Instagram {installed} is covered by {expected or 'no profile'}"
        )
    if installed and installed_major not in {major_of(b) for b in PROFILE.own_validated}:
        newest = max(PROFILE.own_validated, key=version_key) if PROFILE.own_validated else "none yet"
        warnings.append(
            f"Instagram {installed} hasn't been validated with profile {PROFILE.name}"
            f" (newest validated: {newest}; see docs/NEXT.md)"
        )
    if unknown := _unknown_hooks(PROFILE):
        warnings.append(
            f"profile {PROFILE.name} defines {', '.join(unknown)}, which match no @versioned function"
        )
    PROFILE_WARNING = "; ".join(warnings) or None
    log(f"profile {PROFILE.name} (installed: {installed or 'none'})")
    if PROFILE_WARNING:
        log("WARN:", PROFILE_WARNING)
