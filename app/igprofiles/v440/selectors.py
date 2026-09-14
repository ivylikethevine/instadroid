"""Selectors for Instagram 440.x.

Starts as v441's. Override a key once `new_profile.py check v440` shows 440 differs, e.g.
`SELECTORS = {**SELECTORS_441, "share_id": "..."}`.
"""

from igprofiles.v441.selectors import SELECTORS as SELECTORS_441

SELECTORS = {**SELECTORS_441}
