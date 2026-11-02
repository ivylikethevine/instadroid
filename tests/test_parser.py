import random
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from igprofiles.screens import id_matches
from instadroid import capture, common, config, device, parsing, stories, versioning
from PIL import Image, ImageFile

from tests.fakedevice import hierarchy, node
from tests.support import FEED_445

FIXTURE: str = FEED_445.read_text()


def test_parse_hierarchy_finds_both_cards_and_skips_sponsored() -> None:
    posts: list[parsing.Post] = parsing.parse_hierarchy(FIXTURE)
    assert [p["username"] for p in posts] == ["someone_nice", "other_user"]


def test_headless_top_card_is_identified_from_caption_and_media() -> None:
    top: parsing.Post = parsing.parse_hierarchy(FIXTURE)[0]
    assert top["headless"] is True
    assert top["kind"] == "video"  # "Reel by ..." maps to video
    assert top["caption"] == "Top card caption…"
    assert top["posted_date"] == "3 days ago"
    assert top["complete"] is True
    assert top["share_bounds"] == "[390,900][453,1021]"
    assert top["clip_top"] == 289


def test_full_card_fields() -> None:
    card: parsing.Post = parsing.parse_hierarchy(FIXTURE)[1]
    assert card["kind"] == "carousel"
    assert card["place"] == "Anytown, Somewhere"
    assert card["posted_date"] == "21 hours ago"
    assert card["bounds"] == "[0,1287][1080,2000]"
    assert card["alt"].startswith("Photo 1 of 7")
    assert card["caption"] == "Second caption"
    assert card["complete"] is True
    assert card["header_bounds"] == "[0,1150][1080,1287]"
    assert card["caption_truncated"] is False  # no trailing "more" in the raw text


def test_truncated_caption_is_flagged_with_its_bounds() -> None:
    top: parsing.Post = parsing.parse_hierarchy(FIXTURE)[0]
    assert top["caption_truncated"] is True  # raw text ended in "… more"
    assert top["caption_bounds"] == "[32,1030][1080,1100]"


def test_headless_top_card_has_no_header_bounds() -> None:
    # Its header already scrolled off before this dump; the avatar can't be captured from it.
    top: parsing.Post = parsing.parse_hierarchy(FIXTURE)[0]
    assert top["header_bounds"] is None


def test_card_without_caption_or_alt_is_excluded() -> None:
    # A header with neither a caption nor a media description can't be identified (post_id()
    # would hash nothing but the username); it should be left out rather than stored empty.
    xml: str = """<hierarchy><node><node resource-id="android:id/list">
      <node resource-id="com.instagram.android:id/row_feed_profile_header"
            content-desc="ghostuser posted a photo 2 hours ago" />
      <node resource-id="com.instagram.android:id/row_feed_button_share" bounds="[0,0][1,1]" />
      <node text="2 hours ago" />
    </node></node></hierarchy>"""
    assert parsing.parse_hierarchy(xml) == []


def test_resource_ids_match_on_a_whole_id_segment_or_the_full_id() -> None:
    assert id_matches("com.instagram.android:id/row_feed_button_share", "row_feed_button_share")
    assert id_matches("android:id/list", "android:id/list")  # a selector holding a full id
    assert not id_matches("com.instagram.android:id/big_row_feed_button_share", "row_feed_button_share")
    assert not id_matches("com.instagram.android:id/list", "android:id/list")


# A Reel tagged with collaborators ("<user> and N others"), modeled on a real dump captured live
# (2026-09-11): its media node (carrying the "Reel by ..." alt) renders *before* its own header —
# every other card layout has the header first — and it has no separate caption or timestamp node
# at all, only the header's own content-desc.
REEL_COLLAB_FIXTURE: str = """<hierarchy><node><node resource-id="android:id/list">
  <node resource-id="com.instagram.android:id/media_group" bounds="[0,210][1080,2093]">
    <node resource-id="com.instagram.android:id/row_feed_photo_imageview"
          content-desc="Reel by Someone, Liked by a_friend and others, 2 comments, 57 minutes ago"
          bounds="[0,210][1080,2093]" />
  </node>
  <node resource-id="com.instagram.android:id/row_feed_profile_header"
        content-desc="showcase.live posted a video in The Venue Downtown 57 minutes ago"
        bounds="[0,210][1080,347]" />
  <node resource-id="com.instagram.android:id/row_feed_photo_profile_name"
        text="showcase.live and 3 others" />
  <node resource-id="com.instagram.android:id/row_feed_button_share" bounds="[420,2093][483,2214]" />
  <node resource-id="com.instagram.android:id/row_feed_button_like" />
</node></node></hierarchy>"""


