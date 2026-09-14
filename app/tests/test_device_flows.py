"""End-to-end-ish tests of the device-driving code (login, feed navigation, share sheet, carousels,
stories, the scrape loop, main()) against tests.fakedevice.FakeDevice. Screens are synthetic."""

import os
import sqlite3
import subprocess
import sys
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import adbutils
import igprofiles
import pytest
import scraper
from igprofiles.v445.selectors import SELECTORS as SELECTORS_445

from tests.fakedevice import FakeDevice, hierarchy, node

SAVE_FAILURE_LOGCAT = scraper._save_failure_logcat  # captured before conftest stubs it out
CAPTION = SELECTORS_445["caption_class"]  # read at import, before conftest pins the profile
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
            desc="other_user posted a carousel in Anytown, Somewhere 21 hours ago",
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
    monkeypatch.setattr(scraper, "APK_CACHE_DIR", tmp_path / "apk")
    monkeypatch.setattr(scraper, "IG_APK_VERSION", "latest")  # the top-level cache layout
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


def test_login_raises_when_instagram_is_not_installed_and_auto_install_is_off(monkeypatch):
    monkeypatch.setattr(scraper, "IG_AUTO_INSTALL", False)
    with pytest.raises(RuntimeError, match="not installed"):
        scraper.ensure_logged_in(FakeDevice({}, "launcher", installed=()))


def _apk_run(monkeypatch, d, calls, *, fail_on=None):
    """Patch scraper.subprocess.run to fake apkeep + adb install without touching the network or a
    real device, recording every invocation into `calls`. `fail_on` (argv[0], "apkeep" or "adb")
    makes that step raise CalledProcessError. A successful "adb install"/"install-multiple" flips
    `d`'s installed set, same as a real adb install would."""

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if fail_on and cmd[0] == fail_on:
            raise subprocess.CalledProcessError(1, cmd, output="", stderr="boom")
        if cmd[0] == "apkeep":
            out_dir = Path(cmd[cmd.index("-d") + 2])
            out_dir.mkdir(parents=True, exist_ok=True)
            xapk = out_dir / f"{scraper.IG_PKG}@1.0.0.xapk"
            with zipfile.ZipFile(xapk, "w") as zf:
                zf.writestr(f"{scraper.IG_PKG}.apk", b"base")
                zf.writestr("config.arm64_v8a.apk", b"split")
                zf.writestr("manifest.json", b"{}")
        elif cmd[0] == "adb":
            d.install()
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(scraper.subprocess, "run", fake_run)


def test_ensure_logged_in_installs_instagram_when_missing(monkeypatch):
    d = FakeDevice({"home": home_screen()}, "launcher", installed=())
    calls = []
    _apk_run(monkeypatch, d, calls)
    assert scraper.ensure_logged_in(d) is True
    apkeep_call = next(c for c in calls if c[0] == "apkeep")
    assert apkeep_call[:3] == ["apkeep", "-a", scraper.IG_PKG]
    install_call = next(c for c in calls if c[0] == "adb")
    assert install_call[3] == "install-multiple"
    assert install_call[4].endswith(f"{scraper.IG_PKG}.apk")
    assert install_call[5].endswith("config.arm64_v8a.apk")


def test_installing_instagram_reactivates_the_profile(monkeypatch):
    d = FakeDevice({"home": home_screen()}, "launcher", installed=())
    _apk_run(monkeypatch, d, [])
    monkeypatch.setattr(scraper, "PROFILE_WARNING", "stale warning from before the install")
    assert scraper.ensure_logged_in(d) is True
    assert scraper.PROFILE.name == "v445" and scraper.PROFILE_WARNING is None  # installed 445.0.0.45.83


def test_auto_install_fetches_the_profiles_own_apk_version(monkeypatch):
    monkeypatch.setattr(scraper, "IG_APK_VERSION", "")
    monkeypatch.setattr(scraper, "PROFILE", igprofiles.load("v446"))
    d = FakeDevice({"home": home_screen()}, "launcher", installed=(), ig_version="446.0.0.49.77")
    calls = []
    _apk_run(monkeypatch, d, calls)
    monkeypatch.setattr(scraper, "IG_PROFILE", "v446")
    assert scraper.ensure_logged_in(d) is True
    assert next(c for c in calls if c[0] == "apkeep")[2] == f"{scraper.IG_PKG}@446.0.0.49.77"


