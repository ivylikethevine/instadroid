import re
from datetime import UTC, datetime
from pathlib import Path

import igprofiles
import pytest
from igprofiles.screens import id_matches
from instadroid import capture, common, config, device, parsing, stories, versioning

FIXTURE = igprofiles.fixture("v424", "feed_445.xml").read_text()


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
REEL_COLLAB_FIXTURE = """<hierarchy><node><node resource-id="android:id/list">
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


def test_clean_caption_strips_user_and_more() -> None:
    assert parsing.clean_caption("user Hello there… more", "user") == "Hello there…"
    assert parsing.clean_caption("user Short", "user") == "Short"


def test_clean_caption_strips_nbsp() -> None:
    assert parsing.clean_caption("user Hello there", "user") == "Hello there"


def test_permalink_regex_accepts_reel_and_p_with_tracking_params() -> None:
    rx: re.Pattern[str] = versioning.SELECTORS["permalink"]
    reel: re.Match[str] | None = rx.match("https://www.instagram.com/reel/AbCdEf12345/?stkn=abc")
    post: re.Match[str] | None = rx.match("https://www.instagram.com/p/ZyXwVu98765/")
    assert reel is not None and reel.group("code") == "AbCdEf12345"
    assert post is not None and post.group("type") == "p"
    assert rx.match("https://www.instagram.com/someone/") is None


NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


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


def test_parse_posted_at_rejects_unknown_formats() -> None:
    assert parsing.parse_posted_at("", NOW) is None
    assert parsing.parse_posted_at("sometime", NOW) is None


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


def test_same_post_refuses_two_real_differing_captions_same_day() -> None:
    a: parsing.PostIdentity = {"username": "club", "caption": "First post of the day", "posted_at": NOW}
    b: parsing.PostIdentity = {
        "username": "club",
        "caption": "Second, unrelated post",
        "posted_at": NOW,
        "posted_at_precision": 86400,
    }
    assert parsing.same_post(a, b) is False


def test_same_post_respects_time_tolerance() -> None:
    from datetime import timedelta

    existing: parsing.PostIdentity = {"username": "u", "caption": "", "posted_at": NOW}
    far: parsing.PostIdentity = {
        "username": "u",
        "caption": "",
        "posted_at": NOW - timedelta(hours=3),
        "posted_at_precision": 60,
    }
    assert parsing.same_post(existing, far) is False  # 3h apart, 60s-precision candidate


def test_same_post_requires_matching_username() -> None:
    a: parsing.PostIdentity = {"username": "alice", "caption": "", "posted_at": NOW}
    b: parsing.PostIdentity = {"username": "bob", "caption": "", "posted_at": NOW, "posted_at_precision": 60}
    assert parsing.same_post(a, b) is False


STORY_TRAY_FIXTURE = """<hierarchy><node><node resource-id="com.instagram.android:id/reels_tray_container"
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
FOLLOWING_LIST_FIXTURE = """<hierarchy><node><node resource-id="com.instagram.android:id/frame_header">
    <node resource-id="com.instagram.android:id/row_header_textview" text="Categories" />
  </node>
  <node resource-id="com.instagram.android:id/container" content-desc="Least interacted with">
    <node resource-id="com.instagram.android:id/title" text="Least interacted with" />
    <node resource-id="com.instagram.android:id/subtitle" text="ashnikko and 6 others" />
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


def test_capture_story_media_returns_none_for_malformed_bounds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PIL import Image

    monkeypatch.setattr(config, "MEDIA_DIR", tmp_path)
    img: Image.Image = Image.new("RGB", (200, 400), "red")

    assert stories.capture_story_media(img, "not-bounds", 0, "tmp") is None
    assert stories.capture_story_media(img, None, 0, "tmp") is None


def test_capture_story_media_returns_none_when_clip_leaves_too_little_height(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PIL import Image

    monkeypatch.setattr(config, "MEDIA_DIR", tmp_path)
    img: Image.Image = Image.new("RGB", (200, 400), "red")

    assert stories.capture_story_media(img, "[0,0][100,150]", 0, "tmp") is None


def test_capture_story_media_crops_and_saves_under_a_stories_subdirectory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PIL import Image

    monkeypatch.setattr(config, "MEDIA_DIR", tmp_path)
    img: Image.Image = Image.new("RGB", (200, 400), "red")

    path: Path | None = stories.capture_story_media(img, "[0,0][200,400]", 50, "tmpstory")

    assert path == tmp_path / "stories" / "tmpstory.webp"
    assert path is not None and path.exists()


def test_carousel_count_parses_slide_total() -> None:
    assert parsing.carousel_count("Photo 1 of 7 by Other User, 317 likes, 10 comments") == 7
    assert parsing.carousel_count("Video 3 of 3 by X") == 3


def test_carousel_count_defaults_to_one_for_non_carousel_alt() -> None:
    assert parsing.carousel_count("Reel by Someone Nice, Liked by a_friend and others") == 1
    assert parsing.carousel_count("") == 1


def test_safe_filename_accepts_plausible_handles() -> None:
    assert common.safe_filename("some.user_92") == "some.user_92"


@pytest.mark.parametrize("bad", ["../etc/passwd", "..", ".", "", "has space", "has/slash"])
def test_safe_filename_rejects_unsafe_input(bad: str) -> None:
    assert common.safe_filename(bad) is None


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


def test_in_quiet_hours_respects_the_configured_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "DAYNIGHT_QUIET_START", 0)
    monkeypatch.setattr(config, "DAYNIGHT_QUIET_END", 6)
    assert device._in_quiet_hours(datetime(2026, 1, 1, 3, tzinfo=UTC)) is True  # 3am: quiet
    assert device._in_quiet_hours(datetime(2026, 1, 1, 14, tzinfo=UTC)) is False  # 2pm: not quiet
    assert device._in_quiet_hours(datetime(2026, 1, 1, 6, tzinfo=UTC)) is False  # end is exclusive


def test_in_quiet_hours_wraps_past_midnight(monkeypatch: pytest.MonkeyPatch) -> None:
    # A window like 22:00-06:00 has start > end and must wrap around midnight.
    monkeypatch.setattr(config, "DAYNIGHT_QUIET_START", 22)
    monkeypatch.setattr(config, "DAYNIGHT_QUIET_END", 6)
    assert device._in_quiet_hours(datetime(2026, 1, 1, 23, tzinfo=UTC)) is True
    assert device._in_quiet_hours(datetime(2026, 1, 1, 2, tzinfo=UTC)) is True
    assert device._in_quiet_hours(datetime(2026, 1, 1, 12, tzinfo=UTC)) is False


def test_in_quiet_hours_uses_device_timezone(monkeypatch: pytest.MonkeyPatch) -> None:
    # 02:00 UTC is 21:00 the previous day in US/Eastern (UTC-5) — outside a 0-6 UTC-local window.
    monkeypatch.setattr(config, "DEVICE_TIMEZONE", "America/New_York")
    monkeypatch.setattr(config, "DAYNIGHT_QUIET_START", 0)
    monkeypatch.setattr(config, "DAYNIGHT_QUIET_END", 6)
    assert device._in_quiet_hours(datetime(2026, 1, 1, 2, tzinfo=UTC)) is False


def test_sample_duration_uniform_stays_within_bounds() -> None:
    for _ in range(200):
        v: float = device.sample_duration(1.0, 3.0)
        assert 1.0 <= v <= 3.0


def test_sample_duration_lognormal_stays_within_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "TIME_DISTRIBUTION", "lognormal")
    for _ in range(200):
        v: float = device.sample_duration(1.0, 3.0)
        assert 1.0 <= v <= 3.0


def test_sample_duration_daynight_widens_the_top_of_the_range_during_quiet_hours(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TIME_DISTRIBUTION", "daynight")
    monkeypatch.setattr(config, "DEVICE_TIMEZONE", "")
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
