"""End-to-end-ish tests of the device-driving code (login, feed navigation, share sheet, carousels,
stories, the scrape loop, main()) against tests.fakedevice.FakeDevice. Screens are synthetic."""

import os
import sqlite3
import subprocess
import sys
import time
import zipfile
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NoReturn, TypedDict, Unpack

import adbutils
import igprofiles
import pytest
import uiautomator2 as u2
from igprofiles.v424.selectors import SELECTORS as SELECTORS_445
from instadroid import (
    capture,
    config,
    db,
    device,
    diagnostics,
    install,
    navigation,
    parsing,
    scrape,
    stories,
    uidevice,
    versioning,
)
from PIL import Image, ImageDraw, ImageOps

from tests.fakedevice import HEIGHT, WIDTH, FakeDevice, Node, Out, hierarchy, node

SAVE_FAILURE_LOGCAT = diagnostics.save_failure_logcat  # captured before conftest stubs it out
_caption_class = SELECTORS_445["caption_class"]  # read at import, before conftest pins the profile
assert isinstance(_caption_class, str)
CAPTION = _caption_class
ACTION_BAR = node("action_bar_container", bounds=(0, 142, 1080, 289))
FOLLOWING_TITLE = node(
    "action_bar_title", cls="android.widget.TextView", text="Following", bounds=(150, 160, 500, 270)
)
TOP_URL = "https://www.instagram.com/reel/TOP123/?igsh=abc"
OTHER_URL = "https://www.instagram.com/p/OTHER1/?igsh=xyz"
PROFILE_TAB = node("profile_tab", desc="Profile", bounds=(864, 2088, 1080, 2214), goto="profile")

type SqlValue = str | int | float | bytes | None


def rows(con: sqlite3.Connection, sql: str) -> list[dict[str, SqlValue]]:
    """A query's rows as {column: value}, for a connection whose row_factory is sqlite3.Row."""
    found: list[sqlite3.Row] = con.execute(sql).fetchall()
    return [dict(zip(r.keys(), r, strict=True)) for r in found]


def row(con: sqlite3.Connection, sql: str) -> dict[str, SqlValue] | None:
    """The first row of rows(), or None when the query matches nothing."""
    found = rows(con, sql)
    return found[0] if found else None


def values(con: sqlite3.Connection, sql: str) -> list[tuple[SqlValue, ...]]:
    """A query's rows as plain tuples, whatever the connection's row_factory."""
    found: list[Iterable[SqlValue]] = con.execute(sql).fetchall()
    return [tuple(r) for r in found]


def scalar(con: sqlite3.Connection, sql: str) -> SqlValue:
    """The first column of the first row, e.g. a COUNT(*)."""
    return values(con, sql)[0][0]


class RunOptions(TypedDict, total=False):
    """The subprocess.run() keyword arguments the install and logcat code passes."""

    check: bool
    capture_output: bool
    text: bool
    errors: str
    timeout: float


def story_button(user: str, index: int, seen: bool, x: int, goto: str | None = None) -> Node:
    desc = f"{user}'s story, {index} of 3, {'Seen' if seen else 'Unseen'}."
    return node(cls="android.widget.Button", desc=desc, bounds=(x, 300, x + 180, 480), goto=goto)


def home_screen(switcher_goto: str = "menu", extra: Iterable[Node] = ()) -> str:
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


def feed_cards(slide: int = 1, top_share: str = "share_top", other_share: str = "share_other") -> list[Node]:
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


def following_screen(cards: list[Node] | None = None, sheet: Node | None = None) -> str:
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


def copy_link(clip: str | None = None) -> Node:
    return node(desc="Copy link", bounds=(0, 2240, 1080, 2330), clip=clip, goto="following")


OLDER_CARDS = [
    node("row_feed_profile_header", desc="old_user posted a photo 2 days ago", bounds=(0, 300, 1080, 437)),
    node("row_feed_photo_imageview", desc="Photo by Old User, 5 likes", bounds=(0, 437, 1080, 1500)),
    node("row_feed_button_share", bounds=(390, 1500, 453, 1621)),
    node(cls=CAPTION, text="old_user Old caption", bounds=(32, 1630, 1080, 1700)),
]


class FeedDeviceOptions(TypedDict, total=False):
    """The FakeDevice options feed_device() passes through (it sets back, scroll and hswipe itself)."""

    pull: dict[str, str] | None
    foreign: Iterable[str]
    launch_screen: str
    launch_blocked: bool
    installed: Iterable[str]
    ig_version: str


class FollowingDeviceOptions(FeedDeviceOptions, total=False):
    """feed_device()'s own arguments plus FeedDeviceOptions, as feed_device_with_following() forwards them."""

    top_share: str
    other_share: str
    start: str


def feed_device(
    top_share: str = "share_top",
    other_share: str = "share_other",
    *,
    start: str = "home",
    **kw: Unpack[FeedDeviceOptions],
) -> FakeDevice:
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
        start,
        back=back,
        scroll={"following": "older"},
        hswipe={"following": "following_s2", "following_s2": "following_s3"},
        **kw,
    )


def profile_screen(following_goto: str = "following_list") -> str:
    return hierarchy(
        ACTION_BAR,
        node(
            "profile_header_following_stacked_familiar",
            desc="following",
            bounds=(782, 321, 1038, 464),
            goto=following_goto,
        ),
    )


def following_list_screen(usernames: Iterable[str]) -> str:
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


def feed_device_with_following(
    pages: list[list[str]], main_scroll: dict[str, str] | None = None, **kw: Unpack[FollowingDeviceOptions]
) -> FakeDevice:
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
def fast_offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "posts.sqlite"))
    monkeypatch.setattr(config, "MEDIA_DIR", tmp_path / "media")
    monkeypatch.setattr(config, "DEBUG_DIR", tmp_path / "debug")
    monkeypatch.setattr(config, "APK_CACHE_DIR", tmp_path / "apk")
    monkeypatch.setattr(config, "IG_APK_VERSION", "latest")  # the top-level cache layout

    def no_pause(lo: float = 1.0, hi: float = 3.0) -> None:
        pass

    def no_sleep(seconds: float) -> None:
        pass

    monkeypatch.setattr(device, "human_pause", no_pause)
    monkeypatch.setattr(time, "sleep", no_sleep)
    monkeypatch.setattr(config, "IG_USERNAME", "me")
    monkeypatch.setattr(config, "IG_PASSWORD", "hunter2")
    monkeypatch.setattr(capture, "_last_url", "")
    monkeypatch.setattr(config, "CLIPBOARD_TIMEOUT", 0.01)
    return tmp_path


# --- login ------------------------------------------------------------------------------------


def login_screen(button_goto: str = "notnow") -> str:
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


def text_screen(text: str, goto: str | None = None) -> str:
    return hierarchy(node(cls="android.widget.TextView", text=text, bounds=(0, 1000, 1080, 1100), goto=goto))


def test_login_fills_the_form_and_dismisses_interstitials() -> None:
    screens = {"login": login_screen(), "notnow": text_screen("Not now", goto="home"), "home": home_screen()}
    d = FakeDevice(screens, "login")
    assert navigation.ensure_logged_in(d) is True
    assert d.typed == [(0, "me"), (1, "hunter2")]
    assert d.screen == "home"
    assert d.launches == ["com.instagram.mainactivity.LauncherActivity"]  # resolved, not monkey