def test_a_pinned_apk_version_gets_its_own_cache_folder(monkeypatch):
    # An unpinned bundle already cached at the top level must not be installed for a pinned version.
    xapk_dir = scraper.APK_CACHE_DIR / "xapk"
    xapk_dir.mkdir(parents=True)
    (xapk_dir / f"{scraper.IG_PKG}.apk").write_bytes(b"latest")
    monkeypatch.setattr(scraper, "IG_APK_VERSION", "445.0.0.45.83")
    d = FakeDevice({"home": home_screen()}, "launcher", installed=())
    calls = []
    _apk_run(monkeypatch, d, calls)
    assert scraper.ensure_logged_in(d) is True
    apkeep_call = next(c for c in calls if c[0] == "apkeep")
    assert apkeep_call[2] == f"{scraper.IG_PKG}@445.0.0.45.83"
    assert apkeep_call[-1] == str(scraper.APK_CACHE_DIR / "445.0.0.45.83")
    install_call = next(c for c in calls if c[0] == "adb")
    assert all("/445.0.0.45.83/" in arg for arg in install_call[4:])


def test_install_version_replaces_a_newer_install_in_place(monkeypatch):
    d = FakeDevice({"home": home_screen()}, "launcher", ig_version="446.0.0.49.77")
    d.install = lambda: setattr(d, "ig_version", "445.0.0.45.83")  # what the downgrade installs
    calls = []
    _apk_run(monkeypatch, d, calls)
    assert scraper.install_instagram_version(d, "445.0.0.45.83") == "445.0.0.45.83"
    assert next(c for c in calls if c[0] == "apkeep")[2] == f"{scraper.IG_PKG}@445.0.0.45.83"
    install_call = next(c for c in calls if c[0] == "adb")
    assert install_call[3:6] == ["install-multiple", "-r", "-d"]


def test_install_version_defaults_to_the_pinned_version_and_skips_when_already_installed(monkeypatch):
    monkeypatch.setattr(scraper, "IG_APK_VERSION", "445.0.0.45.83")
    d = FakeDevice({"home": home_screen()}, "launcher")  # already reports 445.0.0.45.83
    calls = []
    _apk_run(monkeypatch, d, calls)
    assert scraper.install_instagram_version(d) == "445.0.0.45.83"
    assert calls == []


def test_install_version_raises_when_the_device_reports_another_version(monkeypatch):
    d = FakeDevice({"home": home_screen()}, "launcher", ig_version="446.0.0.49.77")
    _apk_run(monkeypatch, d, [])  # the fake install leaves the version untouched
    with pytest.raises(scraper.DeviceNotReady, match="device reports 446.0.0.49.77"):
        scraper.install_instagram_version(d, "445.0.0.45.83")


def test_ensure_logged_in_reuses_a_cached_apk(monkeypatch):
    xapk_dir = scraper.APK_CACHE_DIR / "xapk"
    xapk_dir.mkdir(parents=True)
    (xapk_dir / f"{scraper.IG_PKG}.apk").write_bytes(b"base")
    d = FakeDevice({"home": home_screen()}, "launcher", installed=())
    calls = []
    _apk_run(monkeypatch, d, calls)
    assert scraper.ensure_logged_in(d) is True
    assert not any(c[0] == "apkeep" for c in calls)
    install_call = next(c for c in calls if c[0] == "adb")
    assert install_call[3] == "install"  # single apk: no -multiple


def test_ensure_logged_in_raises_transiently_when_apkeep_fails(monkeypatch):
    d = FakeDevice({}, "launcher", installed=())
    _apk_run(monkeypatch, d, [], fail_on="apkeep")
    with pytest.raises(scraper.DeviceNotReady):
        scraper.ensure_logged_in(d)