def test_reel_with_media_before_header_is_identified_not_split_in_two() -> None:
    posts: list[parsing.Post] = parsing.parse_hierarchy(REEL_COLLAB_FIXTURE)
    assert len(posts) == 1  # not two dead-end entries that both fail the final filter
    p: parsing.Post = posts[0]
    assert p["username"] == "showcase.live"
    assert p["kind"] == "video"
    assert p["place"] == "The Venue Downtown"
    assert p["posted_date"] == "57 minutes ago"
    assert p["alt"].startswith("Reel by")
    assert p["headless"] is False
    assert p["header_bounds"] == "[0,210][1080,347]"


def test_reel_with_media_before_header_is_complete_once_share_button_seen() -> None:
    # This layout has no caption/timestamp node to wait for -- the share button is the bottom of
    # the card. Without this, the post would never pass scrape_once()'s `if not p["complete"]`
    # gate and would never actually get stored.
    p: parsing.Post = parsing.parse_hierarchy(REEL_COLLAB_FIXTURE)[0]
    assert p["complete"] is True
    assert p["share_bounds"] == "[420,2093][483,2214]"
    assert p["bounds"] == "[0,210][1080,2093]"


def test_a_genuinely_different_off_screen_card_is_not_merged_into_the_next_header() -> None:
    # Control case: an ordinary (non-Reel) card whose header has scrolled off is identified from
    # its own caption before any later header appears -- confirming the merge fix only fires for
    # the narrow headless+no-username+no-caption+no-share_bounds+Reel-alt case, not generally.
    xml: str = f"""<hierarchy><node><node resource-id="android:id/list">
      <node class="{versioning.SELECTORS["caption_class"]}" text="old_user Old caption" />
      <node resource-id="com.instagram.android:id/row_feed_profile_header"
            content-desc="new_user posted a photo 1 hour ago" bounds="[0,900][1080,1030]" />
      <node class="{versioning.SELECTORS["caption_class"]}" text="new_user New caption" />
      <node resource-id="com.instagram.android:id/row_feed_button_share" bounds="[0,0][1,1]" />
      <node text="1 hour ago" />
    </node></node></hierarchy>"""
    posts: list[parsing.Post] = parsing.parse_hierarchy(xml)
    assert [p["username"] for p in posts] == ["old_user", "new_user"]


def test_post_id_is_the_same_truncated_and_expanded() -> None:
    """The bug from the 446 validation run: a card hashed once truncated and again after its caption
    was expanded, so the same post was processed twice in one run."""
    truncated: parsing.Post = parsing._new_post("u", "photo", "", "", 0)
    truncated["caption"], truncated["caption_truncated"] = (
        "A long first line that Instagram cuts off after two…",
        True,
    )
    expanded: parsing.Post = truncated.copy()
    expanded["caption"] = (
        "A long first line that Instagram cuts off after two lines, and then goes on for a while\nSecond line"
    )
    assert parsing.post_id(truncated) == parsing.post_id(expanded)
    # An early line break: only the first line is shown before "… more", and only it counts.
    short_first: parsing.Post = truncated.copy()
    short_first["caption"] = "Hi…"
    full: parsing.Post = truncated.copy()
    full["caption"] = "Hi\n\nMuch more text below the fold"
    assert parsing.post_id(short_first) == parsing.post_id(full)
    other: parsing.Post = truncated.copy()
    other["caption"] = "A different first line altogether"
    assert parsing.post_id(other) != parsing.post_id(truncated)
    assert parsing.caption_key("  Hello   world\nnext  ") == "Hello world"


def test_post_id_ignores_counts_dates_and_kind() -> None:
    base: parsing.Post = parsing._new_post("u", "photo", "", "", 0)
    base["alt"] = "Photo 1 of 3 by U, 5 likes, 2 comments"
    later: parsing.Post = base.copy()
    later["kind"], later["alt"] = "carousel", "Photo 2 of 3 by U, 9 likes, 4 comments"
    captioned: parsing.Post = base.copy()
    captioned["caption"] = "hello"
    assert parsing.post_id(base) == parsing.post_id(later)
    assert parsing.post_id(captioned) != parsing.post_id(base)


