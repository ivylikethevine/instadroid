"""End-to-end-ish tests of the device-driving code (login, feed navigation, share sheet, carousels,
stories, the scrape loop, main()) against tests.fakedevice.FakeDevice. Screens are synthetic."""

import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import adbutils
import pytest
import scraper

from tests.fakedevice import FakeDevice, hierarchy, node

CAPTION = scraper.SELECTORS["caption_class"]
ACTION_BAR = node("action_bar_container", bounds=(0, 142, 1080, 289))
FOLLOWING_TITLE = node(
    "action_bar_title", cls="android.widget.TextView", text="Following", bounds=(150, 160, 500, 270)
)
TOP_URL = "https://www.instagram.com/reel/TOP123/?igsh=abc"
OTHER_URL = "https://www.instagram.com/p/OTHER1/?igsh=xyz"
PROFILE_TAB = node("profile_tab", desc="Profile", bounds=(864, 2088, 1080, 2214), goto="profile")


def story_button(user, index, seen, x, goto=None):
    desc = f"{user}'s story, {index} of 3, {'Seen' if seen else 'Unseen'}."
    return node(cls="android.widget.Button", desc=desc, bounds=(x, 300, x + 180, 480), goto=goto)


def home_screen(switcher_goto="menu", extra=()):
    return hierarchy(
        ACTION_BAR,
        node(desc="Instagram Home Feed", bounds=(0, 150, 400, 280), goto=switcher_goto),
        node(
            "reels_tray_container",
            bounds=(0, 289, 1080, 500),
            children=[
                story_button("me", 0, True, 0),
                story_button("alice", 1, False, 200, goto="story_alice"),
                story_button("bob", 2, False, 400, goto=""),  # tap never opens the viewer
                story_button("carol", 3, True, 600),
            ],
        ),
        node("android:id/list", bounds=(0, 500, 1080, 2200)),
        node("feed_tab", bounds=(0, 2200, 216, 2340)),
        PROFILE_TAB,
        *extra,
    )


MENU = hierarchy(
    node(cls="android.widget.TextView", text="Following", bounds=(0, 1800, 1080, 1900), goto="following"),
    node(cls="android.widget.TextView", text="Favorites", bounds=(0, 1900, 1080, 2000)),
)

STORY = hierarchy(
    node(
        "reel_viewer_root",
        bounds=(0, 0, 1080, 2340),
        children=[
            node("reel_viewer_media_container", bounds=(0, 150, 1080, 2200)),
            node("reel_viewer_top_shadow", bounds=(0, 150, 1080, 400)),
            node(
                "reel_viewer_timestamp", cls="android.widget.TextView", text="5h", bounds=(200, 200, 300, 250)
            ),
        ],
    )
)


def feed_cards(slide=1, top_share="share_top", other_share="share_other"):
    """A header-less Reel (header scrolled off) above a full carousel card."""
    reel_desc = "Reel by Someone Nice, Liked by a_friend and others, 6 comments, August 29"
    return [
        node("media_group", bounds=(0, 289, 1080, 900)),
        node("row_feed_photo_imageview", desc=reel_desc, bounds=(0, 289, 1080, 900)),
        node("row_feed_button_share", bounds=(390, 900, 453, 1021), goto=top_share),
        node(cls=CAPTION, text="someone_nice Top card caption… more", bounds=(32, 1030, 1080, 1100)),
        node(cls="android.widget.TextView", text="3 days ago", bounds=(32, 1100, 300, 1140)),
        node(
            "row_feed_profile_header",
            desc="other_user posted a carousel in San Diego, California 21 hours ago",
            bounds=(0, 1150, 1080, 1287),
        ),
        node(
            "carousel_media_group",
            bounds=(0, 1287, 1080, 2000),
            children=[
                node(
                    "carousel_image",
                    desc=f"Photo {slide} of 7 by Other User, 317 likes, 10 comments",
                    bounds=(0, 1287, 1080, 2000),
                )
            ],
        ),
        node("row_feed_button_share", bounds=(390, 2000, 453, 2121), goto=other_share),
        node(cls=CAPTION, text="other_user Second caption", bounds=(32, 2130, 1080, 2200)),
    ]


def following_screen(cards=None, sheet=None):
    kids = [
        ACTION_BAR,
        FOLLOWING_TITLE,
        node(
            "android:id/list",
            bounds=(0, 289, 1080, 2235),
            children=cards if cards is not None else feed_cards(),
        ),
        PROFILE_TAB,
    ]
    if sheet:
        kids.append(sheet)
    return hierarchy(*kids)


