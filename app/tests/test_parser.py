from datetime import UTC, datetime
from pathlib import Path

import pytest
import scraper

FIXTURE = (Path(__file__).parent / "fixture_feed.xml").read_text()


def test_parse_hierarchy_finds_both_cards_and_skips_sponsored():
    posts = scraper.parse_hierarchy(FIXTURE)
    assert [p["username"] for p in posts] == ["someone_nice", "other_user"]


def test_headless_top_card_is_identified_from_caption_and_media():
    top = scraper.parse_hierarchy(FIXTURE)[0]
    assert top["headless"] is True
    assert top["kind"] == "video"  # "Reel by ..." maps to video
    assert top["caption"] == "Top card caption…"
    assert top["posted_date"] == "3 days ago"
    assert top["complete"] is True
    assert top["share_bounds"] == "[390,900][453,1021]"
    assert top["clip_top"] == 289


def test_full_card_fields():
    card = scraper.parse_hierarchy(FIXTURE)[1]
    assert card["kind"] == "carousel"
    assert card["place"] == "San Diego, California"
    assert card["posted_date"] == "21 hours ago"
    assert card["bounds"] == "[0,1287][1080,2000]"
    assert card["alt"].startswith("Photo 1 of 7")
    assert card["caption"] == "Second caption"
    assert card["complete"] is True
    assert card["header_bounds"] == "[0,1150][1080,1287]"


def test_headless_top_card_has_no_header_bounds():
    # Its header already scrolled off before this dump; the avatar can't be captured from it.
    top = scraper.parse_hierarchy(FIXTURE)[0]
    assert top["header_bounds"] is None


def test_card_without_caption_or_alt_is_excluded():
    # A header with neither a caption nor a media description can't be identified (post_id()
    # would hash nothing but the username); it should be left out rather than stored empty.
    xml = """<hierarchy><node><node resource-id="android:id/list">
      <node resource-id="com.instagram.android:id/row_feed_profile_header"
            content-desc="ghostuser posted a photo 2 hours ago" />
      <node resource-id="com.instagram.android:id/row_feed_button_share" bounds="[0,0][1,1]" />
      <node text="2 hours ago" />
    </node></node></hierarchy>"""
    assert scraper.parse_hierarchy(xml) == []


def test_post_id_ignores_counts_dates_and_kind():
    base = {"username": "u", "kind": "photo", "caption": "", "alt": "Photo 1 of 3 by U, 5 likes, 2 comments"}
    later = {**base, "kind": "carousel", "alt": "Photo 2 of 3 by U, 9 likes, 4 comments"}
    assert scraper.post_id(base) == scraper.post_id(later)
    assert scraper.post_id({**base, "caption": "hello"}) != scraper.post_id(base)


@pytest.mark.parametrize(
    ("desc", "expected"),
    [
        (
            "nykky posted a video in Tiger's Pictionary at #1 Fifth Ave August 29",
            ("nykky", "video", "Tiger's Pictionary at #1 Fifth Ave", "August 29"),
        ),
        ("some.one posted a photo 6 days ago", ("some.one", "photo", None, "6 days ago")),
        (
            "club posted a carousel in San Diego, California 3 days ago",
            ("club", "carousel", "San Diego, California", "3 days ago"),
        ),
        ("Sponsored", None),
    ],
)
def test_header_regex(desc, expected):
    m = scraper.SELECTORS["header_desc"].match(desc)
    if expected is None:
        assert m is None
    else:
        assert (m.group("user"), m.group("kind"), m.group("place"), m.group("date")) == expected


def test_clean_caption_strips_user_and_more():
    assert scraper.clean_caption("user Hello there… more", "user") == "Hello there…"
    assert scraper.clean_caption("user Short", "user") == "Short"


def test_clean_caption_strips_nbsp():
    assert scraper.clean_caption("user Hello there", "user") == "Hello there"


def test_permalink_regex_accepts_reel_and_p_with_tracking_params():
    rx = scraper.SELECTORS["permalink"]
    assert rx.match("https://www.instagram.com/reel/DdCSb4WsvUs/?stkn=abc").group("code") == "DdCSb4WsvUs"
    assert rx.match("https://www.instagram.com/p/Dc9fdRTSKE5/").group("type") == "p"
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
def test_parse_posted_at(text, expected, precision):
    assert scraper.parse_posted_at(text, NOW) == (expected, precision)


def test_parse_posted_at_bare_date_rolls_back_a_year_if_in_the_future():
    # "now" is Sep 8; a bare "December 25" with no year must mean last December, not next.
    dt, _ = scraper.parse_posted_at("December 25", NOW)
    assert dt.year == 2025


