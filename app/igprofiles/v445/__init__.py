"""Instagram 445: the validated baseline (live scrapes on 2026-09-14, docs/NEXT.md)."""

from igprofiles.base import BaseProfile

from .selectors import SELECTORS


class Profile(BaseProfile):
    major = 445
    apk_version = "445.0.0.45.83"
    selectors = SELECTORS
    notes = "validated: login, stories, posts (Reels, carousels), permalinks"
