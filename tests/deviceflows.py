"""Shared by the device-flow tests: synthetic screens, a FakeDevice wired up as a Home -> Following feed
(feed_device) and typed SQLite reads. Their fixture, fast_offline, is in conftest.py."""

import sqlite3
import time
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import NoReturn, TypedDict, Unpack

import pytest
from igprofiles.v424.selectors import SELECTORS as SELECTORS_445
from instadroid import (
    config,
    diagnostics,
    parsing,
)

from tests.fakedevice import FakeDevice, Node, hierarchy, node

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


def top_card_id(d: FakeDevice) -> str:
    return parsing.post_id(parsing.parse_hierarchy(d.dump_hierarchy())[0])


def seed_post(
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


class StopLoop(Exception):
    pass


def stop_after_first_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    sleeps: list[float] = []

    def sleep(seconds: float) -> NoReturn:
        sleeps.append(seconds)
        raise StopLoop

    monkeypatch.setattr(time, "sleep", sleep)
    monkeypatch.setattr(config, "TIME_DISTRIBUTION", "uniform")
    monkeypatch.setattr(config, "CONTROL_POLL_SECONDS", 10**9)  # one sleep call per wait, not 30s steps
    return sleeps