def test_ensure_logged_in_raises_transiently_when_install_fails(monkeypatch):
    d = FakeDevice({}, "launcher", installed=())
    _apk_run(monkeypatch, d, [], fail_on="adb")
    with pytest.raises(scraper.DeviceNotReady):
        scraper.ensure_logged_in(d)


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
        "mem_peak_mb": None,  # the fake device has no cgroup files: the memory guard is off
        "oom_kills": None,
    }
    posts = {r["id"]: r for r in con.execute("SELECT * FROM posts")}
    assert set(posts) == {"TOP123", "OTHER1", "OLD1"}
    assert posts["TOP123"]["username"] == "someone_nice" and posts["TOP123"]["kind"] == "video"
    assert posts["TOP123"]["url"] == "https://www.instagram.com/reel/TOP123/"
    assert posts["OTHER1"]["place"] == "Anytown, Somewhere"
    assert posts["OTHER1"]["caption"] == "Second caption"
    assert posts["TOP123"]["ig_version"] == posts["OTHER1"]["ig_version"] == "445.0.0.45.83"
    assert posts["OLD1"]["ig_version"] is None  # seeded before this run; never back-filled
    slides = con.execute("SELECT idx, file FROM media WHERE post_id='OTHER1' ORDER BY idx").fetchall()
    assert [tuple(s) for s in slides] == [(1, "OTHER1_1.webp"), (2, "OTHER1_2.webp")]
    for fn in ("TOP123.webp", "OTHER1.webp", "OTHER1_1.webp", "OTHER1_2.webp"):
        assert (media / fn).exists()
        assert (media / fn).read_bytes()[8:12] == b"WEBP"  # the default MEDIA_FORMAT
    assert (media / "avatars" / "other_user.webp").exists()
    assert (media / "avatars" / "old_user.webp").exists()
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
    assert merged["ig_version"] == "445.0.0.45.83"  # the placeholder had none; the merge fills it in
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
    assert scraper.PROFILE.name == "v445" and scraper.PROFILE_WARNING is None  # device reports 445.0.0.45.83


def test_connect_device_warns_when_the_installed_version_differs_from_the_profile(monkeypatch):
    dev = feed_device()
    dev.ig_version = "446.0.0.49.77"
    monkeypatch.setattr(scraper.adbutils.adb, "connect", lambda addr, timeout=None: None)
    monkeypatch.setattr(scraper.u2, "connect", lambda addr: dev)
    scraper.connect_device()
    assert scraper.PROFILE.name == "v445"  # the configured profile wins; nothing switches on its own
    assert scraper.PROFILE_WARNING == (
        "Instagram 446.0.0.49.77 is installed but profile v445 targets 445.x;"
        " run `scraper.py install` to get 445.0.0.45.83, or set IG_PROFILE=v446"
    )


def test_scrape_once_reports_the_profile_warning(monkeypatch):
    monkeypatch.setattr(scraper, "MAX_STORIES_PER_RUN", 0)
    monkeypatch.setattr(scraper, "MAX_SCROLLS", 1)
    monkeypatch.setattr(scraper, "PROFILE_WARNING", "Instagram 446.0.0.49.77 is installed but profile v445")
    stats = scraper.scrape_once(feed_device(), scraper.db_init())
    assert "Instagram 446.0.0.49.77 is installed but profile v445" in stats["warning"]


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
    assert run["selector_profile"] == "v445"
    assert scraper.POLL_MIN_H * 3600 <= sleeps[0] <= scraper.POLL_MAX_H * 3600


# --- memory guard ---------------------------------------------------------------------------------

MIB = 1024 * 1024


def cgroup_output(current_mib, max_mib=3072, oom_kill=0, inactive_file_mib=0):
    """memory.current, memory.max, memory.events, then (part of) memory.stat, as `cat` prints them."""
    limit = "max" if max_mib is None else str(max_mib * MIB)
    usage = (current_mib + inactive_file_mib) * MIB
    return (
        f"{usage}\n{limit}\nlow 0\nhigh 0\nmax 12\noom 3\noom_kill {oom_kill}\noom_group_kill 0\n"
        f"anon {current_mib * MIB}\nfile {inactive_file_mib * MIB}\ninactive_file {inactive_file_mib * MIB}\n"
    )


def with_cgroup(d, readings):
    """Make FakeDevice `d` answer the memory guard's cgroup read with successive `readings`
    (cgroup_output() strings); the last one repeats."""
    shell = d.shell
    readings = list(readings)

    def fake_shell(cmd):
        joined = " ".join(cmd) if isinstance(cmd, list) else cmd
        if joined.startswith("cat /sys/fs/cgroup/memory.current"):
            d.shell_calls.append(joined)
            return type("Out", (), {"output": readings.pop(0) if len(readings) > 1 else readings[0]})()
        return shell(cmd)

    d.shell = fake_shell
    return d


def test_redroid_memory_parses_the_cgroup_files():
    d = with_cgroup(feed_device(), [cgroup_output(1843, 3072, oom_kill=7)])
    assert scraper._redroid_memory(d) == {"current": 1843 * MIB, "max": 3072 * MIB, "oom_kill": 7}
    unlimited = with_cgroup(feed_device(), [cgroup_output(500, None)])
    assert scraper._redroid_memory(unlimited)["max"] is None
    assert scraper._redroid_memory(feed_device()) is None  # no cgroup files: guard off


