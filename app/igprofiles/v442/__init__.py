"""Instagram 442: scaffolded from v443 by scripts/new_profile.py on 2026-09-14.

Not validated yet. Override only what differs from v443: selector keys in selectors.py, behavior as
methods named after @versioned functions (docs/NEXT.md). `python scripts/new_profile.py validate v442`
marks it validated once a baseline run and replay fixtures show it works.
"""

from igprofiles.v443 import Profile as Profile443

from .selectors import SELECTORS


class Profile(Profile443):
    major = 442
    apk_version = "442.0.0.46.79"
    selectors = SELECTORS
    validated = True
    notes = "scaffolded from v443; not validated"
