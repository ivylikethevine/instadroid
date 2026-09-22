"""One run end to end (scrape_once), connecting to the device, and the poll loop (main) with its startup wait."""

import sqlite3
import urllib.request
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from typing import NoReturn

import adbutils
import igprofiles
import pytest
import uiautomator2 as u2
from instadroid import (
    alerts,
    backup,
    config,
    control,
    db,
    device,
    navigation,
    parsing,
    scrape,
    stories,
    tune,
    uidevice,
    versioning,
)
from shared import sqlrows
from shared.sqlrows import SqlValue

from tests.deviceflows import (
    CAPTION,
    TOP_URL,
    StopLoop,
    feed_device,
    following_screen,
    seed_post,
    stop_after_first_sleep,
    top_card_id,
)
from tests.fakedevice import FakeDevice, Node, node
from tests.support import fetch_row, record_run_ago, row_dict

pytestmark: pytest.MarkDecorator = pytest.mark.usefixtures("fast_offline")


def test_scrape_once_end_to_end(fast_offline: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "MAX_CAROUSEL_SLIDES", 3)
    monkeypatch.setattr(config, "STOP_AFTER_SEEN", 1)
    media: Path = fast_offline / "media"
    con: sqlite3.Connection = db.db_init()
    d: FakeDevice = feed_device()
    old_card: parsing.Post = parsing.parse_hierarchy(d.screens["older"])[0]
    seed_post(con, "OLD1", "old_user", "Old caption", 2, h=parsing.post_id(old_card))

    stats: scrape.RunStats = scrape.scrape_once(d, con)

    assert stats == {
        "new": 2,
        "metrics": {
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
        },
    }
    posts: dict[str, dict[str, SqlValue]] = {
        sqlrows.must_str(r, "id"): row_dict(r) for r in sqlrows.fetch_all(con.execute("SELECT * FROM posts"))
    }
    assert set(posts) == {"TOP123", "OTHER1", "OLD1"}
    assert posts["TOP123"]["username"] == "someone_nice" and posts["TOP123"]["kind"] == "video"
    assert posts["TOP123"]["url"] == "https://www.instagram.com/reel/TOP123/"
    assert posts["OTHER1"]["place"] == "Anytown, Somewhere"
    assert posts["OTHER1"]["caption"] == "Second caption"
    assert posts["TOP123"]["ig_version"] == posts["OTHER1"]["ig_version"] == "445.0.0.45.83"
    assert posts["OLD1"]["ig_version"] is None  # seeded before this run; never back-filled
    slides: list[tuple[SqlValue, ...]] = [
        sqlrows.values(r)
        for r in sqlrows.fetch_all(
            con.execute("SELECT idx, file FROM media WHERE post_id='OTHER1' ORDER BY idx")
        )
    ]
    assert slides == [(1, "OTHER1_1.webp"), (2, "OTHER1_2.webp")]
    fn: str
    for fn in ("TOP123.webp", "OTHER1.webp", "OTHER1_1.webp", "OTHER1_2.webp"):
        assert (media / fn).exists()
        assert (media / fn).read_bytes()[8:12] == b"WEBP"  # the default MEDIA_FORMAT
    assert (media / "avatars" / "other_user.webp").exists()
    assert (media / "avatars" / "old_user.webp").exists()
    stored_stories: list[dict[str, SqlValue]] = [
        row_dict(r) for r in sqlrows.fetch_all(con.execute("SELECT username, media_file FROM stories"))
    ]
    assert [s["username"] for s in stored_stories] == ["alice"]  # bob's viewer never opened, carol was seen
    story_file: SqlValue = stored_stories[0]["media_file"]
    assert isinstance(story_file, str)
    assert (media / story_file).exists()
    assert d.presses[-1] == "home"
    pkg: str
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
    con: sqlite3.Connection = db.db_init()
    # Stored earlier, before the caption rendered: same author and day, placeholder caption.
    seed_post(con, "placeholder", "someone_nice", "Reel by Someone Nice", 3)
    d: FakeDevice = feed_device(top_share="", other_share="")
    d.scroll = {}  # scrolling shows nothing new: only the two cards above are in play

    stats: scrape.RunStats = scrape.scrape_once(d, con)

    assert stats["new"] == 1  # the Reel merged into the placeholder instead of being stored twice
    assert stats["metrics"].get("link_sheet_failures") == 4  # two attempts per card
    merged: dict[str, SqlValue] = row_dict(
        fetch_row(con.execute("SELECT * FROM posts WHERE id='placeholder'"))
    )
    assert merged["caption"] == "Top card caption…"
    assert merged["ig_version"] == "445.0.0.45.83"  # the placeholder had none; the merge fills it in
    assert merged["media_file"]
    other: dict[str, SqlValue] = row_dict(
        fetch_row(con.execute("SELECT * FROM posts WHERE username='other_user'"))
    )
    assert other["url"] is None and other["id"] == other["hash"]


