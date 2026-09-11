"""End-to-end-ish tests of the device-driving code (login, feed navigation, share sheet, carousels,
stories, the scrape loop, main()) against tests.fakedevice.FakeDevice. Screens are synthetic."""

import sqlite3
from datetime import UTC, datetime, timedelta

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


def _caption_card(text, goto=None):
    return [
        node("row_feed_profile_header", desc="someone_nice posted a photo 3 days ago", bounds=(0, 150, 1080, 289)),
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
        {"following": following_screen(_caption_card("someone_nice Short start… more"))},  # no goto: tap is inert
        "following",
    )
    p = scraper.parse_hierarchy(d.dump_hierarchy())[0]

    assert scraper._expand_caption(d, p) == "Short start…"
    assert d.screen == "following"  # never knocked off the feed


def test_expand_caption_is_a_noop_for_a_caption_that_was_never_truncated():
    d = FakeDevice({"following": following_screen(_caption_card("someone_nice Whole caption, no more span"))}, "following")
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
