"""Stories that only reshare a stored post (reshare.py): finding a post's image as a card in a frame,
telling a bare reshare from one with something added, and deleting the bare ones at the end of a run.
"""

import sqlite3
from datetime import UTC, datetime

import pytest
from instadroid import capture, config, db, reshare, scrape, stories, uidevice
from PIL import Image, ImageDraw, ImageOps
from shared import sqlrows

from tests.deviceflows import feed_device

WIDTH: int = 540
HEIGHT: int = 828
BACKDROP: tuple[int, int, int] = (60, 40, 80)


def _photo(seed_size: tuple[int, int], size: tuple[int, int]) -> Image.Image:
    """A photo-like image: coarse noise scaled up, so it has features at every size it's shrunk to."""
    return ImageOps.fit(Image.effect_noise(seed_size, 80), size).convert("RGB")


_POST: Image.Image = _photo((27, 34), (1080, 1350))
_OTHER: Image.Image = _photo((27, 34), (1080, 1350))


def _story(post: Image.Image = _POST, scale: float = 0.88, top: int = 90) -> Image.Image:
    """A story frame showing `post` as a centred card on a plain backdrop."""
    frame: Image.Image = Image.new("RGB", (WIDTH, HEIGHT), BACKDROP)
    width: int = round(WIDTH * scale)
    card: Image.Image = ImageOps.fit(post, (width, round(width * post.height / post.width)))
    frame.paste(card, ((WIDTH - width) // 2, top))
    return frame


def _with_text(frame: Image.Image, top: int) -> Image.Image:
    """`frame` with a block as sharp as a line of text, 40 rows from `top` down."""
    draw: ImageDraw.ImageDraw = ImageDraw.Draw(frame)
    x: int
    for x in range(120, 420, 12):
        draw.rectangle((x, top, x + 5, top + 39), fill="white")
    return frame


def _posts(*images: Image.Image) -> list[reshare.PostImage]:
    found: list[reshare.PostImage] = []
    index: int
    img: Image.Image
    for index, img in enumerate(images):
        post: reshare.PostImage | None = reshare.post_image(f"p{index}", img)
        assert post
        found.append(post)
    return found


def _is_plain(frame: Image.Image) -> bool:
    prepared: reshare.Frame = reshare.Frame(frame)
    found: tuple[reshare.PostImage, reshare.Placement] | None = reshare.find_reshared(prepared, _posts(_POST))
    assert found
    return prepared.plain_around(found[1], _POST.height / _POST.width)


def test_a_posts_image_is_found_as_a_card_at_its_scale_and_height() -> None:
    posts: list[reshare.PostImage] = _posts(_OTHER, _POST)
    found: tuple[reshare.PostImage, reshare.Placement] | None = reshare.find_reshared(
        reshare.Frame(_story(scale=0.76, top=150)), posts
    )
    assert found
    assert found[0].post_id == "p1"
    assert found[1].score > 0.95
    assert found[1].scale == pytest.approx(0.76, abs=0.02)
    assert found[1].top == pytest.approx(150 / WIDTH, abs=0.02)


def test_a_story_of_its_own_matches_no_post() -> None:
    frame: reshare.Frame = reshare.Frame(_photo((27, 41), (WIDTH, HEIGHT)))
    assert reshare.find_reshared(frame, _posts(_POST, _OTHER)) is None


def test_a_featureless_post_image_is_not_searched_for() -> None:
    assert reshare.post_image("p", Image.new("RGB", (1080, 1350), "black")) is None


def test_a_post_image_taller_than_the_frame_fits_nowhere() -> None:
    tall: Image.Image = _photo((27, 81), (1080, 3240))
    frame: reshare.Frame = reshare.Frame(_story())
    assert frame.search(_posts(tall)[0].coarse) is None
    assert reshare.find_reshared(frame, _posts(tall)) is None


def test_a_card_against_a_flat_stretch_of_frame_does_not_correlate() -> None:
    frame: reshare.Frame = reshare.Frame(Image.new("RGB", (WIDTH, HEIGHT), BACKDROP))
    post: reshare.PostImage = _posts(_POST)[0]
    assert frame.refine(post.fine, reshare.Placement(1.0, 0.88, 0.2)).score == 0.0


def test_a_bare_reshare_is_plain_around_its_card() -> None:
    assert _is_plain(_story())


def test_text_above_or_below_the_card_is_not_plain() -> None:
    assert not _is_plain(_with_text(_story(top=150), 30))
    assert not _is_plain(_with_text(_story(top=20), 770))


def test_the_stickers_own_header_above_the_image_is_not_added_text() -> None:
    # one sticker style frames the image in a white card whose header sits just above it
    frame: Image.Image = _story(top=150)
    ImageDraw.Draw(frame).rectangle((32, 120, 507, 149), fill="white")
    assert _is_plain(frame)
    assert not _is_plain(_with_text(frame, 40))


def _store_story(con: sqlite3.Connection, story_id: str, frame: Image.Image | None) -> None:
    media_file: str | None = None
    if frame:
        media_file = f"stories/{story_id}.webp"
        (config.MEDIA_DIR / "stories").mkdir(exist_ok=True)
        capture.save_media(frame, config.MEDIA_DIR / media_file)
    con.execute(
        "INSERT INTO stories (id, username, media_file, scraped_at) VALUES (?, 'alice', ?, ?)",
        (story_id, media_file, datetime.now(UTC).isoformat()),
    )


def _store_post(con: sqlite3.Connection, post_id: str, img: Image.Image | None) -> None:
    if img:
        capture.save_media(img, config.MEDIA_DIR / f"{post_id}.webp")
    con.execute(
        "INSERT INTO posts (id, username, media_file, scraped_at) VALUES (?, 'bob', ?, ?)",
        (post_id, f"{post_id}.webp", datetime.now(UTC).isoformat()),
    )


def test_only_a_bare_reshare_of_a_stored_post_is_deleted(
    con: sqlite3.Connection, capsys: pytest.CaptureFixture[str]
) -> None:
    _store_post(con, "cover", _OTHER)
    con.execute("INSERT INTO media (post_id, idx, file) VALUES ('cover', 1, 'slide.webp')")
    capture.save_media(_POST, config.MEDIA_DIR / "slide.webp")  # the reshared image is a later slide
    _store_post(con, "pruned", None)  # its file is gone from disk
    (config.MEDIA_DIR / "junk.webp").write_bytes(b"not an image")
    _store_post(con, "junk", None)
    _store_story(con, "bare", _story())
    _store_story(con, "captioned", _with_text(_story(top=150), 30))
    _store_story(con, "own", _photo((27, 41), (WIDTH, HEIGHT)))
    _store_story(con, "fileless", None)
    _store_story(con, "lost", _story())
    (config.MEDIA_DIR / "stories" / "lost.webp").unlink()

    assert reshare.drop_reshared_stories(con, "") == 1

    left: list[sqlite3.Row] = sqlrows.fetch_all(con.execute("SELECT id FROM stories ORDER BY id"))
    assert [sqlrows.must_str(r, 0) for r in left] == ["captioned", "fileless", "lost", "own"]
    assert not (config.MEDIA_DIR / "stories" / "bare.webp").exists()
    assert (config.MEDIA_DIR / "stories" / "captioned.webp").exists()
    assert "only reshares stored post cover" in capsys.readouterr().out


def test_stories_from_before_the_run_are_left_alone(con: sqlite3.Connection) -> None:
    _store_post(con, "p", _POST)
    _store_story(con, "bare", _story())
    assert reshare.drop_reshared_stories(con, "9999") == 0
    assert sqlrows.scalar(con.execute("SELECT COUNT(*) FROM stories")) == 1


@pytest.mark.usefixtures("fast_offline")
def test_a_run_checks_its_stories_after_its_posts_and_counts_what_is_left(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "MAX_CAROUSEL_SLIDES", 1)
    monkeypatch.setattr(config, "MAX_SCROLLS", 1)
    started: str = datetime.now(UTC).isoformat()
    posts_stored: list[int] = []

    def two_stories(d: uidevice.Device, con: sqlite3.Connection) -> int:
        return 2

    def one_reshared(con: sqlite3.Connection, since: str) -> int:
        assert since >= started
        posts_stored.append(
            sqlrows.must_int(sqlrows.fetch_all(con.execute("SELECT COUNT(*) FROM posts"))[0], 0)
        )
        return 1

    monkeypatch.setattr(stories, "scrape_stories", two_stories)
    monkeypatch.setattr(reshare, "drop_reshared_stories", one_reshared)
    stats: scrape.RunStats = scrape.scrape_once(feed_device(), db.db_init())
    assert posts_stored == [stats["new"]] and stats["new"] == 2
    assert stats["metrics"].get("new_stories") == 1
