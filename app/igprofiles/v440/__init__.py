"""Instagram 440 onward: the root profile, holding the full selectors.

The selectors were written against 445.0.0.45.83 and have since handled every build listed in
`validated` unchanged, 2026-09-14 (docs/NEXT.md's run log). A later version that changes something
gets its own profile subclassing this one.
"""

from igprofiles.base import BaseProfile

from .selectors import SELECTORS


class Profile(BaseProfile):
    major = 440
    selectors = SELECTORS
    validated = (
        "440.1.0.46.86",
        "441.0.0.43.81",
        "442.0.0.46.79",
        "443.0.0.48.82",
        "444.0.0.46.85",
        "445.0.0.45.83",
        "446.0.0.49.77",
    )
    notes = "root profile: login, stories, photos, carousels, Reels, permalinks"