def test_login_taps_through_the_logged_out_welcome_screen() -> None:
    screens = {
        "welcome": text_screen("I already have a profile", goto="login"),
        "login": login_screen(button_goto="home"),
        "home": home_screen(),
    }
    d = FakeDevice(screens, "welcome")
    assert navigation.ensure_logged_in(d) is True
    assert "login" in d.history


def test_login_dismisses_a_stray_ok_alert_and_accepts_a_live_session() -> None:
    d = FakeDevice({"alert": text_screen("OK", goto="home"), "home": home_screen()}, "alert")
    assert navigation.ensure_logged_in(d) is True
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
def test_login_failures_raise_with_a_debug_dump(
    screens: dict[str, str], start: str, match: str, fast_offline: Path
) -> None:
    with pytest.raises(RuntimeError, match=match):
        navigation.ensure_logged_in(FakeDevice(screens, start))
    assert (fast_offline / "debug" / "login_hierarchy.xml").exists()


def test_login_without_credentials_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "IG_PASSWORD", "")
    with pytest.raises(RuntimeError, match="not set"):
        navigation.ensure_logged_in(FakeDevice({"login": login_screen()}, "login"))


def test_login_raises_when_instagram_is_not_installed_and_auto_install_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "IG_AUTO_INSTALL", False)
    with pytest.raises(RuntimeError, match="not installed"):
        navigation.ensure_logged_in(FakeDevice({}, "launcher", installed=()))


def _apk_run(
    monkeypatch: pytest.MonkeyPatch, d: FakeDevice, calls: list[list[str]], *, fail_on: str | None = None
) -> None:
    """Patch subprocess.run to fake apkeep + adb install without touching the network or a
    real device, recording every invocation into `calls`. `fail_on` (argv[0], "apkeep" or "adb")
    makes that step raise CalledProcessError. A successful "adb install"/"install-multiple" flips
    `d`'s installed set, same as a real adb install would."""

    def fake_run(cmd: list[str], **kwargs: Unpack[RunOptions]) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if fail_on and cmd[0] == fail_on:
            raise subprocess.CalledProcessError(1, cmd, output="", stderr="boom")
        if cmd[0] == "apkeep":
            out_dir = Path(cmd[cmd.index("-d") + 2])
            out_dir.mkdir(parents=True, exist_ok=True)
            xapk = out_dir / f"{config.IG_PKG}@1.0.0.xapk"
            with zipfile.ZipFile(xapk, "w") as zf:
                zf.writestr(f"{config.IG_PKG}.apk", b"base")
                zf.writestr("config.arm64_v8a.apk", b"split")
                zf.writestr("manifest.json", b"{}")
        elif cmd[0] == "adb":
            d.install()
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)


def test_ensure_logged_in_installs_instagram_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    d = FakeDevice({"home": home_screen()}, "launcher", installed=())
    calls: list[list[str]] = []
    _apk_run(monkeypatch, d, calls)
    assert navigation.ensure_logged_in(d) is True
    apkeep_call = next(c for c in calls if c[0] == "apkeep")
    assert apkeep_call[:3] == ["apkeep", "-a", config.IG_PKG]
    install_call = next(c for c in calls if c[0] == "adb")
    assert install_call[3] == "install-multiple"
    assert install_call[4].endswith(f"{config.IG_PKG}.apk")
    assert install_call[5].endswith("config.arm64_v8a.apk")


def test_installing_instagram_reactivates_the_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    d = FakeDevice({"home": home_screen()}, "launcher", installed=())
    _apk_run(monkeypatch, d, [])
    monkeypatch.setattr(versioning, "PROFILE_WARNING", "stale warning from before the install")
    assert navigation.ensure_logged_in(d) is True
    assert versioning.PROFILE.name == "v424" and versioning.PROFILE_WARNING is None  # installed 445.0.0.45.83


def test_auto_install_fetches_the_default_build(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "IG_APK_VERSION", "")
    monkeypatch.setattr(config, "IG_PROFILE", "")
    d = FakeDevice({"home": home_screen()}, "launcher", installed=(), ig_version="445.0.0.45.83")
    calls: list[list[str]] = []
    _apk_run(monkeypatch, d, calls)
    assert navigation.ensure_logged_in(d) is True
    assert next(c for c in calls if c[0] == "apkeep")[2] == f"{config.IG_PKG}@{igprofiles.DEFAULT_BUILD}"
    assert versioning.PROFILE.name == "v424" and versioning.PROFILE_WARNING is None


def test_a_pinned_apk_version_gets_its_own_cache_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    # An unpinned bundle already cached at the top level must not be installed for a pinned version.
    xapk_dir = config.APK_CACHE_DIR / "xapk"
    xapk_dir.mkdir(parents=True)
    (xapk_dir / f"{config.IG_PKG}.apk").write_bytes(b"latest")
    monkeypatch.setattr(config, "IG_APK_VERSION", "445.0.0.45.83")
    d = FakeDevice({"home": home_screen()}, "launcher", installed=())
    calls: list[list[str]] = []
    _apk_run(monkeypatch, d, calls)
    assert navigation.ensure_logged_in(d) is True
    apkeep_call = next(c for c in calls if c[0] == "apkeep")
    assert apkeep_call[2] == f"{config.IG_PKG}@445.0.0.45.83"
    assert apkeep_call[-1] == str(config.APK_CACHE_DIR / "445.0.0.45.83")
    install_call = next(c for c in calls if c[0] == "adb")
    assert all("/445.0.0.45.83/" in arg for arg in install_call[4:])


def test_install_version_replaces_a_newer_install_in_place(monkeypatch: pytest.MonkeyPatch) -> None:
    d = FakeDevice({"home": home_screen()}, "launcher", ig_version="446.0.0.49.77")

    def downgrade(pkg: str = config.IG_PKG) -> None:
        d.ig_version = "445.0.0.45.83"  # what the downgrade installs

    d.install = downgrade
    calls: list[list[str]] = []
    _apk_run(monkeypatch, d, calls)
    assert install.install_instagram_version(d, "445.0.0.45.83") == "445.0.0.45.83"
    assert next(c for c in calls if c[0] == "apkeep")[2] == f"{config.IG_PKG}@445.0.0.45.83"
    install_call = next(c for c in calls if c[0] == "adb")
    assert install_call[3:6] == ["install-multiple", "-r", "-d"]


def test_install_version_defaults_to_the_pinned_version_and_skips_when_already_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "IG_APK_VERSION", "445.0.0.45.83")
    d = FakeDevice({"home": home_screen()}, "launcher")  # already reports 445.0.0.45.83
    calls: list[list[str]] = []
    _apk_run(monkeypatch, d, calls)
    assert install.install_instagram_version(d) == "445.0.0.45.83"
    assert calls == []