def test_redroid_memory_excludes_reclaimable_file_cache():
    # The live reading that stopped a run too early: 2756MiB counted, but ~600MiB was file cache.
    d = with_cgroup(feed_device(), [cgroup_output(2150, 3072, inactive_file_mib=606)])
    assert scraper._redroid_memory(d)["current"] == 2150 * MIB
    assert scraper.MemoryGuard(d).exceeded() is None  # 70% of the limit, not 90%


def test_scrape_once_starts_and_ends_with_instagram_stopped(monkeypatch):
    monkeypatch.setattr(scraper, "MAX_STORIES_PER_RUN", 0)
    monkeypatch.setattr(scraper, "MAX_SCROLLS", 1)
    d = feed_device()
    scraper.scrape_once(d, scraper.db_init())
    stops = [i for i, c in enumerate(d.shell_calls) if c == f"am force-stop {scraper.IG_PKG}"]
    assert len(stops) == 2
    assert stops[0] == 0 or all(
        c.startswith("am force-stop") for c in d.shell_calls[: stops[0]]
    )  # first thing
    assert stops[1] > d.shell_calls.index("dumpsys package com.instagram.android")  # after the run


def test_scrape_once_still_stops_instagram_when_the_run_raises(monkeypatch):
    def boom(d):
        raise scraper.DeviceNotReady("feed never opened")

    monkeypatch.setattr(scraper, "open_target_feed", boom)
    d = feed_device()
    with pytest.raises(scraper.DeviceNotReady):
        scraper.scrape_once(d, scraper.db_init())
    assert d.shell_calls.count(f"am force-stop {scraper.IG_PKG}") == 2
    for pkg in scraper.CACHED_APP_SWEEP:
        assert d.shell_calls.count(f"am force-stop {pkg}") == 2


def test_memory_guard_stops_the_run_before_the_limit(monkeypatch):
    monkeypatch.setattr(scraper, "MEMORY_GUARD_PERCENT", 85)
    monkeypatch.setattr(scraper, "MAX_STORIES_PER_RUN", 0)
    # start, before stories, first screen: fine; second screen check: 2700 of 3072 MiB is 88%.
    d = with_cgroup(
        feed_device(), [cgroup_output(900), cgroup_output(1200), cgroup_output(1500), cgroup_output(2700)]
    )
    stats = scraper.scrape_once(d, scraper.db_init())
    assert "stopped early: redroid memory at 2700 of 3072 MiB (MEMORY_GUARD_PERCENT=85)" in stats["warning"]
    assert stats["mem_peak_mb"] == 2700
    assert stats["oom_kills"] == 0


def test_memory_guard_can_be_disabled(monkeypatch):
    monkeypatch.setattr(scraper, "MEMORY_GUARD_PERCENT", 0)
    monkeypatch.setattr(scraper, "MAX_STORIES_PER_RUN", 0)
    monkeypatch.setattr(scraper, "MAX_SCROLLS", 1)
    stats = scraper.scrape_once(with_cgroup(feed_device(), [cgroup_output(3000)]), scraper.db_init())
    assert not (stats["warning"] or "").startswith("stopped early")
    assert stats["mem_peak_mb"] == 3000  # still measured


def test_memory_guard_skips_stories_when_already_over(monkeypatch):
    monkeypatch.setattr(scraper, "MAX_SCROLLS", 1)
    d = with_cgroup(feed_device(), [cgroup_output(2900)])
    stats = scraper.scrape_once(d, scraper.db_init())
    assert stats["new_stories"] == 0
    assert "skipped stories: redroid memory at 2900 of 3072 MiB" in stats["warning"]


def test_oom_kills_during_a_run_are_reported(monkeypatch):
    monkeypatch.setattr(scraper, "MAX_STORIES_PER_RUN", 0)
    monkeypatch.setattr(scraper, "MAX_SCROLLS", 1)
    d = with_cgroup(feed_device(), [cgroup_output(900, oom_kill=7), cgroup_output(1000, oom_kill=9)])
    stats = scraper.scrape_once(d, scraper.db_init())
    assert stats["oom_kills"] == 2
    assert "redroid OOM-killed 2 Android process(es)" in stats["warning"]


