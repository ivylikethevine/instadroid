"""Instagram 446: partially validated (docs/NEXT.md). Inherits everything from 445 so far."""

from igprofiles.v445 import Profile as Profile445

from .selectors import SELECTORS


class Profile(Profile445):
    major = 446
    apk_version = "446.0.0.49.77"
    selectors = SELECTORS
    notes = "partially validated: login, stories, Reels, permalinks; new photos/carousels untested"
