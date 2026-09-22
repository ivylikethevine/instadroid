import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import adbutils
import pytest
from instadroid import config, db, device, diagnostics, navigation, scrape, stories
from instadroid.uidevice import Device
from uiautomator2.exceptions import HTTPError, LaunchUiAutomationError, UiObjectNotFoundError

from tests.fakedevice import FakeDevice

# A feed list with nothing identifiable in it: parse_hierarchy() returns [] for this.
EMPTY_XML: str = '<hierarchy><node resource-id="android:id/list" bounds="[0,0][1080,2340]" /></hierarchy>'


@pytest.fixture
def con(con: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    monkeypatch.setattr(config, "DEBUG_DIR", tmp_path / "debug")
    return con


class EmptyFeedDevice(FakeDevice):
    """Every dump is the empty feed list, however the scraper navigates."""

    def __init__(self) -> None:
        super().__init__({"feed": EMPTY_XML}, "feed")
        self.dumps = 0

    def dump_hierarchy(self) -> str:
        self.dumps += 1
        return EMPTY_XML


@dataclass
class OfflineCalls:
    open_feed: int = 0
    dumps: list[str] = field(default_factory=list[str])
    scrolls: int = 0


@pytest.fixture
def offline_scrape(monkeypatch: pytest.MonkeyPatch) -> OfflineCalls:
    """Stub out everything in scrape_once() that navigates, so only the scroll loop runs."""
    calls: OfflineCalls = OfflineCalls()

    def open_feed(d: Device) -> None:
        calls.open_feed += 1

    def scrape_stories(d: Device, con: sqlite3.Connection) -> int:
        return 0

    def scroll(d: Device) -> None:
        calls.scrolls += 1

    def human_pause(lo: float = 1.0, hi: float = 3.0) -> None:
        pass

    def dump_debug(d: Device, name: str, xml: str | None = None) -> None:
        calls.dumps.append(name)

    monkeypatch.setattr(navigation, "open_following_feed", open_feed)
    monkeypatch.setattr(stories, "scrape_stories", scrape_stories)
    monkeypatch.setattr(device, "human_scroll", scroll)
    monkeypatch.setattr(device, "human_pause", human_pause)
    monkeypatch.setattr(diagnostics, "dump_debug", dump_debug)
    return calls


def test_empty_screens_reopen_the_feed_once_then_stop_the_run(
    con: sqlite3.Connection, offline_scrape: OfflineCalls, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "MAX_SCROLLS", 25)
    monkeypatch.setattr(config, "EMPTY_SCREEN_LIMIT", 3)
    d: EmptyFeedDevice = EmptyFeedDevice()

    stats: scrape.RunStats = scrape.scrape_once(d, con)

    assert d.dumps == 6  # 3 empty screens, reopen, 3 more, stop — not all 25
    assert offline_scrape.open_feed == 2  # the initial open plus exactly one reopen
    assert offline_scrape.dumps == ["last", "empty_feed0", "empty_feed1"]
    warning: str | None = stats["metrics"].get("warning")
    assert warning is not None
    assert "reopened the feed" in warning
    assert "stopped early" in warning
    assert d.presses[-1] == "home"  # still leaves the app in a natural state


def test_empty_screen_guard_can_be_disabled(
    con: sqlite3.Connection, offline_scrape: OfflineCalls, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "MAX_SCROLLS", 5)
    monkeypatch.setattr(config, "EMPTY_SCREEN_LIMIT", 0)
    d: EmptyFeedDevice = EmptyFeedDevice()

    stats: scrape.RunStats = scrape.scrape_once(d, con)

    assert d.dumps == 5
    assert offline_scrape.open_feed == 1
    assert stats["metrics"].get("warning") is None


def test_scrape_stats_report_cards_per_screen_and_shares(
    con: sqlite3.Connection, offline_scrape: OfflineCalls, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "MAX_SCROLLS", 3)
    monkeypatch.setattr(config, "EMPTY_SCREEN_LIMIT", 0)
    d: EmptyFeedDevice = EmptyFeedDevice()

    metrics: db.RunMetrics = scrape.scrape_once(d, con)["metrics"]

    assert metrics.get("cards_per_screen") == 0.0
    assert metrics.get("share_captioned") == 0.0
    assert metrics.get("share_complete") == 0.0


def test_scrape_once_flags_selector_drift_against_seeded_baseline(
    con: sqlite3.Connection, offline_scrape: OfflineCalls, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "MAX_SCROLLS", 3)
    monkeypatch.setattr(config, "EMPTY_SCREEN_LIMIT", 0)
    for _ in range(5):
        db.record_run(
            con,
            "2026-09-11T00:00:00+00:00",
            "2026-09-11T00:05:00+00:00",
            0,
            None,
            {},
            cards_per_screen=4.0,
            share_captioned=0.8,
            share_complete=0.9,
        )
    d: EmptyFeedDevice = EmptyFeedDevice()  # every dump parses 0 cards — a stand-in for selectors gone stale

    warning: str | None = scrape.scrape_once(d, con)["metrics"].get("warning")
    assert warning is not None and "selector drift?" in warning


def test_record_run_stores_a_warning(con: sqlite3.Connection) -> None:
    db.record_run(con, "2026-09-11T00:00:00+00:00", "2026-09-11T00:05:00+00:00", 0, None, {}, warning="w")
    assert con.execute("SELECT warning FROM runs").fetchone()[0] == "w"


@pytest.mark.parametrize(
    "exc",
    [
        device.DeviceNotReady("could not bring com.instagram.android to the foreground"),
        adbutils.AdbError("device 127.0.0.1:5555 not online"),
        LaunchUiAutomationError("server quit unexpectly"),
        HTTPError("uiautomator jsonrpc unreachable"),
    ],
)
def test_device_failures_are_transient(exc: BaseException) -> None:
    assert device.is_transient(exc)


@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("Instagram wants a human: 'Confirm it's you' screen"),
        RuntimeError("still on login screen after submit (wrong password?)"),
        UiObjectNotFoundError("selector drifted"),
        ValueError("parse bug"),
    ],
)
def test_challenges_and_logic_errors_are_not_transient(exc: BaseException) -> None:
    assert not device.is_transient(exc)