def test_parse_posted_at_rejects_unknown_formats():
    assert scraper.parse_posted_at("", NOW) is None
    assert scraper.parse_posted_at("sometime", NOW) is None


def test_parse_posted_at_leap_day_rollback_into_non_leap_year_does_not_raise():
    # "now" is a leap year, before Feb 29 has passed: rolling a bare "February 29" back a year
    # lands on a non-leap year, where Feb 29 doesn't exist. Must return None, not raise.
    leap_year_now = datetime(2028, 1, 15, tzinfo=UTC)
    assert scraper.parse_posted_at("February 29", leap_year_now) is None


def test_same_post_merges_a_weak_caption_placeholder_into_the_real_row():
    real = {"username": "club", "caption": "Attendance check! see you there", "posted_at": NOW}
    weak = {
        "username": "club",
        "caption": "Photo 1 of 2 by Club, 113 likes, 10 comments",
        "posted_at": NOW,
        "posted_at_precision": 86400,
    }
    assert scraper.same_post(real, weak) is True  # weak candidate merges into the real row
    assert scraper.same_post(weak, {**real, "posted_at_precision": 86400}) is True  # or vice versa


def test_same_post_refuses_two_real_differing_captions_same_day():
    a = {"username": "club", "caption": "First post of the day", "posted_at": NOW}
    b = {
        "username": "club",
        "caption": "Second, unrelated post",
        "posted_at": NOW,
        "posted_at_precision": 86400,
    }
    assert scraper.same_post(a, b) is False


def test_same_post_respects_time_tolerance():
    from datetime import timedelta

    existing = {"username": "u", "caption": "", "posted_at": NOW}
    far = {
        "username": "u",
        "caption": "",
        "posted_at": NOW - timedelta(hours=3),
        "posted_at_precision": 60,
    }
    assert scraper.same_post(existing, far) is False  # 3h apart, 60s-precision candidate


def test_same_post_requires_matching_username():
    a = {"username": "alice", "caption": "", "posted_at": NOW}
    b = {"username": "bob", "caption": "", "posted_at": NOW, "posted_at_precision": 60}
    assert scraper.same_post(a, b) is False


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


def test_parse_story_tray_skips_own_story_and_dedupes_the_nested_image():
    items = scraper.parse_story_tray(STORY_TRAY_FIXTURE)
    assert [i["username"] for i in items] == ["alice", "bob"]


def test_parse_story_tray_reports_seen_state():
    items = scraper.parse_story_tray(STORY_TRAY_FIXTURE)
    by_user = {i["username"]: i for i in items}
    assert by_user["alice"]["seen"] is False
    assert by_user["bob"]["seen"] is True
    assert by_user["alice"]["bounds"] == "[294,210][588,555]"


def test_parse_story_tray_empty_when_tray_not_on_screen():
    assert scraper.parse_story_tray("<hierarchy><node /></hierarchy>") == []


def test_capture_story_media_returns_none_for_malformed_bounds(tmp_path, monkeypatch):
    from PIL import Image

    monkeypatch.setattr(scraper, "MEDIA_DIR", tmp_path)
    img = Image.new("RGB", (200, 400), "red")

    assert scraper.capture_story_media(img, "not-bounds", 0, "tmp") is None
    assert scraper.capture_story_media(img, None, 0, "tmp") is None


def test_capture_story_media_returns_none_when_clip_leaves_too_little_height(tmp_path, monkeypatch):
    from PIL import Image

    monkeypatch.setattr(scraper, "MEDIA_DIR", tmp_path)
    img = Image.new("RGB", (200, 400), "red")

    assert scraper.capture_story_media(img, "[0,0][100,150]", 0, "tmp") is None


def test_capture_story_media_crops_and_saves_under_a_stories_subdirectory(tmp_path, monkeypatch):
    from PIL import Image

    monkeypatch.setattr(scraper, "MEDIA_DIR", tmp_path)
    img = Image.new("RGB", (200, 400), "red")

    path = scraper.capture_story_media(img, "[0,0][200,400]", 50, "tmpstory")

    assert path == tmp_path / "stories" / "tmpstory.jpg"
    assert path.exists()


def test_carousel_count_parses_slide_total():
    assert scraper.carousel_count("Photo 1 of 7 by Other User, 317 likes, 10 comments") == 7
    assert scraper.carousel_count("Video 3 of 3 by X") == 3


def test_carousel_count_defaults_to_one_for_non_carousel_alt():
    assert scraper.carousel_count("Reel by Someone Nice, Liked by a_friend and others") == 1
    assert scraper.carousel_count("") == 1


