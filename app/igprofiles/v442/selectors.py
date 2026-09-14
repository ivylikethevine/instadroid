"""Selectors for Instagram 442.x.

Starts as v443's. Override a key once `new_profile.py check v442` shows 442 differs, e.g.
`SELECTORS = {**SELECTORS_443, "share_id": "..."}`.
"""

from igprofiles.v443.selectors import SELECTORS as SELECTORS_443

SELECTORS = {**SELECTORS_443}