def test_scrape_once_drops_a_permalink_that_belongs_to_another_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "MAX_STORIES_PER_RUN", 0)
    monkeypatch.setattr(config, "MAX_CAROUSEL_SLIDES", 1)
    monkeypatch.setattr(config, "MAX_SCROLLS", 1)
    con: sqlite3.Connection = db.db_init()
    seed_post(con, "TOP123", "someone_else", "Unrelated", 10, h="unrelated")

    scrape.scrape_once(feed_device(), con)

    stored: dict[str, SqlValue] = row_dict(
        fetch_row(con.execute("SELECT * FROM posts WHERE username='someone_nice'"))
    )
    assert stored["url"] is None and stored["id"] != "TOP123"  # stored under its hash, not the stale link


def test_scrape_once_treats_an_edited_caption_as_the_same_post(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "MAX_STORIES_PER_RUN", 0)
    monkeypatch.setattr(config, "MAX_CAROUSEL_SLIDES", 1)
    monkeypatch.setattr(config, "MAX_SCROLLS", 1)
    con: sqlite3.Connection = db.db_init()
    seed_post(con, "TOP123", "someone_nice", "Caption before the edit", 3, h="old-hash")
    d: FakeDevice = feed_device()

    stats: scrape.RunStats = scrape.scrape_once(d, con)

    stored: sqlite3.Row = fetch_row(con.execute("SELECT hash FROM posts WHERE id='TOP123'"))
    assert sqlrows.cell(stored, "hash") == top_card_id(
        feed_device(start="following")
    )  # re-keyed to the new caption
    assert stats["new"] == 1  # only the other card is new
    assert sqlrows.scalar(con.execute("SELECT COUNT(*) FROM posts WHERE username='someone_nice'")) == 1


def fake_adb_connect(addr: str, timeout: float | None = None) -> None:
    pass


def test_connect_device(monkeypatch: pytest.MonkeyPatch) -> None:
    dev: FakeDevice = feed_device()
    monkeypatch.setattr(adbutils.adb, "connect", fake_adb_connect)

    def fake_u2_connect(addr: str | None = None) -> FakeDevice:
        return dev

    monkeypatch.setattr(u2, "connect", fake_u2_connect)
    booted: list[str] = []

    def fake_wait(addr: str, timeout: float) -> bool:
        booted.append(addr)
        return True

    monkeypatch.setattr(tune, "wait_for_boot", fake_wait)
    monkeypatch.setattr(tune, "_tuned", False)
    assert device.connect_device() is dev
    assert booted == [config.ADB_ADDR] and any("pm disable-user" in c for c in dev.shell_calls)  # tuned
    assert (
        versioning.PROFILE.name == "v424" and versioning.PROFILE_WARNING is None
    )  # device reports 445.0.0.45.83


def test_connect_device_warns_about_an_installed_version_nobody_has_validated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dev: FakeDevice = feed_device()
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
    warning: str | None = scrape.scrape_once(feed_device(), db.db_init())["metrics"].get("warning")
    assert warning is not None
    assert "Instagram 999.0.0.1.1 hasn't been validated with profile v424" in warning


