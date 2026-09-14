"""Selectors for Instagram 443.x.

Starts as v444's. Override a key once `new_profile.py check v443` shows 443 differs, e.g.
`SELECTORS = {**SELECTORS_444, "share_id": "..."}`.
"""

from igprofiles.v444.selectors import SELECTORS as SELECTORS_444

SELECTORS = {**SELECTORS_444}