@pytest.mark.parametrize(
    ("desc", "expected"),
    [
        (
            "artist posted a video in Pat's Gallery at #1 Main St August 29",
            ("artist", "video", "Pat's Gallery at #1 Main St", "August 29"),
        ),
        ("some.one posted a photo 6 days ago", ("some.one", "photo", None, "6 days ago")),
        (
            "club posted a carousel in Anytown, Somewhere 3 days ago",
            ("club", "carousel", "Anytown, Somewhere", "3 days ago"),
        ),
        ("Sponsored", None),
    ],
)
def test_header_regex(desc: str, expected: tuple[str, str, str | None, str] | None) -> None:
    m: re.Match[str] | None = versioning.SELECTORS["header_desc"].match(desc)
    if expected is None:
        assert m is None
    else:
        assert m is not None
        assert (m.group("user"), m.group("kind"), m.group("place"), m.group("date")) == expected


@pytest.mark.parametrize(
    ("raw", "cleaned"),
    [
        ("user Hello there… more", "Hello there…"),
        ("user Short", "Short"),
        ("user Hello there", "Hello there"),
    ],
    ids=["more", "short", "nbsp"],
)
def test_clean_caption_strips_user_more_and_nbsp(raw: str, cleaned: str) -> None:
    assert parsing.clean_caption(raw, "user") == cleaned


def test_permalink_regex_accepts_reel_and_p_with_tracking_params() -> None:
    rx: re.Pattern[str] = versioning.SELECTORS["permalink"]
    reel: re.Match[str] | None = rx.match("https://www.instagram.com/reel/AbCdEf12345/?stkn=abc")
    post: re.Match[str] | None = rx.match("https://www.instagram.com/p/ZyXwVu98765/")
    assert reel is not None and reel.group("code") == "AbCdEf12345"
    assert post is not None and post.group("type") == "p"
    assert rx.match("https://www.instagram.com/someone/") is None


NOW: datetime = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("text", "expected", "precision"),
    [
        ("30 minutes ago", datetime(2026, 9, 8, 11, 30, tzinfo=UTC), 60),
        ("3 hours ago", datetime(2026, 9, 8, 9, 0, tzinfo=UTC), 3600),
        ("2 days ago", datetime(2026, 9, 6, 12, 0, tzinfo=UTC), 86400),
        ("Yesterday", datetime(2026, 9, 7, 12, 0, tzinfo=UTC), 86400),
        ("September 1", datetime(2026, 9, 1, 0, 0, tzinfo=UTC), 86400),
        ("August 29, 2024", datetime(2024, 8, 29, 0, 0, tzinfo=UTC), 86400),
    ],
)
def test_parse_posted_at(text: str, expected: datetime, precision: int) -> None:
    assert parsing.parse_posted_at(text, NOW) == (expected, precision)


def test_parse_posted_at_bare_date_rolls_back_a_year_if_in_the_future() -> None:
    # "now" is Sep 8; a bare "December 25" with no year must mean last December, not next.
    parsed: tuple[datetime, int] | None = parsing.parse_posted_at("December 25", NOW)
    assert parsed is not None
    dt: datetime
    _: int
    dt, _ = parsed
    assert dt.year == 2025


def test_parse_posted_at_reads_a_bare_date_as_a_day_on_the_device() -> None:
    tz: ZoneInfo = ZoneInfo("America/Los_Angeles")
    assert parsing.parse_posted_at("September 1", NOW, tz) == (datetime(2026, 9, 1, 7, 0, tzinfo=UTC), 86400)
    assert parsing.parse_posted_at("3 hours ago", NOW, tz) == (datetime(2026, 9, 8, 9, 0, tzinfo=UTC), 3600)
    # 03:00 UTC on January 1 is still December 31 there: "December 31" was today, not a year ago.
    new_year: datetime = datetime(2027, 1, 1, 3, 0, tzinfo=UTC)
    assert parsing.parse_posted_at("December 31", new_year, tz) == (
        datetime(2026, 12, 31, 8, 0, tzinfo=UTC),
        86400,
    )


@pytest.mark.parametrize("text", ["", "sometime", "Someday 5", "February 30", "April 31, 2024"])
def test_parse_posted_at_rejects_unknown_formats_and_impossible_dates(text: str) -> None:
    assert parsing.parse_posted_at(text, NOW) is None


def test_parse_posted_at_leap_day_rollback_into_non_leap_year_does_not_raise() -> None:
    # "now" is a leap year, before Feb 29 has passed: rolling a bare "February 29" back a year
    # lands on a non-leap year, where Feb 29 doesn't exist. Must return None, not raise.
    leap_year_now: datetime = datetime(2028, 1, 15, tzinfo=UTC)
    assert parsing.parse_posted_at("February 29", leap_year_now) is None


