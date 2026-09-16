"""Getting to the target feed (Following or Home) and the Following list, and the allowlist a run filters by."""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from devtools import ROOT
from instadroid import (
    config,
    db,
    navigation,
    parsing,
    scrape,
)
from lxml import etree
from shared import sqlrows
from shared.sqlrows import SqlValue

from tests.deviceflows import (
    ACTION_BAR,
    MENU,
    PROFILE_TAB,
    feed_cards,
    feed_device,
    feed_device_with_following,
    following_screen,
    home_screen,
    profile_screen,
    seed_post,
)
from tests.fakedevice import FakeDevice, FakeSelector, Node, hierarchy, node
from tests.support import sql_column

pytestmark = pytest.mark.usefixtures("fast_offline")

# --- feed navigation --------------------------------------------------------------------------


def test_open_following_feed_goes_through_the_switcher() -> None:
    d: FakeDevice = feed_device()
    assert navigation.open_following_feed(d) is True
    assert d.history[-2:] == ["menu", "following"]


def test_open_following_feed_reenters_a_following_screen_left_over_from_last_run() -> None:
    d: FakeDevice = feed_device(start="following")
    assert navigation.open_following_feed(d) is True
    assert d.history == ["following", "home", "menu", "following"]


def test_open_following_feed_backs_out_of_an_unrelated_screen() -> None:
    d: FakeDevice = feed_device(start="profile")
    d.screens["profile"] = hierarchy(node(text="Edit profile"))
    d.back["profile"] = "home"
    assert navigation.open_following_feed(d) is True
    assert d.presses[0] == "back"


def test_open_following_feed_gives_up_when_the_switcher_never_opens(fast_offline: Path) -> None:
    d: FakeDevice = FakeDevice({"home": home_screen(switcher_goto="")}, "home")
    assert navigation.open_following_feed(d) is False
    assert (fast_offline / "debug" / "feed_switch_hierarchy.xml").exists()


def test_close_sheets_relaunches_if_back_leaves_the_app() -> None:
    d: FakeDevice = feed_device(start="share_top")
    d.back["share_top"] = "launcher"
    assert navigation.close_sheets(d) is True
    assert d.screen == "home"


def test_back_to_feed_relaunches_from_outside_the_app() -> None:
    d: FakeDevice = feed_device(start="launcher", launch_screen="following")
    assert navigation.back_to_feed(d) is True


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
    d: FakeDevice = feed_device(start="following")
    assert navigation.open_home_feed(d) is True
    assert d.screen == "home"


