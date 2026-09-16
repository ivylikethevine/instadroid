"""Capturing a post or story: permalinks through the share sheet, expanded captions, media formats, story dedupe."""

import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest
from instadroid import (
    capture,
    config,
    db,
    parsing,
    stories,
)
from PIL import Image, ImageDraw, ImageFile, ImageOps
from shared import sqlrows

from tests.deviceflows import CAPTION, TOP_URL, feed_device, following_screen, top_card_id
from tests.fakedevice import HEIGHT, WIDTH, FakeDevice, FakeSelector, Node, Out, hierarchy, node

pytestmark = pytest.mark.usefixtures("fast_offline")


def test_fetch_permalink_copies_and_canonicalises_the_link() -> None:
    d: FakeDevice = feed_device(start="following")
    assert capture.fetch_permalink(d, top_card_id(d)) == ("https://www.instagram.com/reel/TOP123/", None)
    assert d.screen == "following"


def test_fetch_permalink_reports_a_sheet_that_never_opens(fast_offline: Path) -> None:
    d: FakeDevice = feed_device(top_share="", start="following")
    assert capture.fetch_permalink(d, top_card_id(d)) == (None, "sheet")
    assert (fast_offline / "debug" / "share_sheet_hierarchy.xml").exists()


def test_fetch_permalink_reports_a_clipboard_that_never_updates() -> None:
    d: FakeDevice = feed_device(top_share="share_noclip", start="following")
    assert capture.fetch_permalink(d, top_card_id(d)) == (None, "clipboard")


def _dumpsys(d: FakeDevice, monkeypatch: pytest.MonkeyPatch, clip: str) -> None:
    """Make `d` answer `dumpsys clipboard` with `clip`, everything else as before."""
    real: Callable[[str | list[str], float], Out] = d.shell

    def shell(cmdargs: str | list[str], timeout: float = 60) -> Out:
        joined: str = " ".join(cmdargs) if isinstance(cmdargs, list) else cmdargs
        return Out(clip) if joined == "dumpsys clipboard" else real(cmdargs, timeout)

    monkeypatch.setattr(d, "shell", shell)