def test_install_version_raises_when_the_device_reports_another_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    d = FakeDevice({"home": home_screen()}, "launcher", ig_version="446.0.0.49.77")
    _apk_run(monkeypatch, d, [])  # the fake install leaves the version untouched
    with pytest.raises(device.DeviceNotReady, match="device reports 446.0.0.49.77"):
        install.install_instagram_version(d, "445.0.0.45.83")


def test_ensure_logged_in_reuses_a_cached_apk(monkeypatch: pytest.MonkeyPatch) -> None:
    xapk_dir = config.APK_CACHE_DIR / "xapk"
    xapk_dir.mkdir(parents=True)
    (xapk_dir / f"{config.IG_PKG}.apk").write_bytes(b"base")
    d = FakeDevice({"home": home_screen()}, "launcher", installed=())
    calls: list[list[str]] = []
    _apk_run(monkeypatch, d, calls)
    assert navigation.ensure_logged_in(d) is True
    assert not any(c[0] == "apkeep" for c in calls)
    install_call = next(c for c in calls if c[0] == "adb")
    assert install_call[3] == "install"  # single apk: no -multiple


def test_ensure_logged_in_raises_transiently_when_apkeep_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    d = FakeDevice({}, "launcher", installed=())
    _apk_run(monkeypatch, d, [], fail_on="apkeep")
    with pytest.raises(device.DeviceNotReady):
        navigation.ensure_logged_in(d)


def test_ensure_logged_in_raises_transiently_when_install_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    d = FakeDevice({}, "launcher", installed=())
    _apk_run(monkeypatch, d, [], fail_on="adb")
    with pytest.raises(device.DeviceNotReady):
        navigation.ensure_logged_in(d)


def test_app_that_never_foregrounds_is_a_transient_device_failure() -> None:
    d = FakeDevice({}, "launcher", launch_blocked=True)
    with pytest.raises(device.DeviceNotReady) as exc:
        navigation.ensure_logged_in(d)
    assert device.is_transient(exc.value)
    assert len(d.launches) == 4  # the first launch plus three retries


def test_an_app_that_dies_right_after_launch_is_not_logged_in() -> None:
    class CrashingDevice(FakeDevice):
        """Instagram comes to the front, then crashes back to the launcher a moment later."""

        checks = 0

        def app_current(self) -> dict[str, str]:
            self.checks += 1
            return {"package": config.IG_PKG if self.checks == 1 else "com.android.launcher3"}

    d = CrashingDevice({"home": home_screen()}, "launcher")
    with pytest.raises(device.DeviceNotReady, match="left the foreground right after launch") as exc:
        navigation.ensure_logged_in(d)
    assert device.is_transient(exc.value)  # retried, with a logcat saved, like any device failure


# --- feed navigation --------------------------------------------------------------------------


def test_open_following_feed_goes_through_the_switcher() -> None:
    d = feed_device()
    assert navigation.open_following_feed(d) is True
    assert d.history[-2:] == ["menu", "following"]


def test_open_following_feed_reenters_a_following_screen_left_over_from_last_run() -> None:
    d = feed_device(start="following")
    assert navigation.open_following_feed(d) is True
    assert d.history == ["following", "home", "menu", "following"]


def test_open_following_feed_backs_out_of_an_unrelated_screen() -> None:
    d = feed_device(start="profile")
    d.screens["profile"] = hierarchy(node(text="Edit profile"))
    d.back["profile"] = "home"
    assert navigation.open_following_feed(d) is True
    assert d.presses[0] == "back"


def test_open_following_feed_gives_up_when_the_switcher_never_opens(fast_offline: Path) -> None:
    d = FakeDevice({"home": home_screen(switcher_goto="")}, "home")
    assert navigation.open_following_feed(d) is False
    assert (fast_offline / "debug" / "feed_switch_hierarchy.xml").exists()


def test_close_sheets_relaunches_if_back_leaves_the_app() -> None:
    d = feed_device(start="share_top")
    d.back["share_top"] = "launcher"
    assert navigation.close_sheets(d) is True
    assert d.screen == "home"


def test_back_to_feed_relaunches_from_outside_the_app() -> None:
    d = feed_device(start="launcher", launch_screen="following")
    assert navigation.back_to_feed(d) is True


# --- permalinks -------------------------------------------------------------------------------


def top_card_id(d: FakeDevice) -> str:
    return parsing.post_id(parsing.parse_hierarchy(d.dump_hierarchy())[0])


def test_fetch_permalink_copies_and_canonicalises_the_link() -> None:
    d = feed_device(start="following")
    assert capture.fetch_permalink(d, top_card_id(d)) == ("https://www.instagram.com/reel/TOP123/", None)
    assert d.screen == "following"


def test_fetch_permalink_reports_a_sheet_that_never_opens(fast_offline: Path) -> None:
    d = feed_device(top_share="", start="following")
    assert capture.fetch_permalink(d, top_card_id(d)) == (None, "sheet")
    assert (fast_offline / "debug" / "share_sheet_hierarchy.xml").exists()


def test_fetch_permalink_reports_a_clipboard_that_never_updates() -> None:
    d = feed_device(top_share="share_noclip", start="following")
    assert capture.fetch_permalink(d, top_card_id(d)) == (None, "clipboard")


def test_fetch_permalink_ignores_the_previous_posts_link_left_in_the_clipboard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(capture, "_last_url", TOP_URL)
    d = feed_device(start="following")
    assert capture.fetch_permalink(d, top_card_id(d)) == (None, "clipboard")


def test_fetch_permalink_when_the_card_is_gone() -> None:
    assert capture.fetch_permalink(feed_device(start="following"), "no-such-card") == (None, "sheet")


def test_fetch_permalink_refuses_to_act_inside_a_stuck_sheet() -> None:
    d = feed_device(start="share_top")
    d.back["share_top"] = "share_top"
    assert capture.fetch_permalink(d, "anything") == (None, "sheet")
    assert d.taps == []  # never tapped anything inside the sheet


def test_force_stop_is_one_shell_call_and_best_effort() -> None:
    d = feed_device(start="following")

    device.free_device_memory(d)

    assert d.shell_calls == [f"am force-stop {pkg}" for pkg in (*device.CACHED_APP_SWEEP, config.IG_PKG)]

    def raising_shell(cmdargs: str | list[str], timeout: float = 60) -> NoReturn:
        raise RuntimeError("device offline")

    d.shell = raising_shell
    device.force_stop(d, config.IG_PKG)  # must not raise


def _caption_card(text: str, goto: str | None = None) -> list[Node]:
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


def test_expand_caption_taps_more_and_returns_the_full_text() -> None:
    d = FakeDevice(
        {
            "following": following_screen(_caption_card("someone_nice Short start… more", goto="expanded")),
            "expanded": following_screen(
                _caption_card("someone_nice Short start continues on with the full text")
            ),
        },
        "following",
    )
    p = parsing.parse_hierarchy(d.dump_hierarchy())[0]
    assert p["caption_truncated"] is True

    assert capture.expand_caption(d, p) == "Short start continues on with the full text"


def test_expand_caption_falls_back_to_the_truncated_text_when_the_tap_does_nothing() -> None:
    d = FakeDevice(
        {
            "following": following_screen(_caption_card("someone_nice Short start… more"))
        },  # no goto: tap is inert
        "following",
    )
    p = parsing.parse_hierarchy(d.dump_hierarchy())[0]

    assert capture.expand_caption(d, p) == "Short start…"
    assert d.screen == "following"  # never knocked off the feed