def test_on_target_feed_matches_feed_mode(fast_offline: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home: FakeDevice = FakeDevice({"home": home_feed_screen()}, "home")
    following: FakeDevice = FakeDevice({"following": following_screen()}, "following")
    monkeypatch.setattr(config, "FEED_MODE", "home")
    assert navigation.on_target_feed(home) is True
    assert navigation.on_target_feed(following) is False
    monkeypatch.setattr(config, "FEED_MODE", "chrono")
    assert navigation.on_target_feed(home) is False
    assert navigation.on_target_feed(following) is True


def test_open_target_feed_dispatches_by_feed_mode(
    fast_offline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    d: FakeDevice = feed_device(start="home")
    monkeypatch.setattr(config, "FEED_MODE", "home")
    navigation.open_target_feed(d)
    assert d.screen == "home"
    d = feed_device(start="home")
    monkeypatch.setattr(config, "FEED_MODE", "chrono")
    navigation.open_target_feed(d)
    assert d.screen == "following"


def test_unknown_feed_mode_falls_back_to_chrono() -> None:
    # A subprocess: FEED_MODE is resolved at import, and reloading config in place would leak into
    # every other test in the session.
    result: subprocess.CompletedProcess[str] = subprocess.run(
        [sys.executable, "-c", "from instadroid import config; print(config.FEED_MODE)"],
        cwd=ROOT / "app",
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
    con: sqlite3.Connection = db.db_init()
    # Seed every card already-known so nothing new needs the share-sheet round trip -- this test
    # is about which feed scrape_once() navigates to, not about re-testing that flow.
    for i, card in enumerate(parsing.parse_hierarchy(home_feed_screen())):
        seed_post(con, f"SEEN{i}", card["username"], "already stored", 1, h=parsing.post_id(card))
    d: FakeDevice = FakeDevice(
        {"home": home_feed_screen(), "following": following_screen(), "menu": MENU},
        "home",
        back={"following": "home", "menu": "home"},
    )

    scrape.scrape_once(d, con)

    assert "following" not in d.history  # never navigated to the chronological feed
    assert "menu" not in d.history  # never opened the switcher either


def test_open_own_following_list_navigates_from_the_feed(fast_offline: Path) -> None:
    d: FakeDevice = feed_device_with_following([["alice", "bob"]], start="following")
    assert navigation.open_own_following_list(d) is True
    assert d.screen == "following_list"
    assert d.history[-3:] == ["following", "profile", "following_list"]


def test_open_own_following_list_leaves_and_reenters_when_already_on_the_list_screen(
    fast_offline: Path,
) -> None:
    # A real live run (2026-09-11) found this exact case: a second refresh in the same app session
    # started mid-scroll instead of at the top, collecting 9 of 30 followed accounts instead of a
    # fresh scroll's 27+ — accepting "already there" as done is the bug this guards against.
    d: FakeDevice = feed_device_with_following([["alice", "bob"]], start="following_list")
    assert navigation.open_own_following_list(d) is True
    assert d.screen == "following_list"
    assert d.history.count("following_list") == 2  # left, then genuinely navigated back in


def test_scrape_following_list_scrolls_until_no_new_username_appears(
    fast_offline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "FOLLOWING_LIST_EMPTY_LIMIT", 1)
    d: FakeDevice = feed_device_with_following(
        [["alice", "bob"], ["carol"]], main_scroll={}, start="following_list"
    )
    assert navigation.scrape_following_list(d) == ["alice", "bob", "carol"]


def test_scrape_following_list_returns_none_when_nothing_is_ever_parsed(
    fast_offline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "FOLLOWING_LIST_EMPTY_LIMIT", 1)
    d: FakeDevice = feed_device_with_following([[]], main_scroll={}, start="following_list")
    assert navigation.scrape_following_list(d) is None


def test_refresh_following_list_replaces_the_stored_list(
    fast_offline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "FOLLOWING_LIST_EMPTY_LIMIT", 1)
    con: sqlite3.Connection = db.db_init()
    con.execute("INSERT INTO following (username, updated_at) VALUES ('stale_unfollowed', '2020-01-01')")
    con.commit()
    d: FakeDevice = feed_device_with_following([["alice", "bob"]], main_scroll={}, start="following")

    navigation.refresh_following_list(d, con)

    assert set(sql_column(con.execute("SELECT username FROM following"))) == {"alice", "bob"}


def test_refresh_following_list_keeps_the_existing_list_on_a_failed_scrape(
    fast_offline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "FOLLOWING_LIST_EMPTY_LIMIT", 1)
    con: sqlite3.Connection = db.db_init()
    con.execute("INSERT INTO following (username, updated_at) VALUES ('good_data', '2020-01-01')")
    con.commit()
    d: FakeDevice = feed_device_with_following(
        [[]], main_scroll={}, start="following"
    )  # empty list = parse failure

    navigation.refresh_following_list(d, con)

    assert set(sql_column(con.execute("SELECT username FROM following"))) == {"good_data"}


def test_scrape_once_filters_posts_from_accounts_not_on_the_refreshed_following_list(
    fast_offline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "FOLLOWING_REFRESH_DAYS", 7)
    monkeypatch.setattr(config, "FOLLOWING_LIST_EMPTY_LIMIT", 1)
    monkeypatch.setattr(config, "MAX_STORIES_PER_RUN", 0)
    monkeypatch.setattr(config, "MAX_CAROUSEL_SLIDES", 1)
    monkeypatch.setattr(config, "MAX_SCROLLS", 2)
    con: sqlite3.Connection = db.db_init()  # following table starts empty -> due for a refresh this run
    # Only someone_nice is on the (about-to-be-scraped) Following list; other_user is not.
    d: FakeDevice = feed_device_with_following([["someone_nice"]], main_scroll={})

    stats: scrape.RunStats = scrape.scrape_once(d, con)

    assert set(sql_column(con.execute("SELECT username FROM following"))) == {"someone_nice"}
    assert stats["metrics"].get("filtered_posts", 0) >= 1
    posts: list[SqlValue] = sql_column(con.execute("SELECT username FROM posts"))
    assert "someone_nice" in posts
    assert "other_user" not in posts
    # A filtered post's account is never upserted -- no avatar work, no accounts-table footprint.
    assert sqlrows.fetch_one(con.execute("SELECT 1 FROM accounts WHERE username='other_user'")) is None


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
    con: sqlite3.Connection = db.db_init()
    d: FakeDevice = feed_device_with_following([[]], main_scroll={})  # the refresh itself finds nothing

    stats: scrape.RunStats = scrape.scrape_once(d, con)

    assert stats["metrics"].get("filtered_posts") == 0
    posts: set[SqlValue] = set(sql_column(con.execute("SELECT username FROM posts")))
    assert posts == {"someone_nice", "other_user"}  # nothing dropped


# --- the retry and give-up branches -------------------------------------------------------------


def test_on_target_feed_is_false_when_no_feed_is_showing_at_all(monkeypatch: pytest.MonkeyPatch) -> None:
    d: FakeDevice = FakeDevice({"blank": hierarchy()}, "blank")
    for mode in ("home", "chrono"):
        monkeypatch.setattr(config, "FEED_MODE", mode)
        assert navigation.on_target_feed(d) is False


def test_open_following_feed_retries_when_the_following_entry_leads_nowhere(
    fast_offline: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The menu's Following entry drops back onto the plain Home feed (no Following title) every time:
    # each attempt gives the screen its cold-start grace, logs, and the run finally gives up.
    menu: str = hierarchy(
        node(cls="android.widget.TextView", text="Following", bounds=(0, 1800, 1080, 1900), goto="home")
    )
    d: FakeDevice = FakeDevice({"home": home_screen(), "menu": menu}, "home", back={"menu": "home"})
    assert navigation.open_following_feed(d) is False
    assert capsys.readouterr().out.count("clicked Following but title not found") == 4
    assert (fast_offline / "debug" / "feed_switch_hierarchy.xml").exists()


def test_open_following_feed_retries_after_a_switcher_tap_that_fails_mid_scroll(
    capsys: pytest.CaptureFixture[str],
) -> None:
    class Scrolling(FakeDevice):
        """The header scrolls away between the switcher's exists() and its click(), once."""

        failed: bool = False

        def tap(self, n: etree._Element) -> None:
            if n.get("content-desc") == "Instagram Home Feed" and not self.failed:
                self.failed = True
                raise LookupError("node gone")
            super().tap(n)

    d: Scrolling = Scrolling(
        {"home": home_screen(), "menu": MENU, "following": following_screen()},
        "home",
        back={"menu": "home", "following": "home"},
    )
    assert navigation.open_following_feed(d) is True
    assert "feed switcher click failed" in capsys.readouterr().out
    assert d.history[-2:] == ["menu", "following"]


class FlickeringTabBar(FakeDevice):
    """A Home feed whose bottom tab bar is mid-transition: on_home_feed()'s own check (the id without
    a `$` anchor) finds nothing for the first `hidden` looks, while open_home_feed()'s tab selector
    still sees the tab to tap -- the only way its tap-and-wait branch is reached, since both look for
    the same node. `failing_taps` makes that many Home-tab taps raise, as a node that scrolled away."""

    def __init__(self, hidden: int, failing_taps: int = 0) -> None:
        super().__init__({"home": home_feed_screen()}, "home")
        self.hidden: int = hidden
        self.failing_taps: int = failing_taps

    def __call__(self, **kwargs: str | list[str]) -> FakeSelector:
        if kwargs.get("resourceIdMatches") == ".*:id/feed_tab" and self.hidden > 0:
            self.hidden -= 1
            return FakeSelector(self, {"resourceIdMatches": "never-matches"})
        return super().__call__(**kwargs)

    def tap(self, n: etree._Element) -> None:
        if (n.get("resource-id") or "").endswith("/feed_tab") and self.failing_taps > 0:
            self.failing_taps -= 1
            raise LookupError("tab bar gone")
        super().tap(n)


def test_open_home_feed_taps_the_home_tab_and_waits_for_the_feed(capsys: pytest.CaptureFixture[str]) -> None:
    # Hidden for the first look and the six post-tap looks of attempt 0; attempt 1 then finds it.
    d: FlickeringTabBar = FlickeringTabBar(hidden=7)
    assert navigation.open_home_feed(d) is True
    assert "tapped Home tab but feed not found" in capsys.readouterr().out
    assert len(d.taps) == 0 and d.presses == []  # a selector tap, never a back press
    d = FlickeringTabBar(hidden=3)  # the feed builds during the post-tap wait: done within attempt 0
    assert navigation.open_home_feed(d) is True
    assert "tapped Home tab but feed not found" not in capsys.readouterr().out


def test_open_home_feed_retries_after_a_home_tab_tap_that_fails(capsys: pytest.CaptureFixture[str]) -> None:
    d: FlickeringTabBar = FlickeringTabBar(hidden=1, failing_taps=1)
    assert navigation.open_home_feed(d) is True
    assert "home tab click failed" in capsys.readouterr().out
    assert d.failing_taps == 0


def test_open_home_feed_gives_up_with_a_dump_when_the_feed_never_appears(fast_offline: Path) -> None:
    d: FlickeringTabBar = FlickeringTabBar(hidden=100)
    assert navigation.open_home_feed(d) is False
    assert (fast_offline / "debug" / "home_feed_open_hierarchy.xml").exists()


def test_open_own_following_list_backs_out_of_a_screen_without_the_tab_bar(fast_offline: Path) -> None:
    d: FakeDevice = feed_device_with_following([["alice"]], start="menu")  # the switcher menu: no tabs
    assert navigation.open_own_following_list(d) is True
    assert d.presses[0] == "back"
    assert d.history[-3:] == ["home", "profile", "following_list"]


def test_open_own_following_list_gives_up_when_the_profile_has_no_following_link(
    fast_offline: Path,
) -> None:
    d: FakeDevice = feed_device_with_following([["alice"]], start="following")
    d.screens["profile"] = hierarchy(ACTION_BAR)  # profile loaded without its counters
    assert navigation.open_own_following_list(d) is False
    assert (fast_offline / "debug" / "following_list_profile0_hierarchy.xml").exists()
    assert (fast_offline / "debug" / "following_list_open_hierarchy.xml").exists()
    assert d.history.count("profile") == 4  # tried every attempt, backing out of the profile each time


def test_open_own_following_list_gives_up_when_the_link_tap_opens_nothing(
    fast_offline: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    d: FakeDevice = feed_device_with_following([["alice"]], start="following")
    d.screens["profile"] = profile_screen(following_goto="")  # the tap is swallowed
    assert navigation.open_own_following_list(d) is False
    assert "list screen not detected after tap" in capsys.readouterr().out
    assert "following_list" not in d.history


def test_open_own_following_list_retries_after_a_profile_tab_tap_that_fails(
    fast_offline: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    class Shifting(FakeDevice):
        failed: bool = False

        def tap(self, n: etree._Element) -> None:
            if n.get("content-desc") == "Profile" and not self.failed:
                self.failed = True
                raise LookupError("node gone")
            super().tap(n)

    base: FakeDevice = feed_device_with_following([["alice"]], start="following")
    d: Shifting = Shifting(base.screens, "following", back=base.back, scroll=base.scroll)
    assert navigation.open_own_following_list(d) is True
    assert "following-list navigation click failed" in capsys.readouterr().out
    assert d.screen == "following_list"


def test_refresh_following_list_leaves_the_stored_list_alone_when_navigation_fails(
    fast_offline: Path,
) -> None:
    con: sqlite3.Connection = db.db_init()
    con.execute("INSERT INTO following (username, updated_at) VALUES ('good_data', '2020-01-01')")
    con.commit()
    d: FakeDevice = feed_device_with_following([["alice"]], start="following")
    d.screens["profile"] = hierarchy(ACTION_BAR)  # never reaches the list

    navigation.refresh_following_list(d, con)

    assert set(sql_column(con.execute("SELECT username FROM following"))) == {"good_data"}
    assert "following_list" not in d.history


@pytest.mark.parametrize(
    "marker",
    [node(cls="android.widget.TextView", text="Write a message…", bounds=(0, 2240, 1080, 2330)),
     node(desc="New group", bounds=(0, 2240, 1080, 2330))],
)  # fmt: skip
def test_close_sheets_recognises_a_share_sheet_by_its_marker_text_or_description(marker: Node) -> None:
    d: FakeDevice = FakeDevice(
        {"sheet": following_screen(sheet=marker), "following": following_screen()},
        "sheet",
        back={"sheet": "following"},
    )
    assert navigation.close_sheets(d) is True
    assert d.presses == ["back"] and d.screen == "following"


def test_back_to_feed_backs_out_of_a_profile_opened_by_a_tap() -> None:
    screens: dict[str, str] = {
        "profile": hierarchy(node(text="Edit profile")),
        "following": following_screen(),
    }
    d: FakeDevice = FakeDevice(screens, "profile", back={"profile": "following"})
    assert navigation.back_to_feed(d) is True
    assert d.presses == ["back"] and d.screen == "following"
    d = FakeDevice(screens, "profile", back={"profile": "following"})
    assert navigation.back_to_feed(d, tries=1) is True  # the last back is trusted without a re-check
    assert d.presses == ["back"]
