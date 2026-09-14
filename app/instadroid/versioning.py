"""The active Instagram version profile (see igprofiles/ and docs/NEXT.md) and the @versioned
hook that lets a profile replace any UI-dependent function."""

import functools

from igprofiles import BaseProfile, major_of
from igprofiles import available as available_profiles
from igprofiles import select as select_profile

from . import config
from .common import log

# PROFILE is the IG_PROFILE profile from igprofiles/, reloaded by activate_profile() on connect and
# after an install, which also sets PROFILE_WARNING when the installed Instagram doesn't match it.
PROFILE: BaseProfile = select_profile(config.IG_PROFILE)[0]
PROFILE_WARNING: str | None = None
_VERSIONED: set[str] = set()  # names of every @versioned function


class _ActiveSelectors:
    """SELECTORS["key"] always reads the active PROFILE's selectors, so switching profiles is just
    reassigning PROFILE."""

    def __getitem__(self, key: str):
        return PROFILE.selectors[key]


SELECTORS = _ActiveSelectors()


def versioned(fn):
    """Let an Instagram version profile replace this function: if the active PROFILE defines a method
    with the same name, calls go there instead, with this implementation passed first as `base` so
    the override can wrap or replace it. Mark anything that depends on Instagram's UI."""
    name = fn.__name__
    _VERSIONED.add(name)

    @functools.wraps(fn)
    def dispatch(*args, **kwargs):
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
    """(Re)load the IG_PROFILE profile into PROFILE, and set PROFILE_WARNING for a bad
    IG_PROFILE, an installed Instagram of a different major version, or a misnamed override."""
    global PROFILE, PROFILE_WARNING
    PROFILE, config_warning = select_profile(config.IG_PROFILE)
    warnings = [config_warning] if config_warning else []
    installed_major = major_of(installed)
    if installed and installed_major != PROFILE.major:
        hint = f"run `scraper.py install` to get {PROFILE.apk_version}"
        if f"v{installed_major}" in available_profiles():
            hint += f", or set IG_PROFILE=v{installed_major}"
        warnings.append(
            f"Instagram {installed} is installed but profile {PROFILE.name} targets {PROFILE.major}.x; {hint}"
        )
    if unknown := _unknown_hooks(PROFILE):
        warnings.append(
            f"profile {PROFILE.name} defines {', '.join(unknown)}, which match no @versioned function"
        )
    PROFILE_WARNING = "; ".join(warnings) or None
    log(f"profile {PROFILE.name} (targets {PROFILE.apk_version}, installed: {installed or 'none'})")
    if PROFILE_WARNING:
        log("WARN:", PROFILE_WARNING)
