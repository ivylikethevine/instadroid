"""One run end to end (scrape_once), connecting to the device, and the poll loop (main) with its startup wait."""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NoReturn

import adbutils
import igprofiles
import pytest
import uiautomator2 as u2
from instadroid import (
    config,
    db,
    device,
    parsing,
    scrape,
    uidevice,
    versioning,
)

from tests.deviceflows import (
    StopLoop,
    feed_device,
    row,
    rows,
    scalar,
    seed_post,
    stop_after_first_sleep,
    top_card_id,
    values,
)
from tests.fakedevice import FakeDevice

pytestmark = pytest.mark.usefixtures("fast_offline")


def test_scrape_once_end_to_end(fast_offline: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "MAX_CAROUSEL_SLIDES", 3)
    monkeypatch.setattr(config, "STOP_AFTER_SEEN", 1)
    media = fast_offline / "media"
    con = db.db_init()
    d = feed_device()
    old_card = parsing.parse_hierarchy(d.screens["older"])[0]
    seed_post(con, "OLD1", "old_user", "Old caption", 2, h=parsing.post_id(old_card))

    stats = scrape.scrape_once(d, con)

    assert stats == {
        "new": 2,
        "new_stories": 1,
        "link_sheet_failures": 0,
        "link_clipboard_failures": 0,
        "warning": None,
        "filtered_posts": 0,
        "mem_peak_mb": None,  # the fake device has no cgroup files: the memory guard is off
        "oom_kills": None,
        "cards_per_screen": 1.75,  # 2 cards on screen 0, 1 on screen 1 (see the log below)
        "share_captioned": 1.0,
        "share_complete": 1.0,
    }
    posts = {r["id"]: r for r in rows(con, "SELECT * FROM posts")}
    assert set(posts) == {"TOP123", "OTHER1", "OLD1"}
    assert posts["TOP123"]["username"] == "someone_nice" and posts["TOP123"]["kind"] == "video"
    assert posts["TOP123"]["url"] == "https://www.instagram.com/reel/TOP123/"
    assert posts["OTHER1"]["place"] == "Anytown, Somewhere"
    assert posts["OTHER1"]["caption"] == "Second caption"
    assert posts["TOP123"]["ig_version"] == posts["OTHER1"]["ig_version"] == "445.0.0.45.83"
    assert posts["OLD1"]["ig_version"] is None  # seeded before this run; never back-filled
    slides = values(con, "SELECT idx, file FROM media WHERE post_id='OTHER1' ORDER BY idx")
    assert slides == [(1, "OTHER1_1.webp"), (2, "OTHER1_2.webp")]
    for fn in ("TOP123.webp", "OTHER1.webp", "OTHER1_1.webp", "OTHER1_2.webp"):
        assert (media / fn).exists()
        assert (media / fn).read_bytes()[8:12] == b"WEBP"  # the default MEDIA_FORMAT
    assert (media / "avatars" / "other_user.webp").exists()
    assert (media / "avatars" / "old_user.webp").exists()
    stored_stories = rows(con, "SELECT username, media_file FROM stories")
    assert [s["username"] for s in stored_stories] == ["alice"]  # bob's viewer never opened, carol was seen
    story_file = stored_stories[0]["media_file"]
    assert isinstance(story_file, str)
    assert (media / story_file).exists()
    assert d.presses[-1] == "home"
    for pkg in device.CACHED_APP_SWEEP:
        assert f"am force-stop {pkg}" in d.shell_calls
    assert f"am force-stop {config.IG_PKG}" in d.shell_calls


def test_scrape_once_without_permalinks_falls_back_to_hash_ids_and_merges_a_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "MAX_STORIES_PER_RUN", 0)
    monkeypatch.setattr(config, "MAX_CAROUSEL_SLIDES", 1)
    monkeypatch.setattr(config, "MAX_SCROLLS", 2)
    monkeypatch.setattr(config, "PERMALINK_RETRIES", 1)
    monkeypatch.setattr(config, "SHARE_TAP_TRIES", 1)
    con = db.db_init()
    # Stored earlier, before the caption rendered: same author and day, placeholder caption.
    seed_post(con, "placeholder", "someone_nice", "Reel by Someone Nice", 3)
    d = feed_device(top_share="", other_share="")
    d.scroll = {}  # scrolling shows nothing new: only the two cards above are in play

    stats = scrape.scrape_once(d, con)

    assert stats["new"] == 1  # the Reel merged into the placeholder instead of being stored twice
    assert stats["link_sheet_failures"] == 4  # two attempts per card
    merged = row(con, "SELECT * FROM posts WHERE id='placeholder'")
    assert merged is not None
    assert merged["caption"] == "Top card caption…"
    assert merged["ig_version"] == "445.0.0.45.83"  # the placeholder had none; the merge fills it in
    assert merged["media_file"]
    other = row(con, "SELECT * FROM posts WHERE username='other_user'")
    assert other is not None
    assert other["url"] is None and other["id"] == other["hash"]


