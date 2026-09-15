"""Instagram version profiles: one package per point where Instagram's UI changed (see docs/NEXT.md
and igprofiles/base.py).

    igprofiles/
      base.py            BaseProfile, the contract every profile follows
      screens.py         which selector keys each screen needs (profile development and tests)
      v424/              __init__.py (Profile), selectors.py, fixtures/

A profile vXYZ covers Instagram XYZ.x up to the next profile, and the scraper picks the highest one at
or below the installed version (covering()). There is no profile for a version that changes nothing.
IG_PROFILE ("v424", or just "424") overrides the automatic choice.
"""

import importlib
import re
from pathlib import Path

from .base import BaseProfile

MIN_MAJOR = 424  # oldest Instagram version this project supports: the lowest profile's major
# The build installed when nothing more specific is asked for (auto-install, `scraper.py install`,
# `new_profile.py restore`). Pinned below the newest validated build on purpose: 446.0.0.49.77 has
# crashed on launch on the reference device since 2026-09-15 (docs/NEXT.md's run log). None means the
# newest validated build. tests/test_profiles.py checks it's a validated build.
DEFAULT_BUILD: str | None = "445.0.0.45.83"
_NAME = re.compile(r"^v(\d{3})$")
_ROOT = Path(__file__).parent


def major_of(version: str | None) -> int | None:
    """446 for "446.0.0.49.77"; None when there's no version (not installed) or it's unparseable."""
    m = re.match(r"(\d+)\.", version or "")
    return int(m.group(1)) if m else None


def version_key(build: str) -> tuple[int, ...]:
    """A build as a sortable tuple: "446.0.0.49.77" -> (446, 0, 0, 49, 77)."""
    return tuple(int(part.group()) for part in re.finditer(r"\d+", build))


def normalize(name: str) -> str:
    """ "445", "v445" and " V445 " all mean "v445"."""
    name = name.strip().lower()
    return name if name.startswith("v") else f"v{name}"


def available() -> list[str]:
    """Profile directory names at or above MIN_MAJOR, ascending: every vXYZ/ with an __init__.py."""
    names: list[str] = []
    for d in _ROOT.iterdir():
        m = _NAME.match(d.name)
        if m and (d / "__init__.py").is_file() and int(m.group(1)) >= MIN_MAJOR:
            names.append(d.name)
    return sorted(names)


def load(name: str) -> BaseProfile:
    """Import igprofiles/<name>/ and return an instance of its Profile. Raises ValueError for a name
    that isn't a valid, present profile, or a profile whose contents don't match its directory."""
    name = normalize(name)
    m = _NAME.match(name)
    if not m:
        raise ValueError(f"{name!r} is not a profile name (expected vXYZ, e.g. v{MIN_MAJOR})")
    if int(m.group(1)) < MIN_MAJOR:
        raise ValueError(f"{name} is below the supported floor (v{MIN_MAJOR})")
    if name not in available():
        raise ValueError(f"no profile directory igprofiles/{name}/ (available: {', '.join(available())})")
    profile_cls: object = getattr(importlib.import_module(f"igprofiles.{name}"), "Profile", None)
    if not (isinstance(profile_cls, type) and issubclass(profile_cls, BaseProfile)):
        raise ValueError(f"igprofiles/{name}/__init__.py must define Profile(BaseProfile)")
    profile = profile_cls()
    missing = [a for a in ("major", "selectors") if not hasattr(profile, a)]
    if missing:
        raise ValueError(f"{name} Profile is missing {', '.join(missing)}")
    if profile.name != name:
        raise ValueError(f"igprofiles/{name}/ defines major={profile.major}, expected {int(m.group(1))}")
    if below := [b for b in profile.own_validated if (major_of(b) or 0) < profile.major]:
        raise ValueError(f"{name} lists validated builds below {profile.major}: {', '.join(below)}")
    return profile


def covering(major: int | None) -> str | None:
    """The profile that handles Instagram `major`: the highest one at or below it. None when `major`
    is None or below every profile."""
    if major is None:
        return None
    return next((n for n in reversed(available()) if int(n[1:]) <= major), None)


def newest_build(profile: str | None = None) -> str | None:
    """The newest validated build of `profile`, or across every profile: default_build()'s fallback, and
    what check_new_builds.py compares APKPure against. None if nothing is validated."""
    names = [normalize(profile)] if profile else available()
    builds = [b for n in names for b in load(n).own_validated]
    return max(builds, key=version_key) if builds else None


def default_build(profile: str | None = None) -> str | None:
    """The build to install by default: DEFAULT_BUILD, unless `profile` is given and hasn't validated
    it, then the newest validated build (of `profile`, or of any profile)."""
    if DEFAULT_BUILD and (profile is None or DEFAULT_BUILD in load(profile).own_validated):
        return DEFAULT_BUILD
    return newest_build(profile)


def select(requested: str = "", installed: str | None = None) -> tuple[BaseProfile, str | None]:
    """(profile, warning). IG_PROFILE (`requested`) when it names a loadable profile; otherwise the
    profile covering the `installed` version, or the newest profile when nothing is installed (or its
    version is unknown). A bad IG_PROFILE, or an installed version below every profile, still returns
    a profile, with a warning, rather than stopping the scraper."""
    warning = None
    if requested.strip():
        try:
            return load(requested), None
        except ValueError as e:
            warning = f"IG_PROFILE={requested!r}: {e}; choosing by installed version instead"
    names = available()
    name = covering(major_of(installed))
    if name is None:
        if major_of(installed) is not None:
            below = f"Instagram {installed} is older than the oldest profile ({names[0]}); using it anyway"
            warning = f"{warning}; {below}" if warning else below
            name = names[0]
        else:
            name = names[-1]
    return load(name), warning


def fixture(name: str, filename: str) -> Path:
    """Path to a test fixture shipped with a profile, e.g. fixture("v424", "feed_445.xml")."""
    return _ROOT / normalize(name) / "fixtures" / filename