def copy_link(clip=None):
    return node(desc="Copy link", bounds=(0, 2240, 1080, 2330), clip=clip, goto="following")


OLDER_CARDS = [
    node("row_feed_profile_header", desc="old_user posted a photo 2 days ago", bounds=(0, 300, 1080, 437)),
    node("row_feed_photo_imageview", desc="Photo by Old User, 5 likes", bounds=(0, 437, 1080, 1500)),
    node("row_feed_button_share", bounds=(390, 1500, 453, 1621)),
    node(cls=CAPTION, text="old_user Old caption", bounds=(32, 1630, 1080, 1700)),
]


def feed_device(top_share="share_top", other_share="share_other", **kw):
    screens = {
        "home": home_screen(),
        "menu": MENU,
        "following": following_screen(feed_cards(1, top_share, other_share)),
        "following_s2": following_screen(feed_cards(2, top_share, other_share)),
        "following_s3": following_screen(feed_cards(3, top_share, other_share)),
        "share_top": following_screen(sheet=copy_link(TOP_URL)),
        "share_other": following_screen(sheet=copy_link(OTHER_URL)),
        "share_noclip": following_screen(sheet=copy_link()),
        "older": following_screen(OLDER_CARDS),
        "story_alice": STORY,
    }
    back = {
        "following": "home",
        "menu": "home",
        "older": "home",
        "story_alice": "launcher",  # backing out of a story can drop out of the app entirely
        "share_top": "following",
        "share_other": "following",
        "share_noclip": "following",
    }
    return FakeDevice(
        screens,
        kw.pop("start", "home"),
        back=back,
        scroll={"following": "older"},
        hswipe={"following": "following_s2", "following_s2": "following_s3"},
        **kw,
    )


def profile_screen(following_goto="following_list"):
    return hierarchy(
        ACTION_BAR,
        node(
            "profile_header_following_stacked_familiar",
            desc="following",
            bounds=(782, 321, 1038, 464),
            goto=following_goto,
        ),
    )


def following_list_screen(usernames):
    return hierarchy(
        ACTION_BAR,
        node("unified_follow_list_view_pager", bounds=(0, 336, 1080, 2214)),
        *[
            node(
                "follow_list_username",
                cls="android.widget.TextView",
                text=u,
                bounds=(247, 1353 + i * 189, 700, 1400 + i * 189),
            )
            for i, u in enumerate(usernames)
        ],
    )


def feed_device_with_following(pages, main_scroll=None, **kw):
    """feed_device() plus a profile -> own-Following-list screen chain, for the followed-accounts
    allowlist. `pages` is a list of username lists, one per Following-list scroll screen; the last
    page has no further scroll entry, simulating "list exhausted." `main_scroll` replaces the main
    feed's own scroll map (default {"following": "older"}) — pass {} to keep a filtering test on
    a single feed screen rather than also scrolling into `older`."""
    d = feed_device(**kw)
    if main_scroll is not None:
        d.scroll = dict(main_scroll)
    d.screens["profile"] = profile_screen()
    d.back["profile"] = "following"
    page_names = ["following_list"] + [f"following_list_s{i}" for i in range(2, len(pages) + 1)]
    for i, (name, usernames) in enumerate(zip(page_names, pages, strict=True)):
        d.screens[name] = following_list_screen(usernames)
        d.back[name] = "profile"
        if i + 1 < len(page_names):
            d.scroll[name] = page_names[i + 1]
    return d


@pytest.fixture(autouse=True)
def fast_offline(tmp_path, monkeypatch):
    monkeypatch.setattr(scraper, "DB_PATH", str(tmp_path / "posts.sqlite"))
    monkeypatch.setattr(scraper, "MEDIA_DIR", tmp_path / "media")
    monkeypatch.setattr(scraper, "DEBUG_DIR", tmp_path / "debug")
    monkeypatch.setattr(scraper, "human_pause", lambda *a, **k: None)
    monkeypatch.setattr(scraper.time, "sleep", lambda s: None)
    monkeypatch.setattr(scraper, "IG_USERNAME", "me")
    monkeypatch.setattr(scraper, "IG_PASSWORD", "hunter2")
    monkeypatch.setattr(scraper, "_last_url", "")
    monkeypatch.setattr(scraper, "CLIPBOARD_TIMEOUT", 0.01)
    return tmp_path


# --- login ------------------------------------------------------------------------------------