def test_main_records_memory_stats(fast_offline, monkeypatch):
    _stop_after_first_sleep(monkeypatch)
    monkeypatch.setattr(scraper, "connect_device", feed_device)
    stats = {"new": 0, "new_stories": 0, "warning": None, "mem_peak_mb": 1843, "oom_kills": 1}
    monkeypatch.setattr(scraper, "scrape_once", lambda d, con: stats)
    with pytest.raises(StopLoop):
        scraper.main()
    row = (
        sqlite3.connect(fast_offline / "posts.sqlite")
        .execute("SELECT mem_peak_mb, oom_kills FROM runs")
        .fetchone()
    )
    assert row == (1843, 1)


# --- media format ---------------------------------------------------------------------------------


def test_jpeg_media_format_still_writes_jpegs(fast_offline, monkeypatch):
    from PIL import Image

    monkeypatch.setattr(scraper, "MEDIA_FORMAT", "jpeg")
    path = scraper.capture_story_media(Image.new("RGB", (200, 400), "red"), "[0,0][200,400]", 0, "s")
    assert path.name == "s.jpg" and path.read_bytes()[:3] == b"\xff\xd8\xff"


def test_recapturing_an_avatar_in_a_new_format_drops_the_old_file(fast_offline, monkeypatch):
    avatars = scraper.MEDIA_DIR / "avatars"
    avatars.mkdir(parents=True)
    (avatars / "old_user.jpg").write_bytes(b"old jpeg")  # captured before switching MEDIA_FORMAT
    d = feed_device(start="older")
    header = scraper.parse_hierarchy(d.screens["older"])[0]["header_bounds"]
    assert scraper.capture_avatar(d, header, "old_user") == "avatars/old_user.webp"
    assert sorted(f.name for f in avatars.iterdir()) == ["old_user.webp"]


# --- startup wait ---------------------------------------------------------------------------------