def test_scrape_once_drops_a_permalink_that_belongs_to_another_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "MAX_STORIES_PER_RUN", 0)
    monkeypatch.setattr(config, "MAX_CAROUSEL_SLIDES", 1)
    monkeypatch.setattr(config, "MAX_SCROLLS", 1)
    con = db.db_init()
    seed_post(con, "TOP123", "someone_else", "Unrelated", 10, h="unrelated")

    scrape.scrape_once(feed_device(), con)

    stored = row(con, "SELECT * FROM posts WHERE username='someone_nice'")
    assert stored is not None
    assert stored["url"] is None and stored["id"] != "TOP123"  # stored under its hash, not the stale link


def test_scrape_once_treats_an_edited_caption_as_the_same_post(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "MAX_STORIES_PER_RUN", 0)
    monkeypatch.setattr(config, "MAX_CAROUSEL_SLIDES", 1)
    monkeypatch.setattr(config, "MAX_SCROLLS", 1)
    con = db.db_init()
    seed_post(con, "TOP123", "someone_nice", "Caption before the edit", 3, h="old-hash")
    d = feed_device()

    stats = scrape.scrape_once(d, con)

    stored = row(con, "SELECT hash FROM posts WHERE id='TOP123'")
    assert stored is not None
    assert stored["hash"] == top_card_id(feed_device(start="following"))  # re-keyed to the new caption
    assert stats["new"] == 1  # only the other card is new
    assert scalar(con, "SELECT COUNT(*) FROM posts WHERE username='someone_nice'") == 1


def fake_adb_connect(addr: str, timeout: float | None = None) -> None:
    pass


def test_connect_device(monkeypatch: pytest.MonkeyPatch) -> None:
    dev = feed_device()
    monkeypatch.setattr(adbutils.adb, "connect", fake_adb_connect)

    def fake_u2_connect(addr: str | None = None) -> FakeDevice:
        return dev

    monkeypatch.setattr(u2, "connect", fake_u2_connect)
    assert device.connect_device() is dev
    assert (
        versioning.PROFILE.name == "v424" and versioning.PROFILE_WARNING is None
    )  # device reports 445.0.0.45.83


def test_connect_device_warns_about_an_installed_version_nobody_has_validated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dev = feed_device()
    dev.ig_version = "999.0.0.1.1"
    monkeypatch.setattr(config, "IG_PROFILE", "")
    monkeypatch.setattr(adbutils.adb, "connect", fake_adb_connect)

    def fake_u2_connect(addr: str | None = None) -> FakeDevice:
        return dev

    monkeypatch.setattr(u2, "connect", fake_u2_connect)
    device.connect_device()
    assert versioning.PROFILE.name == igprofiles.available()[-1]  # the newest profile covers it
    assert (versioning.PROFILE_WARNING or "").startswith(
        f"Instagram 999.0.0.1.1 hasn't been validated with profile {versioning.PROFILE.name}"
    )


def test_scrape_once_reports_the_profile_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "MAX_STORIES_PER_RUN", 0)
    monkeypatch.setattr(config, "MAX_SCROLLS", 1)
    monkeypatch.setattr(
        versioning, "PROFILE_WARNING", "Instagram 999.0.0.1.1 hasn't been validated with profile v424"
    )
    stats = scrape.scrape_once(feed_device(), db.db_init())
    assert stats["warning"] is not None
    assert "Instagram 999.0.0.1.1 hasn't been validated with profile v424" in stats["warning"]