def login_screen(button_goto="notnow"):
    return hierarchy(
        node(
            cls="android.widget.TextView", text="Phone number, username or email", bounds=(0, 500, 1080, 560)
        ),
        node(cls="android.widget.EditText", bounds=(0, 600, 1080, 700)),
        node(cls="android.widget.TextView", text="Password", bounds=(0, 700, 1080, 740)),
        node(cls="android.widget.EditText", bounds=(0, 750, 1080, 850)),
        node(cls="android.widget.Button", desc="Log in", bounds=(0, 900, 1080, 1000), goto=button_goto),
        node(cls="android.widget.TextView", text="Forgot password?", bounds=(0, 1100, 1080, 1150)),
    )


def text_screen(text, goto=None):
    return hierarchy(node(cls="android.widget.TextView", text=text, bounds=(0, 1000, 1080, 1100), goto=goto))


def test_login_fills_the_form_and_dismisses_interstitials():
    screens = {"login": login_screen(), "notnow": text_screen("Not now", goto="home"), "home": home_screen()}
    d = FakeDevice(screens, "login")
    assert scraper.ensure_logged_in(d) is True
    assert d.typed == [(0, "me"), (1, "hunter2")]
    assert d.screen == "home"
    assert d.launches == ["com.instagram.mainactivity.LauncherActivity"]  # resolved, not monkey


def test_login_taps_through_the_logged_out_welcome_screen():
    screens = {
        "welcome": text_screen("I already have a profile", goto="login"),
        "login": login_screen(button_goto="home"),
        "home": home_screen(),
    }
    d = FakeDevice(screens, "welcome")
    assert scraper.ensure_logged_in(d) is True
    assert "login" in d.history


def test_login_dismisses_a_stray_ok_alert_and_accepts_a_live_session():
    d = FakeDevice({"alert": text_screen("OK", goto="home"), "home": home_screen()}, "alert")
    assert scraper.ensure_logged_in(d) is True
    assert d.typed == []


@pytest.mark.parametrize(
    ("screens", "start", "match"),
    [
        ({"challenge": text_screen("Help us confirm it's you")}, "challenge", "wants a human"),
        ({"login": login_screen(button_goto="challenge"), "challenge": text_screen("Enter the code")}, "login", "wants a human"),
        ({"login": login_screen(button_goto="")}, "login", "wrong password"),
        ({"markers": text_screen("Forgot password?")}, "markers", "form not recognised"),
    ],
)  # fmt: skip
def test_login_failures_raise_with_a_debug_dump(screens, start, match, fast_offline):
    with pytest.raises(RuntimeError, match=match):
        scraper.ensure_logged_in(FakeDevice(screens, start))
    assert (fast_offline / "debug" / "login_hierarchy.xml").exists()


def test_login_without_credentials_raises(monkeypatch):
    monkeypatch.setattr(scraper, "IG_PASSWORD", "")
    with pytest.raises(RuntimeError, match="not set"):
        scraper.ensure_logged_in(FakeDevice({"login": login_screen()}, "login"))


def test_login_raises_when_instagram_is_not_installed():
    with pytest.raises(RuntimeError, match="not installed"):
        scraper.ensure_logged_in(FakeDevice({}, "launcher", installed=()))


def test_app_that_never_foregrounds_is_a_transient_device_failure():
    d = FakeDevice({}, "launcher", launch_blocked=True)
    with pytest.raises(scraper.DeviceNotReady) as exc:
        scraper.ensure_logged_in(d)
    assert scraper.is_transient(exc.value)
    assert len(d.launches) == 4  # the first launch plus three retries


# --- feed navigation --------------------------------------------------------------------------


def test_open_following_feed_goes_through_the_switcher():
    d = feed_device()
    assert scraper.open_following_feed(d) is True
    assert d.history[-2:] == ["menu", "following"]


def test_open_following_feed_reenters_a_following_screen_left_over_from_last_run():
    d = feed_device(start="following")
    assert scraper.open_following_feed(d) is True
    assert d.history == ["following", "home", "menu", "following"]


def test_open_following_feed_backs_out_of_an_unrelated_screen():
    d = feed_device(start="profile")
    d.screens["profile"] = hierarchy(node(text="Edit profile"))
    d.back["profile"] = "home"
    assert scraper.open_following_feed(d) is True
    assert d.presses[0] == "back"


def test_open_following_feed_gives_up_when_the_switcher_never_opens(fast_offline):
    d = FakeDevice({"home": home_screen(switcher_goto="")}, "home")
    assert scraper.open_following_feed(d) is False
    assert (fast_offline / "debug" / "feed_switch_hierarchy.xml").exists()