def _record_last_run(con, minutes_ago, error=None):
    finished = (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat()
    scraper.record_run(con, finished, finished, 0, error, {})


@pytest.fixture
def poll_window(monkeypatch):
    monkeypatch.setattr(scraper, "POLL_MIN_H", 2.5)
    monkeypatch.setattr(scraper, "POLL_MAX_H", 4.5)
    monkeypatch.setattr(scraper, "TIME_DISTRIBUTION", "uniform")
    monkeypatch.setattr(scraper, "RETRY_DELAYS_MINUTES", [2.0, 5.0])
    monkeypatch.setattr(scraper, "SCRAPE_ON_STARTUP", False)


def test_startup_scrapes_immediately_with_no_recorded_run(poll_window):
    assert scraper._startup_wait_seconds(scraper.db_init()) == 0


def test_startup_waits_out_the_rest_of_the_poll_interval(poll_window):
    con = scraper.db_init()
    _record_last_run(con, minutes_ago=60)
    wait = scraper._startup_wait_seconds(con)
    assert 1.5 * 3600 - 5 <= wait <= 3.5 * 3600


def test_startup_does_not_wait_when_the_last_run_is_old(poll_window):
    con = scraper.db_init()
    _record_last_run(con, minutes_ago=5 * 60)
    assert scraper._startup_wait_seconds(con) == 0


@pytest.mark.parametrize(
    "error",
    [
        "DeviceNotReady('redroid still booting')",
        "LaunchUiAutomationError('server quit')",
        "AdbError('offline')",
    ],
)
def test_startup_after_a_transient_failure_waits_only_for_the_first_retry(poll_window, error):
    con = scraper.db_init()
    _record_last_run(con, minutes_ago=0.5, error=error)
    assert 85 <= scraper._startup_wait_seconds(con) <= 90  # 2 minutes, minus the 30s already passed


def test_startup_after_a_non_transient_failure_waits_a_full_interval(poll_window):
    con = scraper.db_init()
    _record_last_run(
        con, minutes_ago=1, error="RuntimeError(\"Instagram wants a human: 'Confirm it's you'\")"
    )
    assert scraper._startup_wait_seconds(con) >= 2.5 * 3600 - 65


def test_scrape_on_startup_skips_the_wait(poll_window, monkeypatch):
    monkeypatch.setattr(scraper, "SCRAPE_ON_STARTUP", True)
    con = scraper.db_init()
    _record_last_run(con, minutes_ago=1)
    assert scraper._startup_wait_seconds(con) == 0


def test_main_waits_before_its_first_scrape(fast_offline, monkeypatch):
    sleeps = _stop_after_first_sleep(monkeypatch)
    monkeypatch.setattr(scraper, "_startup_wait_seconds", lambda con: 123.0)
    connects = []
    monkeypatch.setattr(scraper, "connect_device", lambda: connects.append(1))
    with pytest.raises(StopLoop):
        scraper.main()
    assert sleeps == [123.0] and connects == []  # slept first, never connected


# --- failure logcat -------------------------------------------------------------------------------

LOGCAT = """\
09-14 17:16:39.100  1234  1250 I ActivityManager: Start proc 5678:com.instagram.android
09-14 17:16:39.200  1234  1250 D Something: chatter
09-14 17:16:40.000   512   530 E AndroidRuntime: FATAL EXCEPTION IN SYSTEM PROCESS: main
09-14 17:16:40.010   512   530 F libc    : Fatal signal 6 (SIGABRT)
09-14 17:16:41.000   400   400 I lowmemorykiller: Kill 'com.android.settings' (8123), uid 1000
09-14 17:16:42.000   512   540 W Watchdog: *** WATCHDOG KILLING SYSTEM PROCESS: Blocked in handler
"""


def test_filter_logcat_keeps_errors_fatals_and_known_signatures(monkeypatch):
    monkeypatch.setattr(scraper, "LOGCAT_TAIL_LINES", 2000)
    kept = scraper._filter_logcat(LOGCAT)
    assert [line.split(": ", 1)[0].split()[-1] for line in kept] == [
        "AndroidRuntime",
        "libc",
        "lowmemorykiller",
        "Watchdog",
    ]
    monkeypatch.setattr(scraper, "LOGCAT_TAIL_LINES", 2)
    assert len(scraper._filter_logcat(LOGCAT)) == 2  # the tail, not the head
    assert "WATCHDOG KILLING" in scraper._filter_logcat(LOGCAT)[-1]


def test_save_failure_logcat_writes_a_filtered_file(fast_offline, monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=LOGCAT, stderr="")

    monkeypatch.setattr(scraper.subprocess, "run", fake_run)
    path = SAVE_FAILURE_LOGCAT("DeviceNotReady('could not bring com.instagram.android to the foreground')")
    assert calls == [["adb", "-s", scraper.ADB_ADDR, "logcat", "-d", "-v", "threadtime"]]
    assert path.parent == scraper.DEBUG_DIR and path.name.startswith("logcat_") and path.suffix == ".txt"
    text = path.read_text()
    assert text.startswith("# run failed: DeviceNotReady('could not bring")
    assert "FATAL EXCEPTION" in text and "Something: chatter" not in text


def test_save_failure_logcat_tolerates_an_unreachable_device(fast_offline, monkeypatch):
    monkeypatch.setattr(
        scraper.subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="error: device offline"),
    )
    assert SAVE_FAILURE_LOGCAT("AdbError('offline')") is None
    assert not list(scraper.DEBUG_DIR.glob("logcat_*")) if scraper.DEBUG_DIR.exists() else True


def test_main_saves_a_logcat_only_for_device_failures(fast_offline, monkeypatch, no_real_logcat):
    def run_main_once(error):
        _stop_after_first_sleep(monkeypatch)

        def failing():
            raise error

        monkeypatch.setattr(scraper, "connect_device", failing)
        with pytest.raises(StopLoop):
            scraper.main()

    run_main_once(adbutils.AdbError("device 127.0.0.1:5555 not online"))
    assert len(no_real_logcat) == 1 and no_real_logcat[0].startswith("AdbError")
    run_main_once(RuntimeError("Instagram wants a human"))
    assert len(no_real_logcat) == 1  # a login challenge isn't a device failure


def test_failure_logcats_are_pruned_like_other_debug_files(fast_offline, monkeypatch):
    monkeypatch.setattr(scraper, "DEBUG_KEEP", 2)
    scraper.DEBUG_DIR.mkdir(parents=True)
    for i in range(4):
        f = scraper.DEBUG_DIR / f"logcat_2026091{i}T000000Z.txt"
        f.write_text("x")
        os.utime(f, (1_800_000_000 + i, 1_800_000_000 + i))
    monkeypatch.setattr(scraper, "DEBUG_RETAIN_DAYS", 0)
    scraper._prune_debug_dumps()
    assert sorted(f.name for f in scraper.DEBUG_DIR.iterdir()) == [
        "logcat_20260912T000000Z.txt",
        "logcat_20260913T000000Z.txt",
    ]
