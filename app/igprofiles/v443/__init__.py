"""Instagram 443: scaffolded from v444 by scripts/new_profile.py on 2026-09-14.

Not validated yet. Override only what differs from v444: selector keys in selectors.py, behavior as
methods named after @versioned functions (docs/NEXT.md). `python scripts/new_profile.py validate v443`
marks it validated once a baseline run and replay fixtures show it works.
"""

from igprofiles.v444 import Profile as Profile444

from .selectors import SELECTORS


class Profile(Profile444):
    major = 443
    apk_version = "443.0.0.48.82"
    selectors = SELECTORS
    validated = True
    notes = "scaffolded from v444; not validated"