def test_transient_failures_retry_on_the_short_schedule_then_fall_back_to_polling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TIME_DISTRIBUTION", "uniform")
    monkeypatch.setattr(config, "RETRY_DELAYS_MINUTES", [2.0, 5.0])
    monkeypatch.setattr(config, "POLL_MIN_H", 2.5)
    monkeypatch.setattr(config, "POLL_MAX_H", 4.5)
    err: adbutils.AdbError = adbutils.AdbError("offline")

    attempt: int
    seconds: float
    seconds, attempt = scrape.next_sleep_seconds(err, 0)
    assert attempt == 1 and 2 * 60 <= seconds <= 3 * 60
    seconds, attempt = scrape.next_sleep_seconds(err, attempt)
    assert attempt == 2 and 5 * 60 <= seconds <= 7.5 * 60
    seconds, attempt = scrape.next_sleep_seconds(err, attempt)  # retries exhausted
    assert attempt == 0 and 2.5 * 3600 <= seconds <= 4.5 * 3600


@pytest.mark.parametrize("exc", [None, RuntimeError("Instagram wants a human: 'Enter the code' screen")])
def test_success_and_challenges_sleep_a_normal_poll_interval(
    exc: BaseException | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "TIME_DISTRIBUTION", "uniform")
    monkeypatch.setattr(config, "RETRY_DELAYS_MINUTES", [2.0])
    attempt: int
    seconds: float
    seconds, attempt = scrape.next_sleep_seconds(exc, 0)
    assert attempt == 0 and seconds >= config.POLL_MIN_H * 3600


def test_repeated_failures_widen_the_poll_interval_up_to_the_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "TIME_DISTRIBUTION", "uniform")
    monkeypatch.setattr(config, "RETRY_DELAYS_MINUTES", [])
    monkeypatch.setattr(config, "POLL_MIN_H", 3.0)
    monkeypatch.setattr(config, "POLL_MAX_H", 3.0)
    monkeypatch.setattr(config, "FAILURE_BACKOFF_MAX_HOURS", 24.0)
    err: RuntimeError = RuntimeError("no posts parsed")
    hours: list[float] = [scrape.next_sleep_seconds(err, 0, n)[0] / 3600 for n in (1, 2, 3, 4, 5, 40)]
    assert hours == [3.0, 6.0, 12.0, 24.0, 24.0, 24.0]  # 1x, 2x, 4x, then the cap
    assert scrape.next_sleep_seconds(None, 0, 0)[0] / 3600 == 3.0  # success: back to normal
    monkeypatch.setattr(config, "FAILURE_BACKOFF_MAX_HOURS", 0)
    assert scrape.next_sleep_seconds(err, 0, 5)[0] / 3600 == 3.0  # 0 disables
    # A transient failure still takes the retry ladder first, whatever the failure count.
    monkeypatch.setattr(config, "RETRY_DELAYS_MINUTES", [2.0])
    seconds: float
    attempt: int
    seconds, attempt = scrape.next_sleep_seconds(adbutils.AdbError("offline"), 0, 5)
    assert attempt == 1 and seconds <= 3 * 60


def test_empty_retry_schedule_disables_early_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "RETRY_DELAYS_MINUTES", [])
    _: float
    attempt: int
    _, attempt = scrape.next_sleep_seconds(adbutils.AdbError("offline"), 0)
    assert attempt == 0