def test_safe_filename_accepts_plausible_handles():
    assert scraper._safe_filename("some.user_92") == "some.user_92"


@pytest.mark.parametrize("bad", ["../etc/passwd", "..", ".", "", "has space", "has/slash"])
def test_safe_filename_rejects_unsafe_input(bad):
    assert scraper._safe_filename(bad) is None


def test_avatar_bounds_crops_a_square_inside_the_header():
    box = scraper._avatar_bounds("[0,1150][1080,1287]")
    x1, y1, x2, y2 = box
    assert 0 < x1 < x2 <= 1080
    assert 1150 < y1 < y2 <= 1287
    assert (x2 - x1) == (y2 - y1)  # square crop


@pytest.mark.parametrize("bad", [None, "", "not-bounds"])
def test_avatar_bounds_returns_none_for_missing_or_malformed_bounds(bad):
    assert scraper._avatar_bounds(bad) is None


def test_in_quiet_hours_respects_the_configured_window(monkeypatch):
    monkeypatch.setattr(scraper, "DAYNIGHT_QUIET_START", 0)
    monkeypatch.setattr(scraper, "DAYNIGHT_QUIET_END", 6)
    assert scraper._in_quiet_hours(datetime(2026, 1, 1, 3, tzinfo=UTC)) is True  # 3am: quiet
    assert scraper._in_quiet_hours(datetime(2026, 1, 1, 14, tzinfo=UTC)) is False  # 2pm: not quiet
    assert scraper._in_quiet_hours(datetime(2026, 1, 1, 6, tzinfo=UTC)) is False  # end is exclusive


def test_in_quiet_hours_wraps_past_midnight(monkeypatch):
    # A window like 22:00-06:00 has start > end and must wrap around midnight.
    monkeypatch.setattr(scraper, "DAYNIGHT_QUIET_START", 22)
    monkeypatch.setattr(scraper, "DAYNIGHT_QUIET_END", 6)
    assert scraper._in_quiet_hours(datetime(2026, 1, 1, 23, tzinfo=UTC)) is True
    assert scraper._in_quiet_hours(datetime(2026, 1, 1, 2, tzinfo=UTC)) is True
    assert scraper._in_quiet_hours(datetime(2026, 1, 1, 12, tzinfo=UTC)) is False


def test_in_quiet_hours_uses_device_timezone(monkeypatch):
    # 02:00 UTC is 21:00 the previous day in US/Eastern (UTC-5) — outside a 0-6 UTC-local window.
    monkeypatch.setattr(scraper, "DEVICE_TIMEZONE", "America/New_York")
    monkeypatch.setattr(scraper, "DAYNIGHT_QUIET_START", 0)
    monkeypatch.setattr(scraper, "DAYNIGHT_QUIET_END", 6)
    assert scraper._in_quiet_hours(datetime(2026, 1, 1, 2, tzinfo=UTC)) is False


def test_sample_duration_uniform_stays_within_bounds():
    for _ in range(200):
        v = scraper.sample_duration(1.0, 3.0)
        assert 1.0 <= v <= 3.0


def test_sample_duration_lognormal_stays_within_bounds(monkeypatch):
    monkeypatch.setattr(scraper, "TIME_DISTRIBUTION", "lognormal")
    for _ in range(200):
        v = scraper.sample_duration(1.0, 3.0)
        assert 1.0 <= v <= 3.0


def test_sample_duration_daynight_widens_the_top_of_the_range_during_quiet_hours(monkeypatch):
    monkeypatch.setattr(scraper, "TIME_DISTRIBUTION", "daynight")
    monkeypatch.setattr(scraper, "DEVICE_TIMEZONE", "")
    monkeypatch.setattr(scraper, "DAYNIGHT_QUIET_START", 0)
    monkeypatch.setattr(scraper, "DAYNIGHT_QUIET_END", 6)
    quiet = datetime(2026, 1, 1, 3, tzinfo=UTC)
    awake = datetime(2026, 1, 1, 14, tzinfo=UTC)

    quiet_draws = [scraper.sample_duration(1.0, 3.0, now=quiet) for _ in range(300)]
    awake_draws = [scraper.sample_duration(1.0, 3.0, now=awake) for _ in range(300)]

    assert all(1.0 <= v <= 5.0 for v in quiet_draws)  # effective_hi = hi + (hi - lo) = 5.0
    assert all(1.0 <= v <= 3.0 for v in awake_draws)  # unwidened outside quiet hours
    assert max(quiet_draws) > 3.0  # actually exercises the widened top, not just permits it


def test_sample_duration_handles_hi_equal_to_lo():
    assert scraper.sample_duration(2.0, 2.0) == 2.0