def test_close_sheets_relaunches_if_back_leaves_the_app():
    d = feed_device(start="share_top")
    d.back["share_top"] = "launcher"
    assert scraper.close_sheets(d) is True
    assert d.screen == "home"


def test_back_to_feed_relaunches_from_outside_the_app():
    d = feed_device(start="launcher", launch_screen="following")
    assert scraper._back_to_feed(d) is True


# --- permalinks -------------------------------------------------------------------------------


def top_card_id(d):
    return scraper.post_id(scraper.parse_hierarchy(d.dump_hierarchy())[0])


def test_fetch_permalink_copies_and_canonicalises_the_link():
    d = feed_device(start="following")
    assert scraper.fetch_permalink(d, top_card_id(d)) == ("https://www.instagram.com/reel/TOP123/", None)
    assert d.screen == "following"


def test_fetch_permalink_reports_a_sheet_that_never_opens(fast_offline):
    d = feed_device(top_share="", start="following")
    assert scraper.fetch_permalink(d, top_card_id(d)) == (None, "sheet")
    assert (fast_offline / "debug" / "share_sheet_hierarchy.xml").exists()


def test_fetch_permalink_reports_a_clipboard_that_never_updates():
    d = feed_device(top_share="share_noclip", start="following")
    assert scraper.fetch_permalink(d, top_card_id(d)) == (None, "clipboard")


def test_fetch_permalink_ignores_the_previous_posts_link_left_in_the_clipboard(monkeypatch):
    monkeypatch.setattr(scraper, "_last_url", TOP_URL)
    d = feed_device(start="following")
    assert scraper.fetch_permalink(d, top_card_id(d)) == (None, "clipboard")


def test_fetch_permalink_when_the_card_is_gone():
    assert scraper.fetch_permalink(feed_device(start="following"), "no-such-card") == (None, "sheet")


def test_fetch_permalink_refuses_to_act_inside_a_stuck_sheet():
    d = feed_device(start="share_top")
    d.back["share_top"] = "share_top"
    assert scraper.fetch_permalink(d, "anything") == (None, "sheet")
    assert d.taps == []  # never tapped anything inside the sheet


def test_sweep_cached_apps_force_stops_every_package_and_skips_a_failure(monkeypatch):
    d = feed_device(start="following")
    real_shell = d.shell
    calls = []

    def flaky_shell(cmd):
        calls.append(cmd)
        if "com.android.keychain" in cmd:
            raise RuntimeError("device offline")
        return real_shell(cmd)

    monkeypatch.setattr(d, "shell", flaky_shell)

    scraper._sweep_cached_apps(d)  # must not raise despite the keychain failure

    swept = {c[2] for c in calls}
    assert swept == set(scraper.CACHED_APP_SWEEP)  # every package attempted, including after the failure


def test_stop_instagram_force_stops_the_app_and_is_best_effort():
    d = feed_device(start="following")

    scraper._stop_instagram(d)

    assert f"am force-stop {scraper.IG_PKG}" in d.shell_calls

    def raising_shell(cmd):
        raise RuntimeError("device offline")

    d.shell = raising_shell
    scraper._stop_instagram(d)  # must not raise


def _caption_card(text, goto=None):
    return [
        node(
            "row_feed_profile_header",
            desc="someone_nice posted a photo 3 days ago",
            bounds=(0, 150, 1080, 289),
        ),
        node("row_feed_photo_imageview", desc="Photo by Someone Nice, 5 likes", bounds=(0, 289, 1080, 900)),
        node("row_feed_button_share", bounds=(390, 900, 453, 1021)),
        node(cls=CAPTION, text=text, bounds=(32, 1030, 1080, 1100), goto=goto),
    ]


def test_expand_caption_taps_more_and_returns_the_full_text():
    d = FakeDevice(
        {
            "following": following_screen(_caption_card("someone_nice Short start… more", goto="expanded")),
            "expanded": following_screen(
                _caption_card("someone_nice Short start continues on with the full text")
            ),
        },
        "following",
    )
    p = scraper.parse_hierarchy(d.dump_hierarchy())[0]
    assert p["caption_truncated"] is True

    assert scraper._expand_caption(d, p) == "Short start continues on with the full text"


def test_expand_caption_falls_back_to_the_truncated_text_when_the_tap_does_nothing():
    d = FakeDevice(
        {
            "following": following_screen(_caption_card("someone_nice Short start… more"))
        },  # no goto: tap is inert
        "following",
    )
    p = scraper.parse_hierarchy(d.dump_hierarchy())[0]

    assert scraper._expand_caption(d, p) == "Short start…"
    assert d.screen == "following"  # never knocked off the feed


