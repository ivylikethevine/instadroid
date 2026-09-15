"""Selectors for Instagram 424 onward (the root profile).

Moved verbatim from scraper.py's original SELECTORS dict (written against 445.0.0.45.83), plus the few
UI strings that used to be hardcoded inline in scraper.py. When a later Instagram changes one, don't
edit it here: fork a profile for that version (scripts/new_profile.py fork) and override the key there.
"""

import re

from igprofiles.base import Selectors

SELECTORS: Selectors = {
    # Every feed card has a header ViewGroup whose content-desc reads e.g.
    #   "some.artist posted a video in Rich's Diner 21 hours ago"
    #   "some_club posted a carousel in Anytown, Somewhere 3 days ago"
    #   "someone posted a photo August 29"
    "header_id": "row_feed_profile_header",
    "header_desc": re.compile(
        r"^(?P<user>[\w.]+) posted (?:an? )?(?P<kind>\w+)(?: in (?P<place>.+?))?"
        r" (?P<date>\d+ (?:second|minute|hour|day|week)s? ago|[A-Z][a-z]+ \d{1,2}(?:, \d{4})?|Yesterday)$"
    ),
    # Resource-id substrings marking the media area of a card (used for the screenshot crop).
    "media_ids": (
        "carousel_media_group",
        "media_group",
        "row_feed_photo_imageview",
        "zoomable_view_container",
    ),
    # Content-desc on the media itself ("Reel by Some Artist, Liked by ..., August 29" / "Photo 1 of 7 by ...").
    "media_alt": re.compile(r"^(Photo|Video|Reel|Image|Carousel)\b", re.I),
    # Caption widget ("<user> text… more"), share button, and the share sheet's Copy link entry.
    "caption_class": "com.instagram.ui.widget.textview.IgTextLayoutView",
    "timestamp": re.compile(
        r"^(\d+ (?:second|minute|hour|day|week)s? ago|[A-Z][a-z]+ \d{1,2}(?:, \d{4})?|Yesterday)$"
    ),
    # Map the media description's leading word to the header's kind vocabulary.
    "alt_kind": {
        "reel": "video",
        "video": "video",
        "photo": "photo",
        "image": "photo",
        "carousel": "carousel",
    },
    "share_id": "row_feed_button_share",
    "copy_link_desc": "Copy link",
    # The Home feed's story tray (not present on the Following screen). Each item's content-desc
    # is "<user>'s story, <index> of <total>, Unseen."/"...Seen." — index 0 is always the logged-in
    # account's own story.
    "story_tray_id": "reels_tray_container",
    "story_item_desc": re.compile(
        r"^(?P<user>[\w.]+)'s story, (?P<index>\d+) of (?P<total>\d+), (?P<seen>\w+)\.$"
    ),
    "story_viewer_id": "reel_viewer_root",
    "story_media_id": "reel_viewer_media_container",
    # The gradient behind the username/timestamp header, overlaid on the media itself — its bottom
    # edge is where the crop should start, so the saved image doesn't bake in timestamp text that
    # changes hour to hour (see capture_story_media()).
    "story_shadow_id": "reel_viewer_top_shadow",
    "story_timestamp_id": "reel_viewer_timestamp",
    # Anything that means a share/bottom sheet is open. We never interact inside one except to
    # tap "Copy link"; a stray tap there could message a contact.
    "sheet_markers_text": ["Write a message…"],
    "sheet_markers_desc": ["New group"],
    "permalink": re.compile(r"https://www\.instagram\.com/(?P<type>p|reel|reels|tv)/(?P<code>[\w-]+)"),
    # The "Home ⌄" title button at the top of the feed opens the Following/Favorites chooser.
    "feed_switcher_desc": "Instagram Home Feed",
    "following_text": "Following",
    # The Following feed is its own screen: Back button + action_bar_title "Following".
    "following_title_id": "action_bar_title",
    # The bottom tab bar's own Home tab — for FEED_MODE=home, the deliberate alternative to the
    # switcher-based navigation above.
    "home_tab_id": "feed_tab",
    # Own-profile navigation, for the followed-accounts allowlist (FOLLOWING_REFRESH_DAYS). The
    # bottom tab bar's own-avatar tab; the "N following" stacked-avatar link on that profile; the
    # Following-list screen itself (its view pager, present as soon as the screen loads regardless
    # of list content); and each row's username. All confirmed live against a real account/device
    # 2026-09-11 — see refresh_following_list().
    "profile_tab_id": "profile_tab",
    "following_link_id": "profile_header_following_stacked_familiar",
    "following_list_screen_id": "unified_follow_list_view_pager",
    "follow_list_username_id": "follow_list_username",
    # Login screen (Jetpack Compose, no ids): matched by hint text. Several candidates each.
    "login_username_hints": [
        "Mobile number or email",
        "Username, email or mobile number",
        "Phone number, username or email",
        "Username, email address or mobile number",
    ],
    "login_password_hints": ["Password"],
    "login_button_texts": ["Log in", "Log In"],
    "login_page_markers": ["Log in", "Log In", "Forgot password?"],
    # The logged-out "Join Instagram" welcome screen (shown before the actual login form, e.g.
    # after a fresh install or an invalidated session) has neither a login form nor the markers
    # above, so it must be detected and tapped through separately.
    "welcome_existing_profile_text": "I already have a profile",
    # Post-login interstitials and the buttons that dismiss them.
    "dismiss_texts": ["Not now", "Not Now", "Skip", "Save", "Continue", "Don’t allow", "Cancel", "OK"],
    # Anything matching these means a human has to intervene.
    "challenge_texts": [
        "confirmation code",
        "Confirm it's you",
        "Suspicious login",
        "security code",
        "Enter the code",
        "We Detected An Unusual Login",
        "Help us confirm it's you",
    ],
    # --- Formerly hardcoded inline in scraper.py; same values, moved here unchanged. ---
    # The action bar floating over the feed list; its bottom edge is where post crops start.
    "action_bar_id": "action_bar_container",
    # The feed RecyclerView itself (full resource-id, not a suffix).
    "feed_list_id": "android:id/list",
    # A header-less Reel card's mute toggle ("Turn sound on"/"Turn sound off").
    "mute_toggle_desc_prefix": "Turn sound",
    # A truncated caption's trailing "… more" link.
    "caption_more_suffix": re.compile(r"\s*(?:…|\.\.\.)?\s*more$"),
    # A carousel slide's media description ("Photo 2 of 7 by ...").
    "slide_index": re.compile(r"^(?:Photo|Video)\s+(\d+)\s+of\s+(\d+)\b", re.I),
    # A stray "Enter your password"-style alert left over from a previous login attempt.
    "stray_alert_ok_text": "OK",
}
