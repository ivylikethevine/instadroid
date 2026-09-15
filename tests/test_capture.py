"""Capturing a post or story: permalinks through the share sheet, expanded captions, media formats, story dedupe."""

from pathlib import Path

import pytest
from instadroid import (
    capture,
    config,
    db,
    parsing,
    stories,
)
from PIL import Image, ImageDraw, ImageOps
from shared import sqlrows

from tests.deviceflows import CAPTION, TOP_URL, feed_device, following_screen, top_card_id
from tests.fakedevice import HEIGHT, WIDTH, FakeDevice, Node, node

pytestmark = pytest.mark.usefixtures("fast_offline")


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
    assert sqlrows.scalar(con.execute("SELECT COUNT(*) FROM stories")) == 2
    assert len(list((config.MEDIA_DIR / "stories").iterdir())) == 2  # discarded crops removed


def test_a_blank_story_frame_is_discarded(monkeypatch: pytest.MonkeyPatch) -> None:
    con = db.db_init()
    d = feed_device(start="home")
    d.screenshot = lambda: Image.new("RGB", (WIDTH, HEIGHT), (2, 2, 2))
    assert stories.scrape_stories(d, con) == 0
    assert sqlrows.scalar(con.execute("SELECT COUNT(*) FROM stories")) == 0


# --- media format ---------------------------------------------------------------------------------


@pytest.mark.parametrize("media_format", list(config.MEDIA_FORMATS))
def test_each_media_format_writes_its_own_encoding_and_extension(
    fast_offline: Path, monkeypatch: pytest.MonkeyPatch, media_format: str
) -> None:
    monkeypatch.setattr(config, "MEDIA_FORMAT", media_format)
    ext, pil_format = config.MEDIA_FORMATS[media_format]
    path = stories.capture_story_media(Image.new("RGB", (200, 400), "red"), "[0,0][200,400]", 0, "s")
    assert path is not None
    assert path.name == f"s{ext}" and ext in config.MEDIA_EXTS
    with Image.open(path) as img:
        assert img.format == pil_format


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