def test_expand_caption_is_a_noop_for_a_caption_that_was_never_truncated() -> None:
    d = FakeDevice(
        {"following": following_screen(_caption_card("someone_nice Whole caption, no more span"))},
        "following",
    )
    p = parsing.parse_hierarchy(d.dump_hierarchy())[0]
    assert p["caption_truncated"] is False

    assert capture.expand_caption(d, p) == "Whole caption, no more span"
    assert d.taps == []  # nothing to tap


# --- the scrape loop --------------------------------------------------------------------------


def _seed_post(
    con: sqlite3.Connection,
    pid: str,
    username: str,
    caption: str,
    days_ago: int,
    h: str | None = None,
    url: str | None = None,
) -> None:
    ts = (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()
    con.execute(
        "INSERT INTO posts (id, username, kind, posted_date, caption, media_file, scraped_at, hash, url,"
        " posted_at, updated_at) VALUES (?,?,?,?,?,NULL,?,?,?,?,?)",
        (pid, username, "photo", "x", caption, ts, h or pid, url, ts, ts),
    )
    con.commit()


def test_scrape_once_end_to_end(fast_offline: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "MAX_CAROUSEL_SLIDES", 3)
    monkeypatch.setattr(config, "STOP_AFTER_SEEN", 1)
    media = fast_offline / "media"
    con = db.db_init()
    d = feed_device()
    old_card = parsing.parse_hierarchy(d.screens["older"])[0]
    _seed_post(con, "OLD1", "old_user", "Old caption", 2, h=parsing.post_id(old_card))

    stats = scrape.scrape_once(d, con)

    assert stats == {
        "new": 2,
        "new_stories": 1,
        "link_sheet_failures": 0,
        "link_clipboard_failures": 0,
        "warning": None,
        "filtered_posts": 0,
        "mem_peak_mb": None,  # the fake device has no cgroup files: the memory guard is off
        "oom_kills": None,
        "cards_per_screen": 1.75,  # 2 cards on screen 0, 1 on screen 1 (see the log below)
        "share_captioned": 1.0,
        "share_complete": 1.0,
    }
    posts = {r["id"]: r for r in rows(con, "SELECT * FROM posts")}
    assert set(posts) == {"TOP123", "OTHER1", "OLD1"}
    assert posts["TOP123"]["username"] == "someone_nice" and posts["TOP123"]["kind"] == "video"
    assert posts["TOP123"]["url"] == "https://www.instagram.com/reel/TOP123/"
    assert posts["OTHER1"]["place"] == "Anytown, Somewhere"
    assert posts["OTHER1"]["caption"] == "Second caption"
    assert posts["TOP123"]["ig_version"] == posts["OTHER1"]["ig_version"] == "445.0.0.45.83"
    assert posts["OLD1"]["ig_version"] is None  # seeded before this run; never back-filled
    slides = values(con, "SELECT idx, file FROM media WHERE post_id='OTHER1' ORDER BY idx")
    assert slides == [(1, "OTHER1_1.webp"), (2, "OTHER1_2.webp")]
    for fn in ("TOP123.webp", "OTHER1.webp", "OTHER1_1.webp", "OTHER1_2.webp"):
        assert (media / fn).exists()
        assert (media / fn).read_bytes()[8:12] == b"WEBP"  # the default MEDIA_FORMAT
    assert (media / "avatars" / "other_user.webp").exists()
    assert (media / "avatars" / "old_user.webp").exists()
    stored_stories = rows(con, "SELECT username, media_file FROM stories")
    assert [s["username"] for s in stored_stories] == ["alice"]  # bob's viewer never opened, carol was seen
    story_file = stored_stories[0]["media_file"]
    assert isinstance(story_file, str)
    assert (media / story_file).exists()
    assert d.presses[-1] == "home"
    for pkg in device.CACHED_APP_SWEEP:
        assert f"am force-stop {pkg}" in d.shell_calls
    assert f"am force-stop {config.IG_PKG}" in d.shell_calls


def test_scrape_once_without_permalinks_falls_back_to_hash_ids_and_merges_a_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "MAX_STORIES_PER_RUN", 0)
    monkeypatch.setattr(config, "MAX_CAROUSEL_SLIDES", 1)
    monkeypatch.setattr(config, "MAX_SCROLLS", 2)
    monkeypatch.setattr(config, "PERMALINK_RETRIES", 1)
    monkeypatch.setattr(config, "SHARE_TAP_TRIES", 1)
    con = db.db_init()
    # Stored earlier, before the caption rendered: same author and day, placeholder caption.
    _seed_post(con, "placeholder", "someone_nice", "Reel by Someone Nice", 3)
    d = feed_device(top_share="", other_share="")
    d.scroll = {}  # scrolling shows nothing new: only the two cards above are in play

    stats = scrape.scrape_once(d, con)

    assert stats["new"] == 1  # the Reel merged into the placeholder instead of being stored twice
    assert stats["link_sheet_failures"] == 4  # two attempts per card
    merged = row(con, "SELECT * FROM posts WHERE id='placeholder'")
    assert merged is not None
    assert merged["caption"] == "Top card caption…"
    assert merged["ig_version"] == "445.0.0.45.83"  # the placeholder had none; the merge fills it in
    assert merged["media_file"]
    other = row(con, "SELECT * FROM posts WHERE username='other_user'")
    assert other is not None
    assert other["url"] is None and other["id"] == other["hash"]


def test_scrape_once_drops_a_permalink_that_belongs_to_another_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "MAX_STORIES_PER_RUN", 0)
    monkeypatch.setattr(config, "MAX_CAROUSEL_SLIDES", 1)
    monkeypatch.setattr(config, "MAX_SCROLLS", 1)
    con = db.db_init()
    _seed_post(con, "TOP123", "someone_else", "Unrelated", 10, h="unrelated")

    scrape.scrape_once(feed_device(), con)

    stored = row(con, "SELECT * FROM posts WHERE username='someone_nice'")
    assert stored is not None
    assert stored["url"] is None and stored["id"] != "TOP123"  # stored under its hash, not the stale link


def test_scrape_once_treats_an_edited_caption_as_the_same_post(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "MAX_STORIES_PER_RUN", 0)
    monkeypatch.setattr(config, "MAX_CAROUSEL_SLIDES", 1)
    monkeypatch.setattr(config, "MAX_SCROLLS", 1)
    con = db.db_init()
    _seed_post(con, "TOP123", "someone_nice", "Caption before the edit", 3, h="old-hash")
    d = feed_device()

    stats = scrape.scrape_once(d, con)

    stored = row(con, "SELECT hash FROM posts WHERE id='TOP123'")
    assert stored is not None
    assert stored["hash"] == top_card_id(feed_device(start="following"))  # re-keyed to the new caption
    assert stats["new"] == 1  # only the other card is new
    assert scalar(con, "SELECT COUNT(*) FROM posts WHERE username='someone_nice'") == 1


