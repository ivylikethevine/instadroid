"""What every Instagram version profile provides. See docs/PROFILES.md for the full design."""

import re
from typing import Literal

# closed=True (PEP 728) says a selector set has exactly these keys, so iterating one (screens.py) yields
# the value types below rather than `object`. typing.TypedDict only accepts it from Python 3.15.
from typing_extensions import TypedDict


class Selectors(TypedDict, closed=True):
    """One profile's full selector set, as the scraper reads it through versioning.SELECTORS. How each
    kind of value is matched against a screen is described in screens.key_matches()."""

    header_id: str
    header_desc: re.Pattern[str]
    media_ids: tuple[str, ...]
    media_alt: re.Pattern[str]
    caption_class: str
    timestamp: re.Pattern[str]
    alt_kind: dict[str, str]
    share_id: str
    copy_link_desc: str
    story_tray_id: str
    story_item_desc: re.Pattern[str]
    story_viewer_id: str
    story_media_id: str
    story_shadow_id: str
    story_timestamp_id: str
    sheet_markers_text: list[str]
    sheet_markers_desc: list[str]
    permalink: re.Pattern[str]
    feed_switcher_desc: str
    following_text: str
    following_title_id: str
    home_tab_id: str
    profile_tab_id: str
    following_link_id: str
    following_list_screen_id: str
    follow_list_username_id: str
    login_username_hints: list[str]
    login_password_hints: list[str]
    login_button_texts: list[str]
    login_page_markers: list[str]
    welcome_existing_profile_text: str
    dismiss_texts: list[str]
    challenge_texts: list[str]
    action_bar_id: str
    feed_list_id: str
    mute_toggle_desc_prefix: str
    caption_more_suffix: re.Pattern[str]
    slide_index: re.Pattern[str]
    stray_alert_ok_text: str


type SelectorValue = str | list[str] | tuple[str, ...] | re.Pattern[str] | dict[str, str]

# The Selectors keys grouped by value type, for versioning.SELECTORS's typed lookup.
# _check_key_groups() below makes the type checker hold each group to the types declared above.
type StrKey = Literal[
    "header_id",
    "caption_class",
    "share_id",
    "copy_link_desc",
    "story_tray_id",
    "story_viewer_id",
    "story_media_id",
    "story_shadow_id",
    "story_timestamp_id",
    "feed_switcher_desc",
    "following_text",
    "following_title_id",
    "home_tab_id",
    "profile_tab_id",
    "following_link_id",
    "following_list_screen_id",
    "follow_list_username_id",
    "welcome_existing_profile_text",
    "action_bar_id",
    "feed_list_id",
    "mute_toggle_desc_prefix",
    "stray_alert_ok_text",
]
type StrListKey = Literal[
    "sheet_markers_text",
    "sheet_markers_desc",
    "login_username_hints",
    "login_password_hints",
    "login_button_texts",
    "login_page_markers",
    "dismiss_texts",
    "challenge_texts",
]
type PatternKey = Literal[
    "header_desc",
    "media_alt",
    "timestamp",
    "story_item_desc",
    "permalink",
    "caption_more_suffix",
    "slide_index",
]
type StrTupleKey = Literal["media_ids"]
type StrDictKey = Literal["alt_kind"]


def _check_key_groups(
    s: Selectors, a: StrKey, b: StrListKey, c: PatternKey, d: StrTupleKey, e: StrDictKey
) -> tuple[str, list[str], re.Pattern[str], tuple[str, ...], dict[str, str]]:
    """Never called: indexing Selectors with each group is how the type checker confirms every key in a
    group exists and holds that group's type."""
    return s[a], s[b], s[c], s[d], s[e]


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
      selectors    the full Selectors dict the scraper reads through SELECTORS
      validated    the exact builds a live baseline run and replay fixtures showed it handles
                   (devtools/new_profile.py validate). Declared on each profile's own class, never
                   inherited: a new profile starts with none.

    Behavior overrides: a profile can replace any instadroid function marked @versioned by defining a
    method of the same name. It receives the base implementation first, so it can wrap or replace it:

        def parse_hierarchy(self, base: Callable[[str], list[Post]], xml: str) -> list[Post]:
            posts = base(xml)
            ...
            return posts
    """

    major: int
    selectors: Selectors
    validated: tuple[str, ...] = ()
    notes: str = ""  # free text shown by `scraper.py profiles`

    @property
    def name(self) -> str:
        return f"v{self.major}"

    @property
    def own_validated(self) -> tuple[str, ...]:
        """The builds validated with this profile itself, not inherited from the one it subclasses."""
        return self.validated if "validated" in vars(type(self)) else ()

    def __repr__(self) -> str:
        return f"<profile {self.name}>"