def test_same_post_merges_a_weak_caption_placeholder_into_the_real_row() -> None:
    real: parsing.PostIdentity = {
        "username": "club",
        "caption": "Attendance check! see you there",
        "posted_at": NOW,
    }
    weak: parsing.PostIdentity = {
        "username": "club",
        "caption": "Photo 1 of 2 by Club, 113 likes, 10 comments",
        "posted_at": NOW,
        "posted_at_precision": 86400,
    }
    assert parsing.same_post(real, weak) is True  # weak candidate merges into the real row
    real_precise: parsing.PostIdentity = {**real, "posted_at_precision": 86400}
    assert parsing.same_post(weak, real_precise) is True  # or vice versa


@pytest.mark.parametrize(
    ("a", "b"),
    [
        (
            {"username": "club", "caption": "First post of the day", "posted_at": NOW},
            {
                "username": "club",
                "caption": "Second, unrelated post",
                "posted_at": NOW,
                "posted_at_precision": 86400,
            },
        ),
        (  # 3h apart, 60s-precision candidate
            {"username": "u", "caption": "", "posted_at": NOW},
            {
                "username": "u",
                "caption": "",
                "posted_at": NOW - timedelta(hours=3),
                "posted_at_precision": 60,
            },
        ),
        (
            {"username": "alice", "caption": "", "posted_at": NOW},
            {"username": "bob", "caption": "", "posted_at": NOW, "posted_at_precision": 60},
        ),
        (
            {"username": "u", "caption": "", "posted_at": NOW},
            {"username": "u", "caption": "", "posted_at": None},
        ),
        (
            {"username": "u", "caption": "", "posted_at": None},
            {"username": "u", "caption": "", "posted_at": NOW},
        ),
    ],
    ids=[
        "two-real-captions-same-day",
        "outside-the-time-tolerance",
        "different-usernames",
        "candidate-undated",
        "existing-undated",
    ],
)
def test_same_post_refuses(a: parsing.PostIdentity, b: parsing.PostIdentity) -> None:
    assert parsing.same_post(a, b) is False


STORY_TRAY_FIXTURE: str = """<hierarchy><node><node resource-id="com.instagram.android:id/reels_tray_container"
  class="androidx.recyclerview.widget.RecyclerView">
    <node class="android.widget.LinearLayout">
      <node class="android.widget.Button" content-desc="myself's story, 0 of 3, Unseen."
            bounds="[0,210][294,555]" />
    </node>
    <node class="android.widget.LinearLayout">
      <node class="android.widget.Button" content-desc="alice's story, 1 of 3, Unseen."
            bounds="[294,210][588,555]">
        <node class="android.widget.ImageView" content-desc="alice's story, 1 of 3, Unseen."
              bounds="[329,245][552,468]" />
      </node>
    </node>
    <node class="android.widget.LinearLayout">
      <node class="android.widget.Button" content-desc="bob's story, 2 of 3, Seen."
            bounds="[588,210][882,555]" />
    </node>
</node></node></hierarchy>"""


def test_parse_story_tray_skips_own_story_and_dedupes_the_nested_image() -> None:
    items: list[parsing.StoryItem] = parsing.parse_story_tray(STORY_TRAY_FIXTURE)
    assert [i["username"] for i in items] == ["alice", "bob"]


# Modeled on a real Following-list screen dump (own account, 2026-09-11): "Categories" suggestion
# cards (own resource-ids: title/subtitle, no follow_list_username) sit above the real rows, and a
# "Sorted by ..." header between them — both must never be mistaken for a followed account.
FOLLOWING_LIST_FIXTURE: str = """<hierarchy><node><node resource-id="com.instagram.android:id/frame_header">
    <node resource-id="com.instagram.android:id/row_header_textview" text="Categories" />
  </node>
  <node resource-id="com.instagram.android:id/container" content-desc="Least interacted with">
    <node resource-id="com.instagram.android:id/title" text="Least interacted with" />
    <node resource-id="com.instagram.android:id/subtitle" text="another.artist and 6 others" />
  </node>
  <node resource-id="com.instagram.android:id/sorting_entry_row_option" text="Sorted by Default" />
  <node resource-id="com.instagram.android:id/follow_list_container">
    <node resource-id="com.instagram.android:id/follow_list_username" text="some.artist" />
    <node resource-id="com.instagram.android:id/follow_list_subtitle" text="Some Artist" />
  </node>
  <node resource-id="com.instagram.android:id/follow_list_container">
    <node resource-id="com.instagram.android:id/follow_list_username" text="night_owl_" />
  </node>
</node></hierarchy>"""


