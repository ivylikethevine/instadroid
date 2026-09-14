"""What every Instagram version profile provides. See docs/NEXT.md for the full design."""

from typing import Any


class BaseProfile:
    """The configuration for a range of Instagram versions, in its own igprofiles/vXYZ/ package:
    `vXYZ/__init__.py` defines a subclass named `Profile`, with selectors.py, fixtures/ for tests, and
    any behavior overrides next to it.

    A profile exists only where Instagram changed something. vXYZ covers every build from major XYZ up
    to the next profile, and the scraper runs the highest profile at or below the installed version
    (igprofiles.covering()). The lowest profile holds the full selectors; every other one subclasses
    the profile before it and overrides only what changed.

    Required on every profile:
      major        the first Instagram major version it covers (must match the directory name)
      selectors    the full selector dict the scraper reads through SELECTORS
      validated    the exact builds a live baseline run and replay fixtures showed it handles
                   (scripts/new_profile.py validate). Declared on each profile's own class, never
                   inherited: a new profile starts with none.

    Behavior overrides: a profile can replace any instadroid function marked @versioned by defining a
    method of the same name. It receives the base implementation first, so it can wrap or replace it:

        def parse_hierarchy(self, base, xml):
            posts = base(xml)
            ...
            return posts
    """

    major: int
    selectors: dict[str, Any]
    validated: tuple[str, ...] = ()
    notes: str = ""  # free text shown by `scraper.py profiles`

    @property
    def name(self) -> str:
        return f"v{self.major}"

    @property
    def own_validated(self) -> tuple[str, ...]:
        """The builds validated with this profile itself, not inherited from the one it subclasses."""
        return vars(type(self)).get("validated", ())

    def __repr__(self) -> str:
        return f"<profile {self.name}>"