def test_fetch_permalink_falls_back_to_dumpsys_clipboard(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """uiautomator2's read is empty (the Android 10+ background-clipboard restriction), but the root
    `dumpsys clipboard` dump shows the link."""
    d: FakeDevice = feed_device(top_share="share_noclip", start="following")
    _dumpsys(
        d,
        monkeypatch,
        '  mPrimaryClip=ClipData { text/plain "" {T:https://www.instagram.com/p/DUMP1/?igsh=x} }',
    )
    assert capture.fetch_permalink(d, top_card_id(d)) == ("https://www.instagram.com/p/DUMP1/", None)
    assert "permalink read via dumpsys clipboard" in capsys.readouterr().out


def test_fetch_permalink_notes_a_redacted_dumpsys_clip_once(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    d: FakeDevice = feed_device(top_share="share_noclip", start="following")
    _dumpsys(d, monkeypatch, "  mPrimaryClip=ClipData { text/plain {T:<redacted>} }")
    capture.reset_last_url(d)
    assert capture.fetch_permalink(d, top_card_id(d)) == (None, "clipboard")
    assert capture.fetch_permalink(d, top_card_id(d)) == (None, "clipboard")
    assert capsys.readouterr().out.count("shows a clip but no permalink") == 1


def test_fetch_permalink_ignores_the_previous_posts_link_left_in_the_clipboard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(capture, "_last_code", capture.permalink_code(TOP_URL))
    d: FakeDevice = feed_device(start="following")
    assert capture.fetch_permalink(d, top_card_id(d)) == (None, "clipboard")


def test_the_same_link_read_raw_and_trimmed_is_still_the_same_link() -> None:
    """Copy link yields TOP_URL (tracking parameters, trailing slash); the previous run left the trimmed
    form on the clipboard. They share a shortcode, so the new read is stale, not a fresh permalink."""
    d: FakeDevice = feed_device(start="following")
    d.clipboard = "https://www.instagram.com/reel/TOP123/"
    capture.reset_last_url(d)
    assert capture._last_code == "TOP123"
    assert capture.fetch_permalink(d, top_card_id(d)) == (None, "clipboard")


def test_fetch_permalink_when_the_card_is_gone() -> None:
    assert capture.fetch_permalink(feed_device(start="following"), "no-such-card") == (None, "sheet")


def test_fetch_permalink_refuses_to_act_inside_a_stuck_sheet() -> None:
    d: FakeDevice = feed_device(start="share_top")
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
    d: FakeDevice = FakeDevice(
        {
            "following": following_screen(_caption_card("someone_nice Short start… more", goto="expanded")),
            "expanded": following_screen(
                _caption_card("someone_nice Short start continues on with the full text")
            ),
        },
        "following",
    )
    p: parsing.Post = parsing.parse_hierarchy(d.dump_hierarchy())[0]
    assert p["caption_truncated"] is True

    assert capture.expand_caption(d, p) == "Short start continues on with the full text"


def test_expand_caption_falls_back_to_the_truncated_text_when_the_tap_does_nothing() -> None:
    d: FakeDevice = FakeDevice(
        {
            "following": following_screen(_caption_card("someone_nice Short start… more"))
        },  # no goto: tap is inert
        "following",
    )
    p: parsing.Post = parsing.parse_hierarchy(d.dump_hierarchy())[0]

    assert capture.expand_caption(d, p) == "Short start…"
    assert d.screen == "following"  # never knocked off the feed


def test_expand_caption_is_a_noop_for_a_caption_that_was_never_truncated() -> None:
    d: FakeDevice = FakeDevice(
        {"following": following_screen(_caption_card("someone_nice Whole caption, no more span"))},
        "following",
    )
    p: parsing.Post = parsing.parse_hierarchy(d.dump_hierarchy())[0]
    assert p["caption_truncated"] is False

    assert capture.expand_caption(d, p) == "Whole caption, no more span"
    assert d.taps == []  # nothing to tap


_NOISE = Image.effect_noise((WIDTH // 8, HEIGHT // 8), 80)  # random, so generated once


def _story_frame(seed: int, overlay: bool = False) -> Image.Image:
    """A photo-like screenshot (noise), optionally with a bar drawn over its lower part, the way a
    tooltip or reply box can differ between two captures of the same story."""
    img: Image.Image = ImageOps.fit(_NOISE, (WIDTH, HEIGHT)).convert("RGB")
    if seed:
        img = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if overlay:
        ImageDraw.Draw(img).rectangle((100, HEIGHT - 400, WIDTH - 100, HEIGHT - 300), fill="white")
    return img


def test_a_recaptured_story_is_not_stored_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    con: sqlite3.Connection = db.db_init()
    frame: Image.Image = _story_frame(0)
    d: FakeDevice = feed_device(start="home")
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
    con: sqlite3.Connection = db.db_init()
    d: FakeDevice = feed_device(start="home")
    d.screenshot = lambda: Image.new("RGB", (WIDTH, HEIGHT), (2, 2, 2))
    assert stories.scrape_stories(d, con) == 0
    assert sqlrows.scalar(con.execute("SELECT COUNT(*) FROM stories")) == 0


# --- media format ---------------------------------------------------------------------------------


@pytest.mark.parametrize("media_format", list(config.MEDIA_FORMATS))
def test_each_media_format_writes_its_own_encoding_and_extension(
    fast_offline: Path, monkeypatch: pytest.MonkeyPatch, media_format: str
) -> None:
    monkeypatch.setattr(config, "MEDIA_FORMAT", media_format)
    ext: str
    pil_format: str
    ext, pil_format = config.MEDIA_FORMATS[media_format]
    path: Path | None = stories.capture_story_media(
        Image.new("RGB", (200, 400), "red"), "[0,0][200,400]", 0, "s"
    )
    assert path is not None
    assert path.name == f"s{ext}" and ext in config.MEDIA_EXTS
    img: ImageFile.ImageFile
    with Image.open(path) as img:
        assert img.format == pil_format


def test_recapturing_an_avatar_in_a_new_format_drops_the_old_file(
    fast_offline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    avatars: Path = config.MEDIA_DIR / "avatars"
    avatars.mkdir(parents=True)
    (avatars / "old_user.jpg").write_bytes(b"old jpeg")  # captured before switching MEDIA_FORMAT
    d: FakeDevice = feed_device(start="older")
    header: str | None = parsing.parse_hierarchy(d.screens["older"])[0]["header_bounds"]
    assert header is not None
    assert capture.capture_avatar(d, header, "old_user") == "avatars/old_user.webp"
    assert sorted(f.name for f in avatars.iterdir()) == ["old_user.webp"]


# --- a device that misbehaves mid-capture ---------------------------------------------------------


def _no_clipboard(d: FakeDevice) -> str | None:
    raise RuntimeError("clipboard service unavailable")


def _drop_clipboard(d: FakeDevice, value: str | None) -> None:
    pass  # the Copy link tap still writes; only the read is broken


def test_a_clipboard_that_cannot_be_read_is_treated_as_empty(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    d: FakeDevice = feed_device(start="following")
    monkeypatch.setattr(FakeDevice, "clipboard", property(_no_clipboard, _drop_clipboard), raising=False)
    monkeypatch.setattr(capture, "_last_code", "LEFTOVER")
    capture.reset_last_url(d)
    assert capture._last_code == ""  # nothing to compare against: the first link read counts as fresh
    assert capture.fetch_permalink(d, top_card_id(d)) == (None, "clipboard")
    assert "clipboard read failed" in capsys.readouterr().out
    assert d.screen == "following"


def test_fetch_permalink_survives_a_dumpsys_clipboard_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(capture, "_dumpsys_failed", False)  # run state, normally reset by reset_last_url()
    d: FakeDevice = feed_device(top_share="share_noclip", start="following")
    real: Callable[[str | list[str], float], Out] = d.shell

    def shell(cmdargs: str | list[str], timeout: float = 60) -> Out:
        joined: str = " ".join(cmdargs) if isinstance(cmdargs, list) else cmdargs
        if joined == "dumpsys clipboard":
            raise RuntimeError("dumpsys: service not found")
        return real(cmdargs, timeout)

    monkeypatch.setattr(d, "shell", shell)
    assert capture.fetch_permalink(d, top_card_id(d)) == (None, "clipboard")
    assert capture.fetch_permalink(d, top_card_id(d)) == (None, "clipboard")
    assert capsys.readouterr().out.count("dumpsys clipboard failed") == 1  # not retried this run


class _VanishingSelector(FakeSelector):
    @property
    def info(self) -> dict[str, dict[str, int]]:
        raise LookupError("node vanished")


class _VanishingSheetDevice(FakeDevice):
    """The share sheet opens, but its Copy link node is gone by the time its bounds are read."""

    def __call__(self, **kwargs: str | list[str]) -> FakeSelector:
        if kwargs.get("description") == "Copy link":
            return _VanishingSelector(self, kwargs)
        return super().__call__(**kwargs)


def test_fetch_permalink_reports_a_sheet_whose_copy_link_vanishes(capsys: pytest.CaptureFixture[str]) -> None:
    base: FakeDevice = feed_device(start="following")
    d: _VanishingSheetDevice = _VanishingSheetDevice(
        base.screens, "following", back=base.back, scroll=base.scroll, hswipe=base.hswipe
    )
    assert capture.fetch_permalink(d, top_card_id(d)) == (None, "sheet")
    assert d.screen == "following"  # the sheet was closed and the feed recovered
    assert "Copy link vanished" in capsys.readouterr().out


def _truncated_post(d: FakeDevice) -> parsing.Post:
    p: parsing.Post = parsing.parse_hierarchy(d.dump_hierarchy())[0]
    assert p["caption_truncated"] is True
    return p


def test_expand_caption_gives_up_without_a_distinctive_prefix_or_a_usable_point() -> None:
    d: FakeDevice = FakeDevice(
        {"following": following_screen(_caption_card("someone_nice Short start… more", goto="expanded"))},
        "following",
    )
    p: parsing.Post = _truncated_post(d)
    p["caption"] = "…"  # nothing left to recognise the re-read caption node by
    assert capture.expand_caption(d, p) == "…"
    p = _truncated_post(d)
    p["caption_bounds"] = "not bounds"
    assert capture.expand_caption(d, p) == "Short start…"
    assert d.taps == []  # neither case risked a tap


def test_expand_caption_falls_back_when_the_tap_itself_fails(capsys: pytest.CaptureFixture[str]) -> None:
    class NoTapDevice(FakeDevice):
        def click(self, x: int, y: int) -> None:
            raise RuntimeError("uiautomator jsonrpc unreachable")

    d: NoTapDevice = NoTapDevice(
        {"following": following_screen(_caption_card("someone_nice Short start… more"))}, "following"
    )
    assert capture.expand_caption(d, _truncated_post(d)) == "Short start…"
    assert "caption expand tap failed" in capsys.readouterr().out


def test_expand_caption_recovers_the_feed_when_the_tap_opens_another_screen(
    capsys: pytest.CaptureFixture[str],
) -> None:
    d: FakeDevice = FakeDevice(
        {
            "following": following_screen(_caption_card("someone_nice Short start… more", goto="profile")),
            "profile": hierarchy(
                node(cls="android.widget.TextView", text="someone_nice", bounds=(0, 0, 1080, 200))
            ),
        },
        "following",
        back={"profile": "following"},
    )
    assert capture.expand_caption(d, _truncated_post(d)) == "Short start…"
    assert d.screen == "following" and "recovering" in capsys.readouterr().out


def test_crop_media_skips_unparseable_or_mostly_off_screen_bounds(capsys: pytest.CaptureFixture[str]) -> None:
    d: FakeDevice = feed_device(start="following")
    assert capture.crop_media(d, None, "x") is None
    assert capture.crop_media(d, "[0,2300][1080,2900]", "x") is None  # 40 of 600px on screen
    assert capture.crop_media(d, "[0,0][1080,150]", "x") is None  # too short to be a post image
    assert capsys.readouterr().out.count("mostly off-screen") == 2
    assert not (config.MEDIA_DIR / "x.webp").exists()


def _carousel_card(slide: int, media_bounds: tuple[int, int, int, int]) -> list[Node]:
    return [
        node(
            "row_feed_profile_header",
            desc="other_user posted a carousel 21 hours ago",
            bounds=(0, 300, 1080, 437),
        ),
        node(
            "carousel_media_group",
            bounds=media_bounds,
            children=[
                node(
                    "carousel_image",
                    desc=f"Photo {slide} of 3 by Other User, 317 likes, 10 comments",
                    bounds=media_bounds,
                )
            ],
        ),
        node("row_feed_button_share", bounds=(390, 2000, 453, 2121)),
        node(cls=CAPTION, text="other_user Second caption", bounds=(32, 2130, 1080, 2200)),
    ]


def test_capture_carousel_stops_at_a_slide_that_cannot_be_cropped() -> None:
    d: FakeDevice = FakeDevice(
        {
            "following": following_screen(_carousel_card(1, (0, 437, 1080, 1150))),
            "slide2": following_screen(_carousel_card(2, (0, 2200, 1080, 2913))),  # scrolled nearly off
            "slide3": following_screen(_carousel_card(3, (0, 437, 1080, 1150))),
        },
        "following",
        hswipe={"following": "slide2", "slide2": "slide3"},
    )
    p: parsing.Post = parsing.parse_hierarchy(d.dump_hierarchy())[0]
    assert capture.capture_carousel(d, p, "OTHER1") == []
    assert len(d.swipes) == 1  # never swiped on to slide 3


def test_capture_avatar_refuses_an_unsafe_username_or_bad_bounds() -> None:
    d: FakeDevice = feed_device(start="older")
    header: str | None = parsing.parse_hierarchy(d.screens["older"])[0]["header_bounds"]
    assert header is not None
    assert capture.capture_avatar(d, header, "../etc/passwd") is None
    assert capture.capture_avatar(d, "garbage", "old_user") is None
    assert not (config.MEDIA_DIR / "avatars").exists()
