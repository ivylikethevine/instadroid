"""What every Instagram version profile provides. See docs/NEXT.md for the full design."""

from typing import Any


class BaseProfile:
    """One Instagram major version's complete configuration, living in its own igprofiles/vXYZ/
    package: `vXYZ/__init__.py` defines a subclass named `Profile`, and everything version-specific
    sits next to it (selectors.py, fixtures/ for tests, and any behavior overrides).

    Required on every profile:
      major        the Instagram major version it targets (must match the directory name, >= 440)
      apk_version  the exact build `scraper.py install` and auto-install fetch for this profile
      selectors    the full selector dict the scraper reads through SELECTORS

    Behavior overrides: a profile can replace any instadroid function marked @versioned by defining a
    method of the same name. It receives the base implementation first, so it can wrap or replace it:

        def parse_hierarchy(self, base, xml):
            posts = base(xml)
            ...
            return posts

    Subclassing another version's Profile inherits its selectors and overrides; override only what
    changed.
    """

    major: int
    apk_version: str
    selectors: dict[str, Any]
    notes: str = ""  # free text shown by `scraper.py profiles`
    # False until a live baseline run and replay fixtures show the profile works (scripts/new_profile.py
    # validate). Running an unvalidated profile works, with a warning; DEFAULT_PROFILE must be validated.
    validated: bool = False

    @property
    def name(self) -> str:
        return f"v{self.major}"

    def __repr__(self) -> str:
        return f"<profile {self.name} ({self.apk_version})>"
