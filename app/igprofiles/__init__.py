"""Per-Instagram-version selector profiles (see NEXT.md).

Each profile is a class with `major` (the Instagram major version it targets) and `selectors`. A
newer profile subclasses the previous one and overrides only the keys that changed:

    class V446(V445):
        major = 446
        selectors = {**V445.selectors, "share_id": "..."}
"""

import re

from .v445 import V445

PROFILES = (V445,)  # ascending by major


def major_of(version: str | None) -> int | None:
    """446 for "446.0.0.49.77"; None when there's no version (not installed) or it's unparseable."""
    m = re.match(r"(\d+)\.", version or "")
    return int(m.group(1)) if m else None


def resolve(version: str | None, override: str = "", profiles=PROFILES):
    """Pick the profile for an installed Instagram `version`. Returns (profile, warning); warning is
    None only for an exact major-version match. `override` (IG_SELECTOR_PROFILE) forces a profile by
    major, e.g. to run the 445 selectors against a 446 device to see what broke."""
    newest = profiles[-1]
    major = major_of(version)
    if override:
        forced = next((p for p in profiles if str(p.major) == override.strip()), None)
        if forced is None:
            known = ", ".join(str(p.major) for p in profiles)
            return (
                newest,
                f"IG_SELECTOR_PROFILE={override!r} matches no profile ({known}); using {newest.major}",
            )
        if major != forced.major:
            return (
                forced,
                f"IG_SELECTOR_PROFILE forces selector profile {forced.major} (Instagram {version} installed)",
            )
        return forced, None
    if major is None:
        return newest, f"Instagram version unknown ({version!r}); using selector profile {newest.major}"
    exact = next((p for p in profiles if p.major == major), None)
    if exact:
        return exact, None
    older = [p for p in profiles if p.major < major]
    chosen = older[-1] if older else profiles[0]
    return chosen, f"no selector profile for Instagram {version}; using {chosen.major}"