def test_main_records_a_transient_failure_and_retries_early(
    fast_offline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleeps = stop_after_first_sleep(monkeypatch)
    monkeypatch.setattr(config, "RETRY_DELAYS_MINUTES", [2.0])

    def offline() -> NoReturn:
        raise adbutils.AdbError("device 127.0.0.1:5555 not online")

    monkeypatch.setattr(device, "connect_device", offline)

    with pytest.raises(StopLoop):
        scrape.main()

    error = values(sqlite3.connect(fast_offline / "posts.sqlite"), "SELECT error FROM runs")[0][0]
    assert isinstance(error, str) and error.startswith("AdbError")
    assert 2 * 60 <= sleeps[0] <= 3 * 60


def test_main_records_a_successful_run_with_device_versions(
    fast_offline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleeps = stop_after_first_sleep(monkeypatch)
    monkeypatch.setattr(device, "connect_device", feed_device)
    stats: dict[str, int | str] = {
        "new": 2,
        "new_stories": 1,
        "link_sheet_failures": 1,
        "link_clipboard_failures": 0,
        "warning": "w",
    }

    def fake_scrape_once(d: uidevice.Device, con: sqlite3.Connection) -> dict[str, int | str]:
        return stats

    monkeypatch.setattr(scrape, "scrape_once", fake_scrape_once)

    with pytest.raises(StopLoop):
        scrape.main()

    con = sqlite3.connect(fast_offline / "posts.sqlite")
    con.row_factory = sqlite3.Row
    run = rows(con, "SELECT * FROM runs")[0]
    assert (run["new_posts"], run["new_stories"], run["warning"], run["error"]) == (2, 1, "w", None)
    assert (run["android_release"], run["ig_version"]) == ("13", "445.0.0.45.83")
    assert run["selector_profile"] == "v424"
    assert config.POLL_MIN_H * 3600 <= sleeps[0] <= config.POLL_MAX_H * 3600


# --- startup wait ---------------------------------------------------------------------------------


def _record_last_run(con: sqlite3.Connection, minutes_ago: float, error: str | None = None) -> None:
    finished = (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat()
    db.record_run(con, finished, finished, 0, error, {})


@pytest.fixture
def poll_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "POLL_MIN_H", 2.5)
    monkeypatch.setattr(config, "POLL_MAX_H", 4.5)
    monkeypatch.setattr(config, "TIME_DISTRIBUTION", "uniform")
    monkeypatch.setattr(config, "RETRY_DELAYS_MINUTES", [2.0, 5.0])
    monkeypatch.setattr(config, "SCRAPE_ON_STARTUP", False)


def test_startup_scrapes_immediately_with_no_recorded_run(poll_window: None) -> None:
    assert scrape._startup_wait_seconds(db.db_init()) == 0


def test_startup_waits_out_the_rest_of_the_poll_interval(poll_window: None) -> None:
    con = db.db_init()
    _record_last_run(con, minutes_ago=60)
    wait = scrape._startup_wait_seconds(con)
    assert 1.5 * 3600 - 5 <= wait <= 3.5 * 3600


def test_startup_does_not_wait_when_the_last_run_is_old(poll_window: None) -> None:
    con = db.db_init()
    _record_last_run(con, minutes_ago=5 * 60)
    assert scrape._startup_wait_seconds(con) == 0


@pytest.mark.parametrize(
    "error",
    [
        "DeviceNotReady('redroid still booting')",
        "LaunchUiAutomationError('server quit')",
        "AdbError('offline')",
    ],
)
def test_startup_after_a_transient_failure_waits_only_for_the_first_retry(
    poll_window: None, error: str
) -> None:
    con = db.db_init()
    _record_last_run(con, minutes_ago=0.5, error=error)
    assert 85 <= scrape._startup_wait_seconds(con) <= 90  # 2 minutes, minus the 30s already passed


def test_startup_after_a_non_transient_failure_waits_a_full_interval(poll_window: None) -> None:
    con = db.db_init()
    _record_last_run(
        con, minutes_ago=1, error="RuntimeError(\"Instagram wants a human: 'Confirm it's you'\")"
    )
    assert scrape._startup_wait_seconds(con) >= 2.5 * 3600 - 65


def test_scrape_on_startup_skips_the_wait(poll_window: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "SCRAPE_ON_STARTUP", True)
    con = db.db_init()
    _record_last_run(con, minutes_ago=1)
    assert scrape._startup_wait_seconds(con) == 0


def test_main_waits_before_its_first_scrape(fast_offline: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps = stop_after_first_sleep(monkeypatch)

    def fixed_wait(con: sqlite3.Connection, now: datetime | None = None) -> float:
        return 123.0

    monkeypatch.setattr(scrape, "_startup_wait_seconds", fixed_wait)
    connects: list[int] = []
    monkeypatch.setattr(device, "connect_device", lambda: connects.append(1))
    with pytest.raises(StopLoop):
        scrape.main()
    assert sleeps == [123.0] and connects == []  # slept first, never connected
