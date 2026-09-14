"""Selectors for Instagram 441.x.

Starts as v442's. Override a key once `new_profile.py check v441` shows 441 differs, e.g.
`SELECTORS = {**SELECTORS_442, "share_id": "..."}`.
"""

from igprofiles.v442.selectors import SELECTORS as SELECTORS_442

SELECTORS = {**SELECTORS_442}
