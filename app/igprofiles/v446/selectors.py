"""Selectors for Instagram 446.x.

Identical to 445 so far: the 445 selectors captured stories, a Reel with its permalink, and
recognized already-saved posts on 446.0.0.49.77 (2026-09-14, docs/NEXT.md). Override a key here once
446 is found to differ, e.g. `SELECTORS = {**SELECTORS_445, "share_id": "..."}`.
"""

from igprofiles.v445.selectors import SELECTORS as SELECTORS_445

SELECTORS = {**SELECTORS_445}
