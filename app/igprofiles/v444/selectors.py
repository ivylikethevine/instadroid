"""Selectors for Instagram 444.x.

Starts as v445's. Override a key once `new_profile.py check v444` shows 444 differs, e.g.
`SELECTORS = {**SELECTORS_445, "share_id": "..."}`.
"""

from igprofiles.v445.selectors import SELECTORS as SELECTORS_445

SELECTORS = {**SELECTORS_445}