def test_main_records_a_transient_failure_and_retries_early(
    fast_offline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleeps: list[float] = stop_after_first_sleep(monkeypatch)
    monkeypatch.setattr(config, "RETRY_DELAYS_MINUTES", [2.0])

    def offline() -> NoReturn:
        raise adbutils.AdbError("device 127.0.0.1:5555 not online")

    monkeypatch.setattr(device, "connect_device", offline)

    with pytest.raises(StopLoop):
        scrape.main()

    error: SqlValue = sqlrows.scalar(
        sqlite3.connect(fast_offline / "posts.sqlite").execute("SELECT error FROM runs")
    )
    assert isinstance(error, str) and error.startswith("AdbError")
    assert 2 * 60 <= sleeps[0] <= 3 * 60


def test_main_counts_earlier_failures_from_the_runs_table(
    fast_offline: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A restart doesn't reset the backoff: two failed runs already recorded plus this one is three."""
    sleeps: list[float] = stop_after_first_sleep(monkeypatch)
    monkeypatch.setattr(config, "POLL_MIN_H", 3.0)
    monkeypatch.setattr(config, "POLL_MAX_H", 3.0)
    monkeypatch.setattr(config, "FAILURE_BACKOFF_MAX_HOURS", 24.0)
    monkeypatch.setattr(config, "SCRAPE_ON_STARTUP", True)  # the startup wait backs off too; not under test
    con: sqlite3.Connection = db.db_init()
    record_run_ago(con, 600, "RuntimeError('no posts parsed')")
    record_run_ago(con, 300, "RuntimeError('no posts parsed')")
    assert db.consecutive_failures(con) == 2
    con.close()

    def broken() -> NoReturn:
        raise RuntimeError("no posts parsed")

    monkeypatch.setattr(device, "connect_device", broken)
    with pytest.raises(StopLoop):
        scrape.main()
    assert sleeps == [12.0 * 3600]  # third failure in a row: 4x the 3h interval
    assert "3 failed runs in a row; backing off" in capsys.readouterr().out


def test_the_daily_budget_holds_the_loop_until_the_oldest_run_ages_out(
    fast_offline: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sleeps: list[float] = stop_after_first_sleep(monkeypatch)
    monkeypatch.setattr(config, "MAX_RUNS_PER_DAY", 3)
    monkeypatch.setattr(config, "SCRAPE_ON_STARTUP", True)
    con: sqlite3.Connection = db.db_init()
    minutes: int
    for minutes in (23 * 60, 12 * 60, 60):
        record_run_ago(con, minutes)
    assert scrape.budget_wait_seconds(con) == pytest.approx(3600 + 1, abs=5)  # the 23h-old one ages out in 1h
    record_run_ago(con, 25 * 60)  # older than a day: not counted
    assert scrape.budget_wait_seconds(con) == pytest.approx(3600 + 1, abs=5)
    monkeypatch.setattr(config, "MAX_RUNS_PER_DAY", 4)
    assert scrape.budget_wait_seconds(con) == 0  # under the ceiling
    monkeypatch.setattr(config, "MAX_RUNS_PER_DAY", 3)
    con.close()
    connects: list[int] = []
    monkeypatch.setattr(device, "connect_device", lambda: connects.append(1))
    with pytest.raises(StopLoop):
        scrape.main()
    assert connects == [] and len(sleeps) == 1 and 3590 <= sleeps[0] <= 3610
    assert "3 runs started in the last 24h (MAX_RUNS_PER_DAY)" in capsys.readouterr().out
    monkeypatch.setattr(config, "MAX_RUNS_PER_DAY", 0)
    with db.db_init() as con:
        assert scrape.budget_wait_seconds(con) == 0  # disabled


def test_main_records_a_successful_run_with_device_versions(
    fast_offline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleeps: list[float] = stop_after_first_sleep(monkeypatch)
    monkeypatch.setattr(device, "connect_device", feed_device)
    stats: scrape.RunStats = {
        "new": 2,
        "metrics": {"new_stories": 1, "link_sheet_failures": 1, "link_clipboard_failures": 0, "warning": "w"},
    }

    def fake_scrape_once(d: uidevice.Device, con: sqlite3.Connection) -> scrape.RunStats:
        return stats

    monkeypatch.setattr(scrape, "scrape_once", fake_scrape_once)

    with pytest.raises(StopLoop):
        scrape.main()

    con: sqlite3.Connection = sqlite3.connect(fast_offline / "posts.sqlite")
    run: dict[str, SqlValue] = row_dict(fetch_row(con.execute("SELECT * FROM runs")))
    assert (run["new_posts"], run["new_stories"], run["warning"], run["error"]) == (2, 1, "w", None)
    assert (run["android_release"], run["ig_version"]) == ("13", "445.0.0.45.83")
    assert run["selector_profile"] == "v424"
    assert config.POLL_MIN_H * 3600 <= sleeps[0] <= config.POLL_MAX_H * 3600


# --- startup wait ---------------------------------------------------------------------------------


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
    con: sqlite3.Connection = db.db_init()
    record_run_ago(con, 60)
    wait: float = scrape._startup_wait_seconds(con)
    assert 1.5 * 3600 - 5 <= wait <= 3.5 * 3600


def test_startup_does_not_wait_when_the_last_run_is_old(poll_window: None) -> None:
    con: sqlite3.Connection = db.db_init()
    record_run_ago(con, 5 * 60)
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
    con: sqlite3.Connection = db.db_init()
    record_run_ago(con, 0.5, error=error)
    assert 85 <= scrape._startup_wait_seconds(con) <= 90  # 2 minutes, minus the 30s already passed


def test_startup_after_a_non_transient_failure_waits_a_full_interval(poll_window: None) -> None:
    con: sqlite3.Connection = db.db_init()
    record_run_ago(con, 1, error="RuntimeError(\"Instagram wants a human: 'Confirm it's you'\")")
    assert scrape._startup_wait_seconds(con) >= 2.5 * 3600 - 65


def test_scrape_on_startup_skips_the_wait(poll_window: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "SCRAPE_ON_STARTUP", True)
    con: sqlite3.Connection = db.db_init()
    record_run_ago(con, 1)
    assert scrape._startup_wait_seconds(con) == 0


def test_main_waits_before_its_first_scrape(fast_offline: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = stop_after_first_sleep(monkeypatch)

    def fixed_wait(con: sqlite3.Connection, now: datetime | None = None) -> float:
        return 123.0

    monkeypatch.setattr(scrape, "_startup_wait_seconds", fixed_wait)
    connects: list[int] = []
    monkeypatch.setattr(device, "connect_device", lambda: connects.append(1))
    with pytest.raises(StopLoop):
        scrape.main()
    assert sleeps == [123.0] and connects == []  # slept first, never connected


# --- surprises one run survives -------------------------------------------------------------------


def _one_screen(monkeypatch: pytest.MonkeyPatch) -> None:
    """A run over the first Following screen only: no stories, one slide per carousel, no scroll."""
    monkeypatch.setattr(config, "MAX_STORIES_PER_RUN", 0)
    monkeypatch.setattr(config, "MAX_CAROUSEL_SLIDES", 1)
    monkeypatch.setattr(config, "MAX_SCROLLS", 1)


def test_startup_wait_is_zero_for_an_unreadable_runs_table_or_finish_time(poll_window: None) -> None:
    assert scrape._startup_wait_seconds(sqlite3.connect(":memory:")) == 0  # no runs table at all
    con: sqlite3.Connection = db.db_init()
    db.record_run(con, "2026-09-14T12:00:00+00:00", "not a timestamp", 0, None, {})
    assert scrape._startup_wait_seconds(con) == 0


def test_a_failed_following_refresh_does_not_sink_the_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _one_screen(monkeypatch)
    monkeypatch.setattr(config, "FOLLOWING_REFRESH_DAYS", 7)

    def never_opens(d: uidevice.Device, con: sqlite3.Connection) -> NoReturn:
        raise RuntimeError("Following list never opened")

    monkeypatch.setattr(navigation, "refresh_following_list", never_opens)
    stats: scrape.RunStats = scrape.scrape_once(feed_device(), db.db_init())
    assert stats["new"] == 2  # no stored list yet, so nothing is filtered
    assert "following-list refresh failed" in capsys.readouterr().out


def test_a_failed_story_capture_does_not_sink_the_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(config, "MAX_CAROUSEL_SLIDES", 1)
    monkeypatch.setattr(config, "MAX_SCROLLS", 1)

    def viewer_crashed(d: uidevice.Device, con: sqlite3.Connection) -> NoReturn:
        raise RuntimeError("story viewer never opened")

    monkeypatch.setattr(stories, "scrape_stories", viewer_crashed)
    stats: scrape.RunStats = scrape.scrape_once(feed_device(), db.db_init())
    assert stats["new"] == 2 and stats["metrics"].get("new_stories") == 0
    assert "story capture failed" in capsys.readouterr().out


def test_a_failed_backup_is_a_warning_not_a_failed_run(monkeypatch: pytest.MonkeyPatch) -> None:
    _one_screen(monkeypatch)

    def disk_full(con: sqlite3.Connection, force: bool = False, now: datetime | None = None) -> NoReturn:
        raise OSError("No space left on device")

    monkeypatch.setattr(backup, "backup_database", disk_full)
    warning: str | None = scrape.scrape_once(feed_device(), db.db_init())["metrics"].get("warning")
    assert warning is not None and "database backup failed: No space left on device" in warning


def test_an_unreachable_reader_is_a_warning_not_a_failed_run(monkeypatch: pytest.MonkeyPatch) -> None:
    _one_screen(monkeypatch)
    monkeypatch.setattr(config, "FRESHRSS_REFRESH_URL", "http://127.0.0.1:9/i/?c=feed&a=actualize&token=t")

    def refused(request: str | urllib.request.Request, timeout: float) -> NoReturn:
        raise OSError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", refused)
    stats: scrape.RunStats = scrape.scrape_once(feed_device(), db.db_init())
    warning: str | None = stats["metrics"].get("warning")
    assert stats["new"] == 2 and warning is not None
    assert "FreshRSS refresh ping to http://127.0.0.1:9/i/ failed" in warning and "token" not in warning


def test_run_recorded_survives_an_alert_bookkeeping_failure(
    fast_offline: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(device, "connect_device", feed_device)
    stats: scrape.RunStats = {"new": 0, "metrics": {}}

    def fake_scrape_once(d: uidevice.Device, con: sqlite3.Connection) -> scrape.RunStats:
        return stats

    def table_locked(con: sqlite3.Connection, now: datetime | None = None) -> NoReturn:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(scrape, "scrape_once", fake_scrape_once)
    monkeypatch.setattr(alerts, "update", table_locked)
    con: sqlite3.Connection = db.db_init()
    assert scrape.run_recorded(con) == (stats, None)
    assert sqlrows.scalar(con.execute("SELECT COUNT(*) FROM runs")) == 1  # the run was still recorded
    assert "could not update alerts" in capsys.readouterr().out


def test_the_loop_rechecks_the_lock_and_budget_after_a_budget_wait(
    fast_offline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    budgets: Iterator[float] = iter([10.0, 0.0])  # over budget once, then clear

    def budget(con: sqlite3.Connection, now: datetime | None = None) -> float:
        return next(budgets)

    def no_startup_wait(con: sqlite3.Connection, now: datetime | None = None) -> float:
        return 0.0

    waits: list[float] = []

    def wait(con: sqlite3.Connection, seconds: float) -> None:
        waits.append(seconds)

    def run_recorded(con: sqlite3.Connection) -> NoReturn:
        raise StopLoop

    monkeypatch.setattr(scrape, "budget_wait_seconds", budget)
    monkeypatch.setattr(scrape, "_startup_wait_seconds", no_startup_wait)
    monkeypatch.setattr(control, "wait", wait)
    monkeypatch.setattr(scrape, "run_recorded", run_recorded)
    with pytest.raises(StopLoop):
        scrape.main()
    assert waits == [10.0]  # waited the budget out once, re-checked, then ran


def test_a_share_sheet_that_leaves_the_feed_reopens_it_and_keeps_the_post(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _one_screen(monkeypatch)
    d: FakeDevice = feed_device()
    # Copy link works, but tapping it lands on the Home feed instead of back on Following.
    d.screens["share_top"] = following_screen(
        sheet=node(desc="Copy link", bounds=(0, 2240, 1080, 2330), clip=TOP_URL, goto="home")
    )
    con: sqlite3.Connection = db.db_init()
    stats: scrape.RunStats = scrape.scrape_once(d, con)
    assert stats["new"] == 2
    assert sqlrows.scalar(con.execute("SELECT COUNT(*) FROM posts WHERE id='TOP123'")) == 1  # stored once
    assert "not on the chrono feed any more; reopening it" in capsys.readouterr().out


def _media_less_card() -> list[Node]:
    """A card whose media node never rendered: header, share button and caption only."""
    return [
        node(
            "row_feed_profile_header", desc="text_user posted a photo 2 days ago", bounds=(0, 300, 1080, 437)
        ),
        node("row_feed_button_share", bounds=(390, 1500, 453, 1621), goto="share_top"),
        node(cls=CAPTION, text="text_user Words only", bounds=(32, 1630, 1080, 1700)),
    ]


def test_a_card_without_a_media_node_is_stored_without_media_and_dumped(
    fast_offline: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _one_screen(monkeypatch)
    d: FakeDevice = feed_device()
    d.screens["following"] = following_screen(_media_less_card())
    con: sqlite3.Connection = db.db_init()
    assert scrape.scrape_once(d, con)["new"] == 1
    assert (fast_offline / "debug" / "no_media_node_hierarchy.xml").exists()
    assert row_dict(fetch_row(con.execute("SELECT id, media_file FROM posts"))) == {
        "id": "TOP123",
        "media_file": None,
    }
    assert "media node not found" in capsys.readouterr().out


def test_a_card_whose_bottom_is_off_screen_waits_for_a_later_dump(monkeypatch: pytest.MonkeyPatch) -> None:
    _one_screen(monkeypatch)
    d: FakeDevice = feed_device()
    d.screens["following"] = following_screen(
        [
            node(
                "row_feed_profile_header",
                desc="old_user posted a photo 2 days ago",
                bounds=(0, 300, 1080, 437),
            ),
            node("media_group", bounds=(0, 437, 1080, 2235)),
            node("row_feed_photo_imageview", desc="Photo by Old User, 5 likes", bounds=(0, 437, 1080, 2235)),
        ]  # no share button yet: the card's identity isn't stable
    )
    d.scroll = {}
    con: sqlite3.Connection = db.db_init()
    stats: scrape.RunStats = scrape.scrape_once(d, con)
    assert stats["new"] == 0 and stats["metrics"].get("share_complete") == 0.0
    assert sqlrows.scalar(con.execute("SELECT COUNT(*) FROM posts")) == 0
    assert sqlrows.scalar(con.execute("SELECT COUNT(*) FROM accounts WHERE username='old_user'")) == 1


def test_a_card_the_profile_cannot_attribute_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """parse_hierarchy() itself drops username-less cards, but a profile override may not."""
    _one_screen(monkeypatch)
    root: igprofiles.BaseProfile = igprofiles.load("v424")

    class Unattributed(type(root)):
        def parse_hierarchy(self, base: Callable[[str], list[parsing.Post]], xml: str) -> list[parsing.Post]:
            posts: list[parsing.Post] = base(xml)
            if posts:
                posts.append({**posts[0], "username": "", "complete": False})
            return posts

    monkeypatch.setattr(versioning, "PROFILE", Unattributed())
    con: sqlite3.Connection = db.db_init()
    assert scrape.scrape_once(feed_device(), con)["new"] == 2
    assert sqlrows.scalar(con.execute("SELECT COUNT(*) FROM accounts WHERE username=''")) == 0
