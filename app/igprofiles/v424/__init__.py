"""Instagram 424 onward: the root profile, holding the full selectors.

The selectors were written against 445.0.0.45.83 and have since handled every build listed in
`validated` unchanged, 2026-09-14 (docs/RUNLOG.md). A later version that changes something
gets its own profile subclassing this one.
"""

from igprofiles.base import BaseProfile, Selectors

from .selectors import SELECTORS


class Profile(BaseProfile):
    major: int = 424
    selectors: Selectors = SELECTORS
    validated: tuple[str, ...] = (
        "424.0.0.49.64",
        "440.1.0.46.86",
        "441.0.0.43.81",
        "442.0.0.46.79",
        "443.0.0.48.82",
        "444.0.0.46.85",
        "445.0.0.45.83",
        "446.0.0.49.77",
    )
    notes: str = "root profile: login, stories, photos, carousels, Reels, permalinks"
