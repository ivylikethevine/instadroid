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
from PIL import Image, ImageOps
from shared import sqlrows

from tests.deviceflows import feed_device, following_screen
from tests.fakedevice import HEIGHT, WIDTH, FakeDevice, hierarchy

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
