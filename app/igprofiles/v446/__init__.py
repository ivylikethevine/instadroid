"""Instagram 446: validated live on 2026-09-14 (docs/NEXT.md). Inherits everything from 445."""

from igprofiles.v445 import Profile as Profile445

from .selectors import SELECTORS


class Profile(Profile445):
    major = 446
    apk_version = "446.0.0.49.77"
    selectors = SELECTORS
    notes = "validated: login, stories, photos, carousels, Reels, permalinks (445 selectors unchanged)"
