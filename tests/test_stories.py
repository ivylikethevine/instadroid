"""The story tray's failure paths (stories.scrape_stories, capture_story): an item without bounds, a
viewer that closes before it's read, a Home feed that can't be reached or recovered, a same-bytes recapture.
"""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from instadroid import (
    config,
    db,
    parsing,
    stories,
)
from PIL import Image, ImageDraw, ImageFile, ImageOps, ImageStat
from shared import sqlrows

from tests.deviceflows import feed_device, following_screen
from tests.fakedevice import HEIGHT, WIDTH, FakeDevice, hierarchy, node

pytestmark: pytest.MarkDecorator = pytest.mark.usefixtures("fast_offline")

_NOISE: Image.Image = Image.effect_noise((WIDTH // 8, HEIGHT // 8), 80)  # random, so generated once


def _frame() -> Image.Image:
    """A photo-like screenshot: FakeDevice's own solid colour can read as a blank frame."""
    return ImageOps.fit(_NOISE, (WIDTH, HEIGHT)).convert("RGB")


def test_capture_story_skips_an_item_without_usable_bounds() -> None:
    d: FakeDevice = feed_device(start="home")
    item: parsing.StoryItem = {"username": "alice", "seen": False, "bounds": "not-bounds"}
    assert stories.capture_story(d, item) is None
    assert d.taps == [] and d.presses == []


def test_a_story_that_closes_before_it_is_read_is_skipped_with_a_dump(
    fast_offline: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    class AutoExiting(FakeDevice):
        """The viewer answers the cheap exists() check, then is gone by the time of the full dump."""

        def dump_hierarchy(self) -> str:
            return hierarchy() if self.screen == "story_alice" else super().dump_hierarchy()

    con: sqlite3.Connection = db.db_init()
    d: AutoExiting = AutoExiting(feed_device(start="home").screens, "home", back={"story_alice": "home"})
    assert stories.scrape_stories(d, con) == 0
    assert "closed before it could be read" in capsys.readouterr().out
    assert (fast_offline / "debug" / "story_alice_hierarchy.xml").exists()
    assert d.presses[0] == "back"  # still backed out of wherever the viewer left us


def test_scrape_stories_skips_the_run_when_home_is_out_of_reach(capsys: pytest.CaptureFixture[str]) -> None:
    con: sqlite3.Connection = db.db_init()
    d: FakeDevice = FakeDevice({"following": following_screen()}, "following")  # back leads nowhere
    assert stories.scrape_stories(d, con) == 0
    assert d.presses == ["back"] * 3
    assert "could not reach the Home feed" in capsys.readouterr().out


def test_scrape_stories_stops_early_when_home_cannot_be_recovered(capsys: pytest.CaptureFixture[str]) -> None:
    class Stubborn(FakeDevice):
        """Ejected to the launcher by alice's story; the next few relaunches are ignored, then the
        run's own return to the feed works, so the early stop is what gets tested, not a crash."""

        ignored: int = 3

        def app_start(self, package_name: str, activity: str | None = None, stop: bool = False) -> None:
            if self.ignored > 0:
                self.ignored -= 1
                self.launches.append(activity)
                return
            super().app_start(package_name, activity, stop)

    con: sqlite3.Connection = db.db_init()
    base: FakeDevice = feed_device(start="home")
    d: Stubborn = Stubborn(base.screens, "home", back=base.back)
    d.screenshot = _frame
    assert stories.scrape_stories(d, con) == 1  # alice; bob's stale coordinates were never tapped
    assert len(d.taps) == 1
    assert d.ignored == 0
    assert "could not recover the Home feed" in capsys.readouterr().out
    assert d.screen == "following"  # the run still ended up back on its feed


def test_a_byte_identical_recapture_is_dropped_by_the_stories_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The perceptual check only looks a day back; an older row with the same content digest still
    # blocks the insert, and the fresh crop is removed rather than left orphaned.
    con: sqlite3.Connection = db.db_init()
    d: FakeDevice = feed_device(start="home")
    d.screenshot = _frame
    assert stories.scrape_stories(d, con) == 1
    stale: str = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    con.execute("UPDATE stories SET scraped_at=?", (stale,))
    con.commit()

    d = feed_device(start="home")
    d.screenshot = _frame
    assert stories.scrape_stories(d, con) == 0

    assert sqlrows.scalar(con.execute("SELECT COUNT(*) FROM stories")) == 1
    assert len(list((config.MEDIA_DIR / "stories").iterdir())) == 1


def test_a_story_is_saved_whole_and_identified_by_what_is_below_its_header() -> None:
    con: sqlite3.Connection = db.db_init()
    d: FakeDevice = feed_device(start="home")
    d.screenshot = _frame
    assert stories.scrape_stories(d, con) == 1
    saved: ImageFile.ImageFile
    with Image.open(next((config.MEDIA_DIR / "stories").iterdir())) as saved:
        assert saved.size == (WIDTH, 2200 - 150)  # the viewer's whole media container

    def other_header() -> Image.Image:
        """The same frame an hour on: only the header overlay (down to y=400) reads differently."""
        img: Image.Image = _frame()
        ImageDraw.Draw(img).rectangle((0, 150, WIDTH, 399), fill="white")
        return img

    d = feed_device(start="home")
    d.screenshot = other_header
    assert stories.scrape_stories(d, con) == 0


def test_a_dark_first_frame_is_retaken() -> None:
    con: sqlite3.Connection = db.db_init()
    d: FakeDevice = feed_device(start="home")
    shots: list[Image.Image] = [Image.new("RGB", (WIDTH, HEIGHT), (3, 3, 3)), _frame()]
    d.screenshot = lambda: shots.pop(0)
    assert stories.scrape_stories(d, con) == 1  # the fade-in frame alone would have been blank
    assert shots == []


def test_another_accounts_frame_has_to_match_more_closely() -> None:
    con: sqlite3.Connection = db.db_init()
    now: str = datetime.now(UTC).isoformat()
    con.execute(
        "INSERT INTO stories (id, username, scraped_at, phash) VALUES ('s1', 'alice', ?, ?)",
        (now, f"{0:016x}"),
    )
    near: str = f"{0b11:016x}"
    nearish: str = f"{0b1111111:016x}"
    assert stories._find_story_duplicate(con, "alice", nearish)
    assert not stories._find_story_duplicate(con, "bob", nearish)
    assert stories._find_story_duplicate(con, "bob", near)  # the frame both accounts reshared


def test_a_story_viewer_without_a_media_node_stores_nothing() -> None:
    con: sqlite3.Connection = db.db_init()
    d: FakeDevice = feed_device(start="home")
    d.screens["story_alice"] = hierarchy(node("reel_viewer_root", bounds=(0, 0, WIDTH, HEIGHT)))
    assert stories.scrape_stories(d, con) == 0
    assert sqlrows.scalar(con.execute("SELECT COUNT(*) FROM stories")) == 0


def test_the_header_avatar_and_texts_are_painted_over_in_the_saved_frame() -> None:
    avatar: tuple[int, int, int, int] = (32, 200, 116, 284)
    name: tuple[int, int, int, int] = (148, 200, 317, 244)
    age: tuple[int, int, int, int] = (341, 200, 407, 244)
    reshared_avatar: tuple[int, int, int, int] = (148, 260, 190, 300)  # no node of its own, beside its text
    con: sqlite3.Connection = db.db_init()
    d: FakeDevice = feed_device(start="home")
    d.screens["story_alice"] = hierarchy(
        node(
            "reel_viewer_root",
            bounds=(0, 0, WIDTH, HEIGHT),
            children=[
                node("reel_viewer_media_container", bounds=(0, 150, WIDTH, 2200)),
                node("reel_viewer_top_shadow", bounds=(0, 150, WIDTH, 400)),
                node("reel_viewer_profile_picture", bounds=avatar),
                node(
                    "reel_viewer_text_container",
                    bounds=(116, 190, 975, 330),
                    children=[
                        node(cls="android.widget.TextView", text="alice", bounds=name),
                        node(cls="android.widget.TextView", text="5h", bounds=age),
                        node(
                            bounds=(148, 256, 560, 304),
                            children=[
                                node(cls="android.widget.TextView", text="carol", bounds=(200, 260, 330, 300))
                            ],
                        ),
                    ],
                ),
            ],
        )
    )

    def with_header() -> Image.Image:
        img: Image.Image = Image.new("RGB", (WIDTH, HEIGHT), "gray")
        img.paste(_frame().crop((0, 400, WIDTH, HEIGHT)), (0, 400))
        box: tuple[int, int, int, int]
        for box in (avatar, name, age, reshared_avatar):
            ImageDraw.Draw(img).rectangle(box, fill="red")
        return img

    d.screenshot = with_header
    assert stories.scrape_stories(d, con) == 1
    assert (
        sqlrows.scalar(con.execute("SELECT posted_date FROM stories")) == "5h"
    )  # read from the header texts
    saved: ImageFile.ImageFile
    with Image.open(next((config.MEDIA_DIR / "stories").iterdir())) as saved:
        frame: Image.Image = saved.convert("RGB")
    box: tuple[int, int, int, int]
    for box in (avatar, name, age, reshared_avatar):
        mean: list[float] = ImageStat.Stat(frame.crop((box[0], box[1] - 150, box[2], box[3] - 150))).mean
        assert abs(mean[0] - mean[1]) < 10, box  # gray like its surroundings, no longer red
