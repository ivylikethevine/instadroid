"""Instagram 441: scaffolded from v442 by scripts/new_profile.py on 2026-09-14.

Not validated yet. Override only what differs from v442: selector keys in selectors.py, behavior as
methods named after @versioned functions (docs/NEXT.md). `python scripts/new_profile.py validate v441`
marks it validated once a baseline run and replay fixtures show it works.
"""

from igprofiles.v442 import Profile as Profile442

from .selectors import SELECTORS


class Profile(Profile442):
    major = 441
    apk_version = "441.0.0.43.81"
    selectors = SELECTORS
    validated = False
    notes = "scaffolded from v442; not validated"
