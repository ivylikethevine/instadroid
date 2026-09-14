"""Instagram version profiles: one self-contained package per Instagram major version (see
docs/NEXT.md and igprofiles/base.py).

    igprofiles/
      base.py            BaseProfile, the contract every profile follows
      v445/              __init__.py (Profile), selectors.py, fixtures/
      v446/              ...

IG_PROFILE picks one by directory name ("v445", or just "445"). Nothing switches automatically on the
installed Instagram version: the chosen profile is the source of truth, and a mismatch with what's
installed is reported as a warning.
"""

import importlib
import re
from pathlib import Path

from .base import BaseProfile

DEFAULT_PROFILE = "v446"  # moves forward as newer versions are validated
MIN_MAJOR = 440  # oldest Instagram version this project will support
_NAME = re.compile(r"^v(\d{3})$")
_ROOT = Path(__file__).parent


def major_of(version: str | None) -> int | None:
    """446 for "446.0.0.49.77"; None when there's no version (not installed) or it's unparseable."""
    m = re.match(r"(\d+)\.", version or "")
    return int(m.group(1)) if m else None


def normalize(name: str) -> str:
    """ "445", "v445" and " V445 " all mean "v445"."""
    name = name.strip().lower()
    return name if name.startswith("v") else f"v{name}"


def available() -> list[str]:
    """Profile directory names at or above MIN_MAJOR, ascending: every vXYZ/ with an __init__.py."""
    names = []
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
        raise ValueError(f"{name!r} is not a profile name (expected vXYZ, e.g. {DEFAULT_PROFILE})")
    if int(m.group(1)) < MIN_MAJOR:
        raise ValueError(f"{name} is below the supported floor (v{MIN_MAJOR})")
    if name not in available():
        raise ValueError(f"no profile directory igprofiles/{name}/ (available: {', '.join(available())})")
    profile_cls = getattr(importlib.import_module(f"igprofiles.{name}"), "Profile", None)
    if not (isinstance(profile_cls, type) and issubclass(profile_cls, BaseProfile)):
        raise ValueError(f"igprofiles/{name}/__init__.py must define Profile(BaseProfile)")
    profile = profile_cls()
    missing = [a for a in ("major", "apk_version", "selectors") if not hasattr(profile, a)]
    if missing:
        raise ValueError(f"{name} Profile is missing {', '.join(missing)}")
    if profile.name != name:
        raise ValueError(f"igprofiles/{name}/ defines major={profile.major}, expected {int(m.group(1))}")
    if major_of(profile.apk_version) != profile.major:
        raise ValueError(f"{name} apk_version {profile.apk_version!r} is not a {profile.major}.x build")
    return profile


def select(requested: str = "") -> tuple[BaseProfile, str | None]:
    """The profile IG_PROFILE asks for (DEFAULT_PROFILE when empty). A request that can't be loaded
    falls back to DEFAULT_PROFILE with a warning rather than stopping the scraper."""
    if not requested.strip():
        return load(DEFAULT_PROFILE), None
    try:
        return load(requested), None
    except ValueError as e:
        return load(DEFAULT_PROFILE), f"IG_PROFILE={requested!r}: {e}; using {DEFAULT_PROFILE}"


def fixture(name: str, filename: str) -> Path:
    """Path to a test fixture shipped with a profile, e.g. fixture("v445", "feed.xml")."""
    return _ROOT / normalize(name) / "fixtures" / filename