def test_expand_caption_is_a_noop_for_a_caption_that_was_never_truncated():
    d = FakeDevice(
        {"following": following_screen(_caption_card("someone_nice Whole caption, no more span"))},
        "following",
    )
    p = scraper.parse_hierarchy(d.dump_hierarchy())[0]
    assert p["caption_truncated"] is False

    assert scraper._expand_caption(d, p) == "Whole caption, no more span"
    assert d.taps == []  # nothing to tap


# --- the scrape loop --------------------------------------------------------------------------


def _seed_post(con, pid, username, caption, days_ago, h=None, url=None):
    ts = (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()
    con.execute(
        "INSERT INTO posts (id, username, kind, posted_date, caption, media_file, scraped_at, hash, url,"
        " posted_at, updated_at) VALUES (?,?,?,?,?,NULL,?,?,?,?,?)",
        (pid, username, "photo", "x", caption, ts, h or pid, url, ts, ts),
    )
    con.commit()


def test_scrape_once_end_to_end(fast_offline, monkeypatch):
    monkeypatch.setattr(scraper, "MAX_CAROUSEL_SLIDES", 3)
    monkeypatch.setattr(scraper, "STOP_AFTER_SEEN", 1)
    media = fast_offline / "media"
    con = scraper.db_init()
    d = feed_device()
    old_card = scraper.parse_hierarchy(d.screens["older"])[0]
    _seed_post(con, "OLD1", "old_user", "Old caption", 2, h=scraper.post_id(old_card))

    stats = scraper.scrape_once(d, con)

    assert stats == {
        "new": 2,
        "new_stories": 1,
        "link_sheet_failures": 0,
        "link_clipboard_failures": 0,
        "warning": None,
        "filtered_posts": 0,
    }
    posts = {r["id"]: r for r in con.execute("SELECT * FROM posts")}
    assert set(posts) == {"TOP123", "OTHER1", "OLD1"}
    assert posts["TOP123"]["username"] == "someone_nice" and posts["TOP123"]["kind"] == "video"
    assert posts["TOP123"]["url"] == "https://www.instagram.com/reel/TOP123/"
    assert posts["OTHER1"]["place"] == "San Diego, California"
    assert posts["OTHER1"]["caption"] == "Second caption"
    slides = con.execute("SELECT idx, file FROM media WHERE post_id='OTHER1' ORDER BY idx").fetchall()
    assert [tuple(s) for s in slides] == [(1, "OTHER1_1.jpg"), (2, "OTHER1_2.jpg")]
    for fn in ("TOP123.jpg", "OTHER1.jpg", "OTHER1_1.jpg", "OTHER1_2.jpg"):
        assert (media / fn).exists()
    assert (media / "avatars" / "other_user.jpg").exists()
    assert (media / "avatars" / "old_user.jpg").exists()
    stories = con.execute("SELECT username, media_file FROM stories").fetchall()
    assert [s["username"] for s in stories] == ["alice"]  # bob's viewer never opened, carol was seen
    assert (media / stories[0]["media_file"]).exists()
    assert d.presses[-1] == "home"
    for pkg in scraper.CACHED_APP_SWEEP:
        assert f"am force-stop {pkg}" in d.shell_calls
    assert f"am force-stop {scraper.IG_PKG}" in d.shell_calls


def test_scrape_once_without_permalinks_falls_back_to_hash_ids_and_merges_a_placeholder(monkeypatch):
    monkeypatch.setattr(scraper, "MAX_STORIES_PER_RUN", 0)
    monkeypatch.setattr(scraper, "MAX_CAROUSEL_SLIDES", 1)
    monkeypatch.setattr(scraper, "MAX_SCROLLS", 2)
    monkeypatch.setattr(scraper, "PERMALINK_RETRIES", 1)
    monkeypatch.setattr(scraper, "SHARE_TAP_TRIES", 1)
    con = scraper.db_init()
    # Stored earlier, before the caption rendered: same author and day, placeholder caption.
    _seed_post(con, "placeholder", "someone_nice", "Reel by Someone Nice", 3)
    d = feed_device(top_share="", other_share="")
    d.scroll = {}  # scrolling shows nothing new: only the two cards above are in play

    stats = scraper.scrape_once(d, con)

    assert stats["new"] == 1  # the Reel merged into the placeholder instead of being stored twice
    assert stats["link_sheet_failures"] == 4  # two attempts per card
    merged = con.execute("SELECT * FROM posts WHERE id='placeholder'").fetchone()
    assert merged["caption"] == "Top card caption…"
    assert merged["media_file"]
    other = con.execute("SELECT * FROM posts WHERE username='other_user'").fetchone()
    assert other["url"] is None and other["id"] == other["hash"]


def test_scrape_once_drops_a_permalink_that_belongs_to_another_account(monkeypatch):
    monkeypatch.setattr(scraper, "MAX_STORIES_PER_RUN", 0)
    monkeypatch.setattr(scraper, "MAX_CAROUSEL_SLIDES", 1)
    monkeypatch.setattr(scraper, "MAX_SCROLLS", 1)
    con = scraper.db_init()
    _seed_post(con, "TOP123", "someone_else", "Unrelated", 10, h="unrelated")

    scraper.scrape_once(feed_device(), con)

    row = con.execute("SELECT * FROM posts WHERE username='someone_nice'").fetchone()
    assert row["url"] is None and row["id"] != "TOP123"  # stored under its hash, not the stale link


def test_scrape_once_treats_an_edited_caption_as_the_same_post(monkeypatch):
    monkeypatch.setattr(scraper, "MAX_STORIES_PER_RUN", 0)
    monkeypatch.setattr(scraper, "MAX_CAROUSEL_SLIDES", 1)
    monkeypatch.setattr(scraper, "MAX_SCROLLS", 1)
    con = scraper.db_init()
    _seed_post(con, "TOP123", "someone_nice", "Caption before the edit", 3, h="old-hash")
    d = feed_device()

    stats = scraper.scrape_once(d, con)

    row = con.execute("SELECT hash FROM posts WHERE id='TOP123'").fetchone()
    assert row["hash"] == top_card_id(feed_device(start="following"))  # re-keyed to the new caption
    assert stats["new"] == 1  # only the other card is new
    assert con.execute("SELECT COUNT(*) FROM posts WHERE username='someone_nice'").fetchone()[0] == 1


# --- feed mode (chrono vs home) ------------------------------------------------------------------


def home_feed_screen(cards=None):
    """A populated Home feed, with the bottom tab bar (feed_tab) present -- unlike
    following_screen(), which mirrors real Instagram's Following screen hiding it."""
    return hierarchy(
        ACTION_BAR,
        node(desc="Instagram Home Feed", bounds=(0, 150, 400, 280), goto="menu"),
        node(
            "android:id/list",
            bounds=(0, 289, 1080, 2235),
            children=cards if cards is not None else feed_cards(),
        ),
        node("feed_tab", bounds=(0, 2200, 216, 2340)),
        PROFILE_TAB,
    )


def test_open_home_feed_navigates_via_the_home_tab(fast_offline):
    # feed_device()'s own back map already sends "following" -> "home"; open_home_feed() has no
    # switcher to tap, so this is the same recovery path open_following_feed() itself relies on.
    d = feed_device(start="following")
    assert scraper.open_home_feed(d) is True
    assert d.screen == "home"


def test_on_target_feed_matches_feed_mode(fast_offline, monkeypatch):
    home = FakeDevice({"home": home_feed_screen()}, "home")
    following = FakeDevice({"following": following_screen()}, "following")
    monkeypatch.setattr(scraper, "FEED_MODE", "home")
    assert scraper._on_target_feed(home) is True
    assert scraper._on_target_feed(following) is False
    monkeypatch.setattr(scraper, "FEED_MODE", "chrono")
    assert scraper._on_target_feed(home) is False
    assert scraper._on_target_feed(following) is True


def test_open_target_feed_dispatches_by_feed_mode(fast_offline, monkeypatch):
    d = feed_device(start="home")
    monkeypatch.setattr(scraper, "FEED_MODE", "home")
    assert scraper.open_target_feed(d) is True
    assert d.screen == "home"
    d = feed_device(start="home")
    monkeypatch.setattr(scraper, "FEED_MODE", "chrono")
    assert scraper.open_target_feed(d) is True
    assert d.screen == "following"


def test_unknown_feed_mode_falls_back_to_chrono():
    # A subprocess, not importlib.reload(scraper) -- scraper.py is a shared, stateful module across
    # the whole test session (its exception classes are identity-checked elsewhere, per test_push.py's
    # own note), and reloading it in place would rebind those classes out from under other test files.
    result = subprocess.run(
        [sys.executable, "-c", "import scraper; print(scraper.FEED_MODE)"],
        cwd=Path(__file__).parent.parent,
        env={**os.environ, "FEED_MODE": "algorithmic"},
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip().splitlines()[-1] == "chrono"  # the fallback warning also prints


def test_scrape_once_stays_on_home_feed_when_feed_mode_is_home(fast_offline, monkeypatch):
    monkeypatch.setattr(scraper, "FEED_MODE", "home")
    monkeypatch.setattr(scraper, "MAX_STORIES_PER_RUN", 0)
    monkeypatch.setattr(scraper, "MAX_SCROLLS", 1)
    monkeypatch.setattr(scraper, "STOP_AFTER_SEEN", 1)
    con = scraper.db_init()
    # Seed every card already-known so nothing new needs the share-sheet round trip -- this test
    # is about which feed scrape_once() navigates to, not about re-testing that flow.
    for i, card in enumerate(scraper.parse_hierarchy(home_feed_screen())):
        _seed_post(con, f"SEEN{i}", card["username"], "already stored", 1, h=scraper.post_id(card))
    d = FakeDevice(
        {"home": home_feed_screen(), "following": following_screen(), "menu": MENU},
        "home",
        back={"following": "home", "menu": "home"},
    )

    scraper.scrape_once(d, con)

    assert "following" not in d.history  # never navigated to the chronological feed
    assert "menu" not in d.history  # never opened the switcher either


def test_open_own_following_list_navigates_from_the_feed(fast_offline):
    d = feed_device_with_following([["alice", "bob"]], start="following")
    assert scraper.open_own_following_list(d) is True
    assert d.screen == "following_list"
    assert d.history[-3:] == ["following", "profile", "following_list"]


def test_open_own_following_list_leaves_and_reenters_when_already_on_the_list_screen(fast_offline):
    # A real live run (2026-09-11) found this exact case: a second refresh in the same app session
    # started mid-scroll instead of at the top, collecting 9 of 30 followed accounts instead of a
    # fresh scroll's 27+ — accepting "already there" as done is the bug this guards against.
    d = feed_device_with_following([["alice", "bob"]], start="following_list")
    assert scraper.open_own_following_list(d) is True
    assert d.screen == "following_list"
    assert d.history.count("following_list") == 2  # left, then genuinely navigated back in


def test_scrape_following_list_scrolls_until_no_new_username_appears(fast_offline, monkeypatch):
    monkeypatch.setattr(scraper, "FOLLOWING_LIST_EMPTY_LIMIT", 1)
    d = feed_device_with_following([["alice", "bob"], ["carol"]], main_scroll={}, start="following_list")
    assert scraper.scrape_following_list(d) == ["alice", "bob", "carol"]


def test_scrape_following_list_returns_none_when_nothing_is_ever_parsed(fast_offline, monkeypatch):
    monkeypatch.setattr(scraper, "FOLLOWING_LIST_EMPTY_LIMIT", 1)
    d = feed_device_with_following([[]], main_scroll={}, start="following_list")
    assert scraper.scrape_following_list(d) is None


def test_refresh_following_list_replaces_the_stored_list(fast_offline, monkeypatch):
    monkeypatch.setattr(scraper, "FOLLOWING_LIST_EMPTY_LIMIT", 1)
    con = scraper.db_init()
    con.execute("INSERT INTO following (username, updated_at) VALUES ('stale_unfollowed', '2020-01-01')")
    con.commit()
    d = feed_device_with_following([["alice", "bob"]], main_scroll={}, start="following")

    n = scraper.refresh_following_list(d, con)

    assert n == 2
    assert {r[0] for r in con.execute("SELECT username FROM following")} == {"alice", "bob"}


def test_refresh_following_list_keeps_the_existing_list_on_a_failed_scrape(fast_offline, monkeypatch):
    monkeypatch.setattr(scraper, "FOLLOWING_LIST_EMPTY_LIMIT", 1)
    con = scraper.db_init()
    con.execute("INSERT INTO following (username, updated_at) VALUES ('good_data', '2020-01-01')")
    con.commit()
    d = feed_device_with_following([[]], main_scroll={}, start="following")  # empty list = parse failure

    n = scraper.refresh_following_list(d, con)

    assert n is None
    assert {r[0] for r in con.execute("SELECT username FROM following")} == {"good_data"}


def test_scrape_once_filters_posts_from_accounts_not_on_the_refreshed_following_list(
    fast_offline, monkeypatch
):
    monkeypatch.setattr(scraper, "FOLLOWING_REFRESH_DAYS", 7)
    monkeypatch.setattr(scraper, "FOLLOWING_LIST_EMPTY_LIMIT", 1)
    monkeypatch.setattr(scraper, "MAX_STORIES_PER_RUN", 0)
    monkeypatch.setattr(scraper, "MAX_CAROUSEL_SLIDES", 1)
    monkeypatch.setattr(scraper, "MAX_SCROLLS", 2)
    con = scraper.db_init()  # following table starts empty -> due for a refresh this run
    # Only someone_nice is on the (about-to-be-scraped) Following list; other_user is not.
    d = feed_device_with_following([["someone_nice"]], main_scroll={})

    stats = scraper.scrape_once(d, con)

    assert {r[0] for r in con.execute("SELECT username FROM following")} == {"someone_nice"}
    assert stats["filtered_posts"] >= 1
    posts = con.execute("SELECT username FROM posts").fetchall()
    assert any(r[0] == "someone_nice" for r in posts)
    assert all(r[0] != "other_user" for r in posts)
    # A filtered post's account is never upserted -- no avatar work, no accounts-table footprint.
    assert con.execute("SELECT 1 FROM accounts WHERE username='other_user'").fetchone() is None


def test_scrape_once_does_not_filter_before_the_first_successful_refresh(fast_offline, monkeypatch):
    # Enabled but never yet refreshed, and this run's own refresh attempt also finds nothing
    # (empty Following-list screen) -- an empty allowlist must mean "not initialized," not "filter
    # everything," or turning the feature on would silently drop every post on its first run.
    monkeypatch.setattr(scraper, "FOLLOWING_REFRESH_DAYS", 7)
    monkeypatch.setattr(scraper, "FOLLOWING_LIST_EMPTY_LIMIT", 1)
    monkeypatch.setattr(scraper, "MAX_STORIES_PER_RUN", 0)
    monkeypatch.setattr(scraper, "MAX_CAROUSEL_SLIDES", 1)
    monkeypatch.setattr(scraper, "MAX_SCROLLS", 2)
    monkeypatch.setattr(scraper, "STOP_AFTER_SEEN", 1)
    con = scraper.db_init()
    d = feed_device_with_following([[]], main_scroll={})  # the refresh itself finds nothing

    stats = scraper.scrape_once(d, con)

    assert stats["filtered_posts"] == 0
    posts = {r[0] for r in con.execute("SELECT username FROM posts")}
    assert posts == {"someone_nice", "other_user"}  # nothing dropped


# --- device connection and the main loop ------------------------------------------------------


class StopLoop(Exception):
    pass


def test_connect_device(monkeypatch):
    dev = feed_device()
    monkeypatch.setattr(scraper.adbutils.adb, "connect", lambda addr, timeout=None: None)
    monkeypatch.setattr(scraper.u2, "connect", lambda addr: dev)
    assert scraper.connect_device() is dev


def _stop_after_first_sleep(monkeypatch):
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        raise StopLoop

    monkeypatch.setattr(scraper.time, "sleep", sleep)
    monkeypatch.setattr(scraper, "TIME_DISTRIBUTION", "uniform")
    return sleeps


def test_main_records_a_transient_failure_and_retries_early(fast_offline, monkeypatch):
    sleeps = _stop_after_first_sleep(monkeypatch)
    monkeypatch.setattr(scraper, "RETRY_DELAYS_MINUTES", [2.0])

    def offline():
        raise adbutils.AdbError("device 127.0.0.1:5555 not online")

    monkeypatch.setattr(scraper, "connect_device", offline)

    with pytest.raises(StopLoop):
        scraper.main()

    run = sqlite3.connect(fast_offline / "posts.sqlite").execute("SELECT error FROM runs").fetchone()
    assert run[0].startswith("AdbError")
    assert 2 * 60 <= sleeps[0] <= 3 * 60


def test_main_records_a_successful_run_with_device_versions(fast_offline, monkeypatch):
    sleeps = _stop_after_first_sleep(monkeypatch)
    monkeypatch.setattr(scraper, "connect_device", feed_device)
    stats = {
        "new": 2,
        "new_stories": 1,
        "link_sheet_failures": 1,
        "link_clipboard_failures": 0,
        "warning": "w",
    }
    monkeypatch.setattr(scraper, "scrape_once", lambda d, con: stats)

    with pytest.raises(StopLoop):
        scraper.main()

    con = sqlite3.connect(fast_offline / "posts.sqlite")
    con.row_factory = sqlite3.Row
    run = con.execute("SELECT * FROM runs").fetchone()
    assert (run["new_posts"], run["new_stories"], run["warning"], run["error"]) == (2, 1, "w", None)
    assert (run["android_release"], run["ig_version"]) == ("13", "445.0.0.45.83")
    assert scraper.POLL_MIN_H * 3600 <= sleeps[0] <= scraper.POLL_MAX_H * 3600
