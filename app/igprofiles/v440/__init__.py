"""Instagram 440: scaffolded from v441 by scripts/new_profile.py on 2026-09-14.

Not validated yet. Override only what differs from v441: selector keys in selectors.py, behavior as
methods named after @versioned functions (docs/NEXT.md). `python scripts/new_profile.py validate v440`
marks it validated once a baseline run and replay fixtures show it works.
"""

from igprofiles.v441 import Profile as Profile441

from .selectors import SELECTORS


class Profile(Profile441):
    major = 440
    apk_version = "440.1.0.46.86"
    selectors = SELECTORS
    validated = False
    notes = "scaffolded from v441; not validated"
