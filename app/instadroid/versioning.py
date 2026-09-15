"""The active Instagram version profile (see igprofiles/ and docs/PROFILES.md) and the @versioned
hook that lets a profile replace any UI-dependent function."""

import functools
import re
from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import Concatenate, TypeIs, overload

from igprofiles import BaseProfile, covering, major_of, newest_build
from igprofiles import select as select_profile
from igprofiles.base import PatternKey, StrDictKey, StrKey, StrListKey, StrTupleKey

from . import config
from .common import log

# PROFILE is the profile covering the installed Instagram (or IG_PROFILE), reloaded by activate_profile()
# on connect and after an install. Until a device is connected it's provisionally the newest profile.
PROFILE: BaseProfile = select_profile(config.IG_PROFILE)[0]
PROFILE_WARNING: str | None = None
_VERSIONED: set[str] = set()  # names of every @versioned function


def _set_active(profile: BaseProfile, warning: str | None) -> None:
    """Replace PROFILE and PROFILE_WARNING. They stay plain module attributes, read at call time, so a
    test can monkeypatch them like any other; this is the one place the scraper itself changes them,
    written through the module's namespace because their names mark them as constants to the checker."""
    namespace = globals()
    namespace["PROFILE"] = profile
    namespace["PROFILE_WARNING"] = warning


@contextmanager
def using(profile: BaseProfile) -> Generator[None]:
    """Make `profile` the active PROFILE for a block, then put the previous one back: how the profile
    development tools parse a dump under a profile other than the scraper's."""
    previous = PROFILE
    _set_active(profile, PROFILE_WARNING)
    try:
        yield
    finally:
        _set_active(previous, PROFILE_WARNING)


class _ActiveSelectors:
    """SELECTORS["key"] always reads the active PROFILE's selectors, so switching profiles is just
    reassigning PROFILE. The overloads give each key its value type from igprofiles.base.Selectors."""

    @overload
    def __getitem__(self, key: StrKey) -> str: ...
    @overload
    def __getitem__(self, key: StrListKey) -> list[str]: ...
    @overload
    def __getitem__(self, key: PatternKey) -> re.Pattern[str]: ...
    @overload
    def __getitem__(self, key: StrTupleKey) -> tuple[str, ...]: ...
    @overload
    def __getitem__(self, key: StrDictKey) -> dict[str, str]: ...
    def __getitem__(
        self, key: StrKey | StrListKey | PatternKey | StrTupleKey | StrDictKey
    ) -> str | list[str] | re.Pattern[str] | tuple[str, ...] | dict[str, str]:
        return PROFILE.selectors[key]


SELECTORS = _ActiveSelectors()


class Versioned[**P, R]:
    """A @versioned function: calling it runs the active PROFILE's override of the same name when there
    is one, else `base`, the function as written."""

    def __init__(self, fn: Callable[P, R]) -> None:
        functools.update_wrapper(self, fn)
        self.base = fn
        self.name: str = fn.__name__

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R:
        override: object = getattr(PROFILE, self.name, None)
        if override is None:
            return self.base(*args, **kwargs)
        if not _is_override(override, self.base):
            raise TypeError(f"profile {PROFILE.name}'s {self.name} is not callable")
        return override(self.base, *args, **kwargs)


def _is_override[**P, R](
    override: object, base: Callable[P, R]
) -> TypeIs[Callable[Concatenate[Callable[P, R], P], R]]:
    """A profile attribute named after a @versioned function is its override, which by the profile
    contract (igprofiles/base.py) takes the base implementation followed by the function's own
    arguments and returns what it does. Profiles are looked up by name at runtime, so being callable is
    all that can be checked here; `base` only supplies the signature."""
    return callable(override)


def versioned[**P, R](fn: Callable[P, R]) -> Versioned[P, R]:
    """Let an Instagram version profile replace this function: if the active PROFILE defines a method
    with the same name, calls go there instead, with this implementation passed first as `base` so
    the override can wrap or replace it. Mark anything that depends on Instagram's UI."""
    _VERSIONED.add(fn.__name__)
    return Versioned(fn)


def _unknown_hooks(profile: BaseProfile) -> list[str]:
    """Public methods a profile defines that don't match any @versioned function: almost certainly a
    typo, and otherwise silently never called."""
    base = set(dir(BaseProfile))
    return sorted(
        a
        for a in dir(profile)
        if not a.startswith("_") and a not in base and _is_method(profile, a) and a not in _VERSIONED
    )


def _is_method(profile: BaseProfile, name: str) -> bool:
    return callable(getattr(profile, name, None))


def activate_profile(installed: str | None) -> None:
    """(Re)load PROFILE for the `installed` Instagram: the profile covering it, or IG_PROFILE. Sets
    PROFILE_WARNING for a bad IG_PROFILE, an IG_PROFILE that isn't the one covering the installed
    version, an installed major version no build of which has been validated with its profile, or a
    misnamed override."""
    profile, select_warning = select_profile(config.IG_PROFILE, installed)
    warnings = [select_warning] if select_warning else []
    installed_major = major_of(installed)
    if installed and config.IG_PROFILE and (expected := covering(installed_major)) != profile.name:
        warnings.append(
            f"IG_PROFILE={profile.name} is set, but Instagram {installed} is covered by {expected or 'no profile'}"
        )
    if installed and installed_major not in {major_of(b) for b in profile.own_validated}:
        newest = newest_build(profile.name) or "none yet"
        warnings.append(
            f"Instagram {installed} hasn't been validated with profile {profile.name}"
            f" (newest validated: {newest}; see docs/PROFILES.md)"
        )
    if unknown := _unknown_hooks(profile):
        warnings.append(
            f"profile {profile.name} defines {', '.join(unknown)}, which match no @versioned function"
        )
    warning = "; ".join(warnings) or None
    _set_active(profile, warning)
    log(f"profile {profile.name} (installed: {installed or 'none'})")
    if warning:
        log("WARN:", warning)
