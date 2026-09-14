"""Instagram 444: scaffolded from v445 by scripts/new_profile.py on 2026-09-14.

Not validated yet. Override only what differs from v445: selector keys in selectors.py, behavior as
methods named after @versioned functions (docs/NEXT.md). `python scripts/new_profile.py validate v444`
marks it validated once a baseline run and replay fixtures show it works.
"""

from igprofiles.v445 import Profile as Profile445

from .selectors import SELECTORS


class Profile(Profile445):
    major = 444
    apk_version = "444.0.0.46.85"
    selectors = SELECTORS
    validated = True
    notes = "scaffolded from v445; not validated"
