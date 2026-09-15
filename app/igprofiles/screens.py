"""Which selector keys the scraper looks for on which Instagram screen, and a check of a hierarchy dump
against them. This is what tells a new profile's baseline dumps apart from drift: a feed dump in
which `share_id` matches nothing means the share button moved, not that the account was quiet.

`required` keys are what the scraper uses to recognise or act on a screen, so one missing from a
real dump of that screen almost certainly needs a selector change. `optional` keys only show up in
some states of the screen (a truncated caption, a carousel, a headless Reel card) and are reported
without a warning. Keys in SITUATIONAL belong to no one screen (interstitials, challenges, the
clipboard). Every key in a profile's selectors belongs to at least one of these, which
tests/test_profiles.py checks, so a new selector key has to be placed here.

Used by scripts/new_profile.py (`check`) and tests/test_replay.py; never touches a device.
"""

import re
from dataclasses import dataclass, field

from lxml import etree

from .base import Selectors, SelectorValue


@dataclass(frozen=True)
class Screen:
    required: tuple[str, ...]
    optional: tuple[str, ...] = ()
    description: str = ""


SCREENS: dict[str, Screen] = {
    "feed": Screen(
        required=("feed_list_id", "header_id", "header_desc", "share_id", "media_ids"),
        # timestamp sits under the caption, off screen whenever a tall Reel fills it (seen live on 444).
        optional=(
            "timestamp",
            "action_bar_id",
            "caption_class",
            "media_alt",
            "caption_more_suffix",
            "slide_index",
            "mute_toggle_desc_prefix",
        ),
        description="a feed with post cards (Following or Home), one screen of the scroll loop",
    ),
    "home_feed": Screen(
        required=("home_tab_id", "story_tray_id", "story_item_desc"),
        optional=("feed_switcher_desc", "profile_tab_id"),
        description="the top of the Home feed: tab bar, story tray and the feed switcher",
    ),
    "feed_switch_menu": Screen(
        required=("following_text",),
        description="the chooser the Home title opens (Following / Favorites)",
    ),
    "following_feed": Screen(
        required=("following_title_id", "following_text"),
        description="the Following feed's own screen, with its title bar",
    ),
    "story_viewer": Screen(
        required=("story_viewer_id", "story_media_id"),
        optional=("story_shadow_id", "story_timestamp_id"),
        description="one story open in the viewer",
    ),
    "share_sheet": Screen(
        required=("copy_link_desc",),
        optional=("sheet_markers_text", "sheet_markers_desc"),
        description="a post's share sheet",
    ),
    "profile": Screen(
        required=("profile_tab_id", "following_link_id"),
        description="the logged-in account's own profile",
    ),
    "following_list": Screen(
        required=("following_list_screen_id", "follow_list_username_id"),
        description="the logged-in account's Following list",
    ),
    "login": Screen(
        required=("login_password_hints",),
        optional=("login_username_hints", "login_button_texts", "login_page_markers"),
        description="the login form",
    ),
}

# Not tied to one screen: interstitials and challenges that may appear anywhere, the logged-out
# welcome screen, a leftover alert, the clipboard's permalink, and a lookup table.
SITUATIONAL = (
    "dismiss_texts",
    "challenge_texts",
    "welcome_existing_profile_text",
    "stray_alert_ok_text",
    "permalink",
    "alt_kind",
)

# Debug dump names the scraper already writes (diagnostics.dump_debug) and the screen each one shows,
# or is supposed to show: "last" and "empty_feed0" are feed screens that parsed nothing.
_DUMP_SCREENS = (
    (re.compile(r"^(last|empty_feed\d*|feed_switch)$"), "feed"),
    (re.compile(r"^feed_switch_menu\d*$"), "feed_switch_menu"),
    (re.compile(r"^home_feed_open$"), "home_feed"),
    (re.compile(r"^following_list_profile\d*$"), "profile"),
    (re.compile(r"^following_list_(open|first)$"), "following_list"),
    (re.compile(r"^login$"), "login"),
    (re.compile(r"^share_sheet$"), "share_sheet"),
    (re.compile(r"^story_.+$"), "story_viewer"),
)


def screen_of_dump(name: str) -> str | None:
    """The screen a dump_debug() name belongs to ("empty_feed1" -> "feed"), or None (e.g. "manual")."""
    return next((screen for pattern, screen in _DUMP_SCREENS if pattern.match(name)), None)


def screen_of_fixture(name: str) -> str:
    """The screen a fixture shows, from its name: "feed_444" -> "feed", "home_feed" -> "home_feed"."""
    return re.sub(r"_\d{3}$", "", name)


def _strings(nodes: list[etree._Element]) -> list[str]:
    return [v for n in nodes for v in (n.get("text"), n.get("content-desc")) if v]


def key_matches(key: str, value: SelectorValue, nodes: list[etree._Element]) -> bool | None:
    """Whether selector `key` finds anything among `nodes`, using the same kind of comparison the
    scraper does: resource-id suffix for *_id(s), class name for caption_class, exact text or
    content-desc for strings and lists of strings, a regex match on text/content-desc for patterns.
    None for a value that isn't matched against the screen at all (a dict)."""
    if isinstance(value, dict):
        return None
    if isinstance(value, re.Pattern):
        test = value.search if key == "caption_more_suffix" else value.match
        return any(test(s) for s in _strings(nodes))
    values = [value] if isinstance(value, str) else [str(v) for v in value]
    if key.endswith(("_id", "_ids")):
        ids = [n.get("resource-id") or "" for n in nodes]
        return any(rid == v or rid.endswith(f"/{v}") for rid in ids for v in values)
    if key == "caption_class":
        return any(n.get("class") in values for n in nodes)
    strings = _strings(nodes)
    if key.endswith("_prefix"):
        return any(s.startswith(v) for s in strings for v in values)
    return any(s in values for s in strings)


def instagram_nodes(nodes: list[etree._Element]) -> int:
    """Nodes carrying an Instagram resource-id."""
    return sum(1 for n in nodes if (n.get("resource-id") or "").startswith("com.instagram.android:id/"))


@dataclass
class ScreenCheck:
    screen: str | None
    matched: list[str] = field(default_factory=list)
    missing_required: list[str] = field(default_factory=list)
    missing_optional: list[str] = field(default_factory=list)
    instagram_nodes: int = 0

    @property
    def looks_empty(self) -> bool:
        """Nothing expected matched and at most a couple of Instagram resource-ids: not really an
        Instagram screen, e.g. a popup holding focus (seen live: an empty 22px context_menu), the
        launcher, a crash dialog. A small menu that does show its expected text isn't empty."""
        spec = SCREENS.get(self.screen or "")
        expected = (*spec.required, *spec.optional) if spec else ()
        return self.instagram_nodes <= 2 and not any(k in self.matched for k in expected)

    @property
    def ok(self) -> bool:
        return not self.missing_required and not self.looks_empty


def check_screen(xml: str, screen: str | None, selectors: Selectors) -> ScreenCheck:
    """Match every selector key against one dump, and sort the expected ones for `screen` (None: no
    expectations, e.g. a manual dump) into matched and missing."""
    nodes = list(etree.fromstring(xml.encode()).iter("node"))
    matched = sorted(k for k, v in selectors.items() if key_matches(k, v, nodes))
    spec = SCREENS.get(screen or "")
    return ScreenCheck(
        screen=screen,
        matched=matched,
        missing_required=[k for k in spec.required if k not in matched] if spec else [],
        missing_optional=[k for k in spec.optional if k not in matched] if spec else [],
        instagram_nodes=instagram_nodes(nodes),
    )