# --- feed mode (chrono vs home) ------------------------------------------------------------------


def home_feed_screen(cards: list[Node] | None = None) -> str:
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


def test_open_home_feed_navigates_via_the_home_tab(fast_offline: Path) -> None:
    # feed_device()'s own back map already sends "following" -> "home"; open_home_feed() has no
    # switcher to tap, so this is the same recovery path open_following_feed() itself relies on.
    d = feed_device(start="following")
    assert navigation.open_home_feed(d) is True
    assert d.screen == "home"


def test_on_target_feed_matches_feed_mode(fast_offline: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = FakeDevice({"home": home_feed_screen()}, "home")
    following = FakeDevice({"following": following_screen()}, "following")
    monkeypatch.setattr(config, "FEED_MODE", "home")
    assert navigation.on_target_feed(home) is True
    assert navigation.on_target_feed(following) is False
    monkeypatch.setattr(config, "FEED_MODE", "chrono")
    assert navigation.on_target_feed(home) is False
    assert navigation.on_target_feed(following) is True


def test_open_target_feed_dispatches_by_feed_mode(
    fast_offline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    d = feed_device(start="home")
    monkeypatch.setattr(config, "FEED_MODE", "home")
    assert navigation.open_target_feed(d) is True
    assert d.screen == "home"
    d = feed_device(start="home")
    monkeypatch.setattr(config, "FEED_MODE", "chrono")
    assert navigation.open_target_feed(d) is True
    assert d.screen == "following"


def test_unknown_feed_mode_falls_back_to_chrono() -> None:
    # A subprocess: FEED_MODE is resolved at import, and reloading config in place would leak into
    # every other test in the session.
    result = subprocess.run(
        [sys.executable, "-c", "from instadroid import config; print(config.FEED_MODE)"],
        cwd=Path(__file__).parent.parent,
        env={**os.environ, "FEED_MODE": "algorithmic"},
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip().splitlines()[-1] == "chrono"  # the fallback warning also prints


def test_scrape_once_stays_on_home_feed_when_feed_mode_is_home(
    fast_offline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "FEED_MODE", "home")
    monkeypatch.setattr(config, "MAX_STORIES_PER_RUN", 0)
    monkeypatch.setattr(config, "MAX_SCROLLS", 1)
    monkeypatch.setattr(config, "STOP_AFTER_SEEN", 1)
    con = db.db_init()
    # Seed every card already-known so nothing new needs the share-sheet round trip -- this test
    # is about which feed scrape_once() navigates to, not about re-testing that flow.
    for i, card in enumerate(parsing.parse_hierarchy(home_feed_screen())):
        _seed_post(con, f"SEEN{i}", card["username"], "already stored", 1, h=parsing.post_id(card))
    d = FakeDevice(
        {"home": home_feed_screen(), "following": following_screen(), "menu": MENU},
        "home",
        back={"following": "home", "menu": "home"},
    )

    scrape.scrape_once(d, con)

    assert "following" not in d.history  # never navigated to the chronological feed
    assert "menu" not in d.history  # never opened the switcher either


def test_open_own_following_list_navigates_from_the_feed(fast_offline: Path) -> None:
    d = feed_device_with_following([["alice", "bob"]], start="following")
    assert navigation.open_own_following_list(d) is True
    assert d.screen == "following_list"
    assert d.history[-3:] == ["following", "profile", "following_list"]


def test_open_own_following_list_leaves_and_reenters_when_already_on_the_list_screen(
    fast_offline: Path,
) -> None:
    # A real live run (2026-09-11) found this exact case: a second refresh in the same app session
    # started mid-scroll instead of at the top, collecting 9 of 30 followed accounts instead of a
    # fresh scroll's 27+ — accepting "already there" as done is the bug this guards against.
    d = feed_device_with_following([["alice", "bob"]], start="following_list")
    assert navigation.open_own_following_list(d) is True
    assert d.screen == "following_list"
    assert d.history.count("following_list") == 2  # left, then genuinely navigated back in


def test_scrape_following_list_scrolls_until_no_new_username_appears(
    fast_offline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "FOLLOWING_LIST_EMPTY_LIMIT", 1)
    d = feed_device_with_following([["alice", "bob"], ["carol"]], main_scroll={}, start="following_list")
    assert navigation.scrape_following_list(d) == ["alice", "bob", "carol"]


def test_scrape_following_list_returns_none_when_nothing_is_ever_parsed(
    fast_offline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "FOLLOWING_LIST_EMPTY_LIMIT", 1)
    d = feed_device_with_following([[]], main_scroll={}, start="following_list")
    assert navigation.scrape_following_list(d) is None


def test_refresh_following_list_replaces_the_stored_list(
    fast_offline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "FOLLOWING_LIST_EMPTY_LIMIT", 1)
    con = db.db_init()
    con.execute("INSERT INTO following (username, updated_at) VALUES ('stale_unfollowed', '2020-01-01')")
    con.commit()
    d = feed_device_with_following([["alice", "bob"]], main_scroll={}, start="following")

    n = navigation.refresh_following_list(d, con)

    assert n == 2
    assert {r[0] for r in values(con, "SELECT username FROM following")} == {"alice", "bob"}


def test_refresh_following_list_keeps_the_existing_list_on_a_failed_scrape(
    fast_offline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "FOLLOWING_LIST_EMPTY_LIMIT", 1)
    con = db.db_init()
    con.execute("INSERT INTO following (username, updated_at) VALUES ('good_data', '2020-01-01')")
    con.commit()
    d = feed_device_with_following([[]], main_scroll={}, start="following")  # empty list = parse failure

    n = navigation.refresh_following_list(d, con)

    assert n is None
    assert {r[0] for r in values(con, "SELECT username FROM following")} == {"good_data"}


def test_scrape_once_filters_posts_from_accounts_not_on_the_refreshed_following_list(
    fast_offline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "FOLLOWING_REFRESH_DAYS", 7)
    monkeypatch.setattr(config, "FOLLOWING_LIST_EMPTY_LIMIT", 1)
    monkeypatch.setattr(config, "MAX_STORIES_PER_RUN", 0)
    monkeypatch.setattr(config, "MAX_CAROUSEL_SLIDES", 1)
    monkeypatch.setattr(config, "MAX_SCROLLS", 2)
    con = db.db_init()  # following table starts empty -> due for a refresh this run
    # Only someone_nice is on the (about-to-be-scraped) Following list; other_user is not.
    d = feed_device_with_following([["someone_nice"]], main_scroll={})

    stats = scrape.scrape_once(d, con)

    assert {r[0] for r in values(con, "SELECT username FROM following")} == {"someone_nice"}
    assert stats["filtered_posts"] >= 1
    posts = values(con, "SELECT username FROM posts")
    assert any(r[0] == "someone_nice" for r in posts)
    assert all(r[0] != "other_user" for r in posts)
    # A filtered post's account is never upserted -- no avatar work, no accounts-table footprint.
    assert row(con, "SELECT 1 FROM accounts WHERE username='other_user'") is None


def test_scrape_once_does_not_filter_before_the_first_successful_refresh(
    fast_offline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Enabled but never yet refreshed, and this run's own refresh attempt also finds nothing
    # (empty Following-list screen) -- an empty allowlist must mean "not initialized," not "filter
    # everything," or turning the feature on would silently drop every post on its first run.
    monkeypatch.setattr(config, "FOLLOWING_REFRESH_DAYS", 7)
    monkeypatch.setattr(config, "FOLLOWING_LIST_EMPTY_LIMIT", 1)
    monkeypatch.setattr(config, "MAX_STORIES_PER_RUN", 0)
    monkeypatch.setattr(config, "MAX_CAROUSEL_SLIDES", 1)
    monkeypatch.setattr(config, "MAX_SCROLLS", 2)
    monkeypatch.setattr(config, "STOP_AFTER_SEEN", 1)
    con = db.db_init()
    d = feed_device_with_following([[]], main_scroll={})  # the refresh itself finds nothing

    stats = scrape.scrape_once(d, con)

    assert stats["filtered_posts"] == 0
    posts = {r[0] for r in values(con, "SELECT username FROM posts")}
    assert posts == {"someone_nice", "other_user"}  # nothing dropped


# --- device connection and the main loop ------------------------------------------------------


class StopLoop(Exception):
    pass


def fake_adb_connect(addr: str, timeout: float | None = None) -> None:
    pass


def test_connect_device(monkeypatch: pytest.MonkeyPatch) -> None:
    dev = feed_device()
    monkeypatch.setattr(adbutils.adb, "connect", fake_adb_connect)

    def fake_u2_connect(addr: str | None = None) -> FakeDevice:
        return dev

    monkeypatch.setattr(u2, "connect", fake_u2_connect)
    assert device.connect_device() is dev
    assert (
        versioning.PROFILE.name == "v424" and versioning.PROFILE_WARNING is None
    )  # device reports 445.0.0.45.83


def test_connect_device_warns_about_an_installed_version_nobody_has_validated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dev = feed_device()
    dev.ig_version = "999.0.0.1.1"
    monkeypatch.setattr(config, "IG_PROFILE", "")
    monkeypatch.setattr(adbutils.adb, "connect", fake_adb_connect)

    def fake_u2_connect(addr: str | None = None) -> FakeDevice:
        return dev

    monkeypatch.setattr(u2, "connect", fake_u2_connect)
    device.connect_device()
    assert versioning.PROFILE.name == igprofiles.available()[-1]  # the newest profile covers it
    assert (versioning.PROFILE_WARNING or "").startswith(
        f"Instagram 999.0.0.1.1 hasn't been validated with profile {versioning.PROFILE.name}"
    )


def test_scrape_once_reports_the_profile_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "MAX_STORIES_PER_RUN", 0)
    monkeypatch.setattr(config, "MAX_SCROLLS", 1)
    monkeypatch.setattr(
        versioning, "PROFILE_WARNING", "Instagram 999.0.0.1.1 hasn't been validated with profile v424"
    )
    stats = scrape.scrape_once(feed_device(), db.db_init())
    assert stats["warning"] is not None
    assert "Instagram 999.0.0.1.1 hasn't been validated with profile v424" in stats["warning"]


def _stop_after_first_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    sleeps: list[float] = []

    def sleep(seconds: float) -> NoReturn:
        sleeps.append(seconds)
        raise StopLoop

    monkeypatch.setattr(time, "sleep", sleep)
    monkeypatch.setattr(config, "TIME_DISTRIBUTION", "uniform")
    monkeypatch.setattr(config, "CONTROL_POLL_SECONDS", 10**9)  # one sleep call per wait, not 30s steps
    return sleeps


def test_main_records_a_transient_failure_and_retries_early(
    fast_offline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleeps = _stop_after_first_sleep(monkeypatch)
    monkeypatch.setattr(config, "RETRY_DELAYS_MINUTES", [2.0])

    def offline() -> NoReturn:
        raise adbutils.AdbError("device 127.0.0.1:5555 not online")

    monkeypatch.setattr(device, "connect_device", offline)

    with pytest.raises(StopLoop):
        scrape.main()

    error = values(sqlite3.connect(fast_offline / "posts.sqlite"), "SELECT error FROM runs")[0][0]
    assert isinstance(error, str) and error.startswith("AdbError")
    assert 2 * 60 <= sleeps[0] <= 3 * 60


def test_main_records_a_successful_run_with_device_versions(
    fast_offline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleeps = _stop_after_first_sleep(monkeypatch)
    monkeypatch.setattr(device, "connect_device", feed_device)
    stats: dict[str, int | str] = {
        "new": 2,
        "new_stories": 1,
        "link_sheet_failures": 1,
        "link_clipboard_failures": 0,
        "warning": "w",
    }

    def fake_scrape_once(d: uidevice.Device, con: sqlite3.Connection) -> dict[str, int | str]:
        return stats

    monkeypatch.setattr(scrape, "scrape_once", fake_scrape_once)

    with pytest.raises(StopLoop):
        scrape.main()

    con = sqlite3.connect(fast_offline / "posts.sqlite")
    con.row_factory = sqlite3.Row
    run = rows(con, "SELECT * FROM runs")[0]
    assert (run["new_posts"], run["new_stories"], run["warning"], run["error"]) == (2, 1, "w", None)
    assert (run["android_release"], run["ig_version"]) == ("13", "445.0.0.45.83")
    assert run["selector_profile"] == "v424"
    assert config.POLL_MIN_H * 3600 <= sleeps[0] <= config.POLL_MAX_H * 3600


# --- memory guard ---------------------------------------------------------------------------------

MIB = 1024 * 1024


def cgroup_output(
    current_mib: int, max_mib: int | None = 3072, oom_kill: int = 0, inactive_file_mib: int = 0
) -> str:
    """memory.current, memory.max, memory.events, then (part of) memory.stat, as `cat` prints them."""
    limit = "max" if max_mib is None else str(max_mib * MIB)
    usage = (current_mib + inactive_file_mib) * MIB
    return (
        f"{usage}\n{limit}\nlow 0\nhigh 0\nmax 12\noom 3\noom_kill {oom_kill}\noom_group_kill 0\n"
        f"anon {current_mib * MIB}\nfile {inactive_file_mib * MIB}\ninactive_file {inactive_file_mib * MIB}\n"
    )


def with_cgroup(d: FakeDevice, readings: Iterable[str]) -> FakeDevice:
    """Make FakeDevice `d` answer the memory guard's cgroup read with successive `readings`
    (cgroup_output() strings); the last one repeats."""
    shell = d.shell
    readings = list(readings)

    def fake_shell(cmdargs: str | list[str], timeout: float = 60) -> Out:
        joined = " ".join(cmdargs) if isinstance(cmdargs, list) else cmdargs
        if joined.startswith("cat /sys/fs/cgroup/memory.current"):
            d.shell_calls.append(joined)
            return Out(readings.pop(0) if len(readings) > 1 else readings[0])
        return shell(cmdargs, timeout)

    d.shell = fake_shell
    return d


def test_redroid_memory_parses_the_cgroup_files() -> None:
    d = with_cgroup(feed_device(), [cgroup_output(1843, 3072, oom_kill=7)])
    assert device._redroid_memory(d) == {"current": 1843 * MIB, "max": 3072 * MIB, "oom_kill": 7}
    unlimited = with_cgroup(feed_device(), [cgroup_output(500, None)])
    unlimited_reading = device._redroid_memory(unlimited)
    assert unlimited_reading is not None and unlimited_reading["max"] is None
    assert device._redroid_memory(feed_device()) is None  # no cgroup files: guard off


def test_redroid_memory_excludes_reclaimable_file_cache() -> None:
    # The live reading that stopped a run too early: 2756MiB counted, but ~600MiB was file cache.
    d = with_cgroup(feed_device(), [cgroup_output(2150, 3072, inactive_file_mib=606)])
    reading = device._redroid_memory(d)
    assert reading is not None and reading["current"] == 2150 * MIB
    assert device.MemoryGuard(d).exceeded() is None  # 70% of the limit, not 90%


def test_scrape_once_starts_and_ends_with_instagram_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "MAX_STORIES_PER_RUN", 0)
    monkeypatch.setattr(config, "MAX_SCROLLS", 1)
    d = feed_device()
    scrape.scrape_once(d, db.db_init())
    stops = [i for i, c in enumerate(d.shell_calls) if c == f"am force-stop {config.IG_PKG}"]
    assert len(stops) == 2
    assert stops[0] == 0 or all(
        c.startswith("am force-stop") for c in d.shell_calls[: stops[0]]
    )  # first thing
    assert stops[1] > d.shell_calls.index("dumpsys package com.instagram.android")  # after the run


def test_scrape_once_still_stops_instagram_when_the_run_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(d: uidevice.Device) -> NoReturn:
        raise device.DeviceNotReady("feed never opened")

    monkeypatch.setattr(navigation, "open_target_feed", boom)
    d = feed_device()
    with pytest.raises(device.DeviceNotReady):
        scrape.scrape_once(d, db.db_init())
    assert d.shell_calls.count(f"am force-stop {config.IG_PKG}") == 2
    for pkg in device.CACHED_APP_SWEEP:
        assert d.shell_calls.count(f"am force-stop {pkg}") == 2


def test_memory_guard_stops_the_run_before_the_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "MEMORY_GUARD_PERCENT", 85)
    monkeypatch.setattr(config, "MAX_STORIES_PER_RUN", 0)
    # start, before stories, first screen: fine; second screen check: 2700 of 3072 MiB is 88%.
    d = with_cgroup(
        feed_device(), [cgroup_output(900), cgroup_output(1200), cgroup_output(1500), cgroup_output(2700)]
    )
    stats = scrape.scrape_once(d, db.db_init())
    assert stats["warning"] is not None
    assert "stopped early: redroid memory at 2700 of 3072 MiB (MEMORY_GUARD_PERCENT=85)" in stats["warning"]
    assert stats.get("mem_peak_mb") == 2700
    assert stats.get("oom_kills") == 0


def test_memory_guard_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "MEMORY_GUARD_PERCENT", 0)
    monkeypatch.setattr(config, "MAX_STORIES_PER_RUN", 0)
    monkeypatch.setattr(config, "MAX_SCROLLS", 1)
    stats = scrape.scrape_once(with_cgroup(feed_device(), [cgroup_output(3000)]), db.db_init())
    assert not (stats["warning"] or "").startswith("stopped early")
    assert stats.get("mem_peak_mb") == 3000  # still measured


def test_memory_guard_skips_stories_when_already_over(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "MAX_SCROLLS", 1)
    d = with_cgroup(feed_device(), [cgroup_output(2900)])
    stats = scrape.scrape_once(d, db.db_init())
    assert stats["new_stories"] == 0
    assert stats["warning"] is not None
    assert "skipped stories: redroid memory at 2900 of 3072 MiB" in stats["warning"]


_NOISE = Image.effect_noise((WIDTH // 8, HEIGHT // 8), 80)  # random, so generated once


def _story_frame(seed: int, overlay: bool = False) -> Image.Image:
    """A photo-like screenshot (noise), optionally with a bar drawn over its lower part, the way a
    tooltip or reply box can differ between two captures of the same story."""
    img = ImageOps.fit(_NOISE, (WIDTH, HEIGHT)).convert("RGB")
    if seed:
        img = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if overlay:
        ImageDraw.Draw(img).rectangle((100, HEIGHT - 400, WIDTH - 100, HEIGHT - 300), fill="white")
    return img


def test_a_recaptured_story_is_not_stored_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    con = db.db_init()
    frame = _story_frame(0)
    d = feed_device(start="home")
    d.screenshot = lambda: frame
    assert stories.scrape_stories(d, con) == 1

    d = feed_device(start="home")  # same unseen story next run, captured with an overlay on top
    d.screenshot = lambda: _story_frame(0, overlay=True)
    assert stories.scrape_stories(d, con) == 0

    d = feed_device(start="home")  # a different frame from the same account is still new
    d.screenshot = lambda: _story_frame(1)
    assert stories.scrape_stories(d, con) == 1
    assert scalar(con, "SELECT COUNT(*) FROM stories") == 2
    assert len(list((config.MEDIA_DIR / "stories").iterdir())) == 2  # discarded crops removed


def test_a_blank_story_frame_is_discarded(monkeypatch: pytest.MonkeyPatch) -> None:
    con = db.db_init()
    d = feed_device(start="home")
    d.screenshot = lambda: Image.new("RGB", (WIDTH, HEIGHT), (2, 2, 2))
    assert stories.scrape_stories(d, con) == 0
    assert scalar(con, "SELECT COUNT(*) FROM stories") == 0


def test_oom_kills_during_a_run_are_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "MAX_STORIES_PER_RUN", 0)
    monkeypatch.setattr(config, "MAX_SCROLLS", 1)
    d = with_cgroup(feed_device(), [cgroup_output(900, oom_kill=7), cgroup_output(1000, oom_kill=9)])
    stats = scrape.scrape_once(d, db.db_init())
    assert stats.get("oom_kills") == 2
    assert stats["warning"] is not None
    assert "redroid OOM-killed 2 Android process(es)" in stats["warning"]


def test_main_records_memory_stats(fast_offline: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stop_after_first_sleep(monkeypatch)
    monkeypatch.setattr(device, "connect_device", feed_device)
    stats: dict[str, int | None] = {
        "new": 0,
        "new_stories": 0,
        "warning": None,
        "mem_peak_mb": 1843,
        "oom_kills": 1,
    }

    def fake_scrape_once(d: uidevice.Device, con: sqlite3.Connection) -> dict[str, int | None]:
        return stats

    monkeypatch.setattr(scrape, "scrape_once", fake_scrape_once)
    with pytest.raises(StopLoop):
        scrape.main()
    stored = values(sqlite3.connect(fast_offline / "posts.sqlite"), "SELECT mem_peak_mb, oom_kills FROM runs")
    assert stored[0] == (1843, 1)


# --- media format ---------------------------------------------------------------------------------


def test_jpeg_media_format_still_writes_jpegs(fast_offline: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from PIL import Image

    monkeypatch.setattr(config, "MEDIA_FORMAT", "jpeg")
    path = stories.capture_story_media(Image.new("RGB", (200, 400), "red"), "[0,0][200,400]", 0, "s")
    assert path is not None
    assert path.name == "s.jpg" and path.read_bytes()[:3] == b"\xff\xd8\xff"


def test_recapturing_an_avatar_in_a_new_format_drops_the_old_file(
    fast_offline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    avatars = config.MEDIA_DIR / "avatars"
    avatars.mkdir(parents=True)
    (avatars / "old_user.jpg").write_bytes(b"old jpeg")  # captured before switching MEDIA_FORMAT
    d = feed_device(start="older")
    header = parsing.parse_hierarchy(d.screens["older"])[0]["header_bounds"]
    assert header is not None
    assert capture.capture_avatar(d, header, "old_user") == "avatars/old_user.webp"
    assert sorted(f.name for f in avatars.iterdir()) == ["old_user.webp"]


# --- startup wait ---------------------------------------------------------------------------------


def _record_last_run(con: sqlite3.Connection, minutes_ago: float, error: str | None = None) -> None:
    finished = (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat()
    db.record_run(con, finished, finished, 0, error, {})


@pytest.fixture
def poll_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "POLL_MIN_H", 2.5)
    monkeypatch.setattr(config, "POLL_MAX_H", 4.5)
    monkeypatch.setattr(config, "TIME_DISTRIBUTION", "uniform")
    monkeypatch.setattr(config, "RETRY_DELAYS_MINUTES", [2.0, 5.0])
    monkeypatch.setattr(config, "SCRAPE_ON_STARTUP", False)


def test_startup_scrapes_immediately_with_no_recorded_run(poll_window: None) -> None:
    assert scrape._startup_wait_seconds(db.db_init()) == 0


def test_startup_waits_out_the_rest_of_the_poll_interval(poll_window: None) -> None:
    con = db.db_init()
    _record_last_run(con, minutes_ago=60)
    wait = scrape._startup_wait_seconds(con)
    assert 1.5 * 3600 - 5 <= wait <= 3.5 * 3600


def test_startup_does_not_wait_when_the_last_run_is_old(poll_window: None) -> None:
    con = db.db_init()
    _record_last_run(con, minutes_ago=5 * 60)
    assert scrape._startup_wait_seconds(con) == 0


@pytest.mark.parametrize(
    "error",
    [
        "DeviceNotReady('redroid still booting')",
        "LaunchUiAutomationError('server quit')",
        "AdbError('offline')",
    ],
)
def test_startup_after_a_transient_failure_waits_only_for_the_first_retry(
    poll_window: None, error: str
) -> None:
    con = db.db_init()
    _record_last_run(con, minutes_ago=0.5, error=error)
    assert 85 <= scrape._startup_wait_seconds(con) <= 90  # 2 minutes, minus the 30s already passed


def test_startup_after_a_non_transient_failure_waits_a_full_interval(poll_window: None) -> None:
    con = db.db_init()
    _record_last_run(
        con, minutes_ago=1, error="RuntimeError(\"Instagram wants a human: 'Confirm it's you'\")"
    )
    assert scrape._startup_wait_seconds(con) >= 2.5 * 3600 - 65


def test_scrape_on_startup_skips_the_wait(poll_window: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "SCRAPE_ON_STARTUP", True)
    con = db.db_init()
    _record_last_run(con, minutes_ago=1)
    assert scrape._startup_wait_seconds(con) == 0


def test_main_waits_before_its_first_scrape(fast_offline: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps = _stop_after_first_sleep(monkeypatch)

    def fixed_wait(con: sqlite3.Connection, now: datetime | None = None) -> float:
        return 123.0

    monkeypatch.setattr(scrape, "_startup_wait_seconds", fixed_wait)
    connects: list[int] = []
    monkeypatch.setattr(device, "connect_device", lambda: connects.append(1))
    with pytest.raises(StopLoop):
        scrape.main()
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


def test_filter_logcat_keeps_errors_fatals_and_known_signatures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "LOGCAT_TAIL_LINES", 2000)
    kept = diagnostics._filter_logcat(LOGCAT)
    assert [line.split(": ", 1)[0].split()[-1] for line in kept] == [
        "AndroidRuntime",
        "libc",
        "lowmemorykiller",
        "Watchdog",
    ]
    monkeypatch.setattr(config, "LOGCAT_TAIL_LINES", 2)
    assert len(diagnostics._filter_logcat(LOGCAT)) == 2  # the tail, not the head
    assert "WATCHDOG KILLING" in diagnostics._filter_logcat(LOGCAT)[-1]


def test_save_failure_logcat_writes_a_filtered_file(
    fast_offline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Unpack[RunOptions]) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=LOGCAT, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    path = SAVE_FAILURE_LOGCAT("DeviceNotReady('could not bring com.instagram.android to the foreground')")
    assert path is not None
    assert calls == [["adb", "-s", config.ADB_ADDR, "logcat", "-d", "-v", "threadtime"]]
    assert path.parent == config.DEBUG_DIR and path.name.startswith("logcat_") and path.suffix == ".txt"
    text = path.read_text()
    assert text.startswith("# run failed: DeviceNotReady('could not bring")
    assert "FATAL EXCEPTION" in text and "Something: chatter" not in text


def test_save_failure_logcat_tolerates_an_unreachable_device(
    fast_offline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    def offline_run(cmd: list[str], **kwargs: Unpack[RunOptions]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="error: device offline")

    monkeypatch.setattr(subprocess, "run", offline_run)
    assert SAVE_FAILURE_LOGCAT("AdbError('offline')") is None
    assert not list(config.DEBUG_DIR.glob("logcat_*")) if config.DEBUG_DIR.exists() else True


def test_main_saves_a_logcat_only_for_device_failures(
    fast_offline: Path, monkeypatch: pytest.MonkeyPatch, no_real_logcat: list[str]
) -> None:
    def run_main_once(error: BaseException) -> None:
        _stop_after_first_sleep(monkeypatch)

        def failing() -> NoReturn:
            raise error

        monkeypatch.setattr(device, "connect_device", failing)
        with pytest.raises(StopLoop):
            scrape.main()

    run_main_once(adbutils.AdbError("device 127.0.0.1:5555 not online"))
    assert len(no_real_logcat) == 1 and no_real_logcat[0].startswith("AdbError")
    run_main_once(RuntimeError("Instagram wants a human"))
    assert len(no_real_logcat) == 1  # a login challenge isn't a device failure


def test_failure_logcats_are_pruned_like_other_debug_files(
    fast_offline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "DEBUG_KEEP", 2)
    config.DEBUG_DIR.mkdir(parents=True)
    for i in range(4):
        f = config.DEBUG_DIR / f"logcat_2026091{i}T000000Z.txt"
        f.write_text("x")
        os.utime(f, (1_800_000_000 + i, 1_800_000_000 + i))
    monkeypatch.setattr(config, "DEBUG_RETAIN_DAYS", 0)
    diagnostics.prune_debug_dumps()
    assert sorted(f.name for f in config.DEBUG_DIR.iterdir()) == [
        "logcat_20260912T000000Z.txt",
        "logcat_20260913T000000Z.txt",
    ]