def test_parse_following_list_finds_rows_and_skips_categories_and_sort_header() -> None:
    assert parsing.parse_following_list(FOLLOWING_LIST_FIXTURE) == ["some.artist", "night_owl_"]


def test_parse_following_list_returns_empty_for_a_screen_with_no_rows() -> None:
    assert parsing.parse_following_list("<hierarchy><node/></hierarchy>") == []


def test_parse_story_tray_reports_seen_state() -> None:
    items: list[parsing.StoryItem] = parsing.parse_story_tray(STORY_TRAY_FIXTURE)
    by_user: dict[str, parsing.StoryItem] = {i["username"]: i for i in items}
    assert by_user["alice"]["seen"] is False
    assert by_user["bob"]["seen"] is True
    assert by_user["alice"]["bounds"] == "[294,210][588,555]"


def test_parse_story_tray_empty_when_tray_not_on_screen() -> None:
    assert parsing.parse_story_tray("<hierarchy><node /></hierarchy>") == []


@pytest.fixture
def story_frame(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Image.Image:
    """A frame to capture, with MEDIA_DIR at tmp_path."""
    monkeypatch.setattr(config, "MEDIA_DIR", tmp_path)
    return Image.new("RGB", (200, 400), "red")


@pytest.mark.parametrize(
    ("bounds", "clip_top"),
    [
        ("not-bounds", 0),
        (None, 0),
        ("[0,0][100,150]", 0),
        ("[0,0][200,400]", 250),  # 150 rows of body
    ],
    ids=["malformed-bounds", "no-bounds", "short-bounds", "clip-leaves-too-little-height"],
)
def test_capture_story_media_returns_none_without_a_usable_body(
    story_frame: Image.Image, bounds: str | None, clip_top: int
) -> None:
    assert stories.capture_story_media(story_frame, bounds, clip_top, "tmp") is None


def test_capture_story_media_saves_the_whole_frame_under_a_stories_subdirectory(
    tmp_path: Path, story_frame: Image.Image
) -> None:
    saved: tuple[Path, int] | None = stories.capture_story_media(
        story_frame, "[0,20][200,400]", 70, "tmpstory"
    )

    assert saved == (tmp_path / "stories" / "tmpstory.webp", 50)  # the header overlay covers 50 rows
    stored: ImageFile.ImageFile
    with Image.open(tmp_path / "stories" / "tmpstory.webp") as stored:
        assert stored.size == (200, 380)  # nothing above the body is cut off


@pytest.mark.parametrize(
    ("alt", "count"),
    [
        ("Photo 1 of 7 by Other User, 317 likes, 10 comments", 7),
        ("Video 3 of 3 by X", 3),
        ("Reel by Someone Nice, Liked by a_friend and others", 1),  # not a carousel
        ("", 1),
    ],
)
def test_carousel_count_is_the_slide_total_or_one(alt: str, count: int) -> None:
    assert parsing.carousel_count(alt) == count


@pytest.mark.parametrize(
    ("name", "safe"),
    [
        ("some.user_92", "some.user_92"),
        ("../etc/passwd", None),
        ("..", None),
        (".", None),
        ("", None),
        ("has space", None),
        ("has/slash", None),
    ],
)
def test_safe_filename_accepts_plausible_handles_and_rejects_unsafe_input(
    name: str, safe: str | None
) -> None:
    assert common.safe_filename(name) == safe


def test_avatar_bounds_crops_a_square_inside_the_header() -> None:
    box: tuple[int, int, int, int] | None = capture._avatar_bounds("[0,1150][1080,1287]")
    assert box is not None
    x1: int
    y1: int
    x2: int
    y2: int
    x1, y1, x2, y2 = box
    assert 0 < x1 < x2 <= 1080
    assert 1150 < y1 < y2 <= 1287
    assert (x2 - x1) == (y2 - y1)  # square crop


@pytest.mark.parametrize("bad", ["", "not-bounds"])
def test_avatar_bounds_returns_none_for_missing_or_malformed_bounds(bad: str) -> None:
    assert capture._avatar_bounds(bad) is None


@pytest.mark.parametrize(
    ("start", "end", "hour", "quiet"),
    [
        (0, 6, 3, True),
        (0, 6, 14, False),
        (0, 6, 6, False),  # end is exclusive
        (22, 6, 23, True),  # start > end wraps around midnight
        (22, 6, 2, True),
        (22, 6, 12, False),
    ],
)
def test_in_quiet_hours_respects_the_configured_window(
    monkeypatch: pytest.MonkeyPatch, start: int, end: int, hour: int, quiet: bool
) -> None:
    monkeypatch.setattr(config, "DAYNIGHT_QUIET_START", start)
    monkeypatch.setattr(config, "DAYNIGHT_QUIET_END", end)
    assert device._in_quiet_hours(datetime(2026, 1, 1, hour, tzinfo=UTC)) is quiet


def test_in_quiet_hours_uses_device_timezone(monkeypatch: pytest.MonkeyPatch) -> None:
    # 02:00 UTC is 21:00 the previous day in US/Eastern (UTC-5) — outside a 0-6 UTC-local window.
    monkeypatch.setattr(config, "DEVICE_TIMEZONE", "America/New_York")
    monkeypatch.setattr(config, "DAYNIGHT_QUIET_START", 0)
    monkeypatch.setattr(config, "DAYNIGHT_QUIET_END", 6)
    assert device._in_quiet_hours(datetime(2026, 1, 1, 2, tzinfo=UTC)) is False


@pytest.mark.parametrize("distribution", ["uniform", "lognormal"])
def test_sample_duration_stays_within_bounds(monkeypatch: pytest.MonkeyPatch, distribution: str) -> None:
    monkeypatch.setattr(config, "TIME_DISTRIBUTION", distribution)
    for _ in range(200):
        v: float = device.sample_duration(1.0, 3.0)
        assert 1.0 <= v <= 3.0


def test_sample_duration_daynight_widens_the_top_of_the_range_during_quiet_hours(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TIME_DISTRIBUTION", "daynight")
    monkeypatch.setattr(config, "DAYNIGHT_QUIET_START", 0)
    monkeypatch.setattr(config, "DAYNIGHT_QUIET_END", 6)
    quiet: datetime = datetime(2026, 1, 1, 3, tzinfo=UTC)
    awake: datetime = datetime(2026, 1, 1, 14, tzinfo=UTC)

    quiet_draws: list[float] = [device.sample_duration(1.0, 3.0, now=quiet) for _ in range(300)]
    awake_draws: list[float] = [device.sample_duration(1.0, 3.0, now=awake) for _ in range(300)]

    assert all(1.0 <= v <= 5.0 for v in quiet_draws)  # effective_hi = hi + (hi - lo) = 5.0
    assert all(1.0 <= v <= 3.0 for v in awake_draws)  # unwidened outside quiet hours
    assert max(quiet_draws) > 3.0  # actually exercises the widened top, not just permits it


def test_sample_duration_handles_hi_equal_to_lo() -> None:
    assert device.sample_duration(2.0, 2.0) == 2.0


def test_sample_duration_clips_after_repeated_out_of_range_draws(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "TIME_DISTRIBUTION", "lognormal")
    draws: list[float] = []

    def far_out(mu: float, sigma: float) -> float:
        draws.append(mu)
        return 1000.0

    monkeypatch.setattr(random, "lognormvariate", far_out)
    assert device.sample_duration(1.0, 3.0) == 3.0 and len(draws) == 8  # resampled 8 times, then clipped


def test_bounds_helpers_reject_malformed_bounds() -> None:
    assert common.bounds_center("[0,0][10,20]") == (5, 10)
    assert common.bounds_bottom_right("[0,0][100,200]") == (90, 190)
    assert common.bounds_center("nope") is None and common.bounds_bottom_right(None) is None


@pytest.mark.parametrize(
    ("text", "desc", "expected"),
    [
        ("Try Again Later", "", "Try again later"),
        ("", "We  restrict certain activity to protect our community.", "We restrict certain activity"),
        ("YOUR ACCOUNT HAS BEEN DISABLED", "", "Your account has been disabled"),
        ("someone Try again later, the shop opens at 9", "", None),  # a caption starts with its author
        ("Not now", "", None),
    ],
)  # fmt: skip
def test_restriction_text_matches_a_node_that_starts_with_one(
    text: str, desc: str, expected: str | None
) -> None:
    xml: str = hierarchy(node(cls="android.widget.TextView", text=text, desc=desc))
    assert parsing.restriction_text(xml) == expected


def test_no_feed_fixture_reads_as_a_restriction() -> None:
    assert parsing.restriction_text(FIXTURE) is None
