import sqlite3
from pathlib import Path
from typing import Any

import adbutils
import pytest
from instadroid import config, db, device, diagnostics, navigation, scrape, stories
from uiautomator2.exceptions import HTTPError, LaunchUiAutomationError, UiObjectNotFoundError

# A feed list with nothing identifiable in it: parse_hierarchy() returns [] for this.
EMPTY_XML = '<hierarchy><node resource-id="android:id/list" bounds="[0,0][1080,2340]" /></hierarchy>'


@pytest.fixture
def con(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "posts.sqlite"))
    monkeypatch.setattr(config, "MEDIA_DIR", tmp_path / "media")
    monkeypatch.setattr(config, "DEBUG_DIR", tmp_path / "debug")
    return db.db_init()


class EmptyFeedDevice:
    clipboard = ""

    def __init__(self) -> None:
        self.dumps = 0
        self.pressed: list[str] = []

    def dump_hierarchy(self) -> str:
        self.dumps += 1
        return EMPTY_XML

    def press(self, key: str) -> None:
        self.pressed.append(key)


@pytest.fixture
def offline_scrape(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stub out everything in scrape_once() that navigates, so only the scroll loop runs."""
    calls: dict[str, Any] = {"open_feed": 0, "dumps": [], "scrolls": 0}

    def open_feed(d: EmptyFeedDevice) -> None:
        calls["open_feed"] += 1

    def scroll(d: EmptyFeedDevice) -> None:
        calls["scrolls"] += 1

    monkeypatch.setattr(navigation, "open_following_feed", open_feed)
    monkeypatch.setattr(stories, "scrape_stories", lambda d, con: 0)
    monkeypatch.setattr(device, "human_scroll", scroll)
    monkeypatch.setattr(device, "human_pause", lambda *a, **k: None)
    monkeypatch.setattr(diagnostics, "dump_debug", lambda d, name, xml=None: calls["dumps"].append(name))
    return calls


def test_empty_screens_reopen_the_feed_once_then_stop_the_run(
    con: sqlite3.Connection, offline_scrape: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "MAX_SCROLLS", 25)
    monkeypatch.setattr(config, "EMPTY_SCREEN_LIMIT", 3)
    d = EmptyFeedDevice()

    stats = scrape.scrape_once(d, con)

    assert d.dumps == 6  # 3 empty screens, reopen, 3 more, stop — not all 25
    assert offline_scrape["open_feed"] == 2  # the initial open plus exactly one reopen
    assert offline_scrape["dumps"] == ["last", "empty_feed0", "empty_feed1"]
    assert "reopened the feed" in stats["warning"]
    assert "stopped early" in stats["warning"]
    assert d.pressed[-1] == "home"  # still leaves the app in a natural state


def test_empty_screen_guard_can_be_disabled(
    con: sqlite3.Connection, offline_scrape: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "MAX_SCROLLS", 5)
    monkeypatch.setattr(config, "EMPTY_SCREEN_LIMIT", 0)
    d = EmptyFeedDevice()

    stats = scrape.scrape_once(d, con)

    assert d.dumps == 5
    assert offline_scrape["open_feed"] == 1
    assert stats["warning"] is None


def test_scrape_stats_report_cards_per_screen_and_shares(
    con: sqlite3.Connection, offline_scrape: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "MAX_SCROLLS", 3)
    monkeypatch.setattr(config, "EMPTY_SCREEN_LIMIT", 0)
    d = EmptyFeedDevice()

    stats = scrape.scrape_once(d, con)

    assert stats["cards_per_screen"] == 0.0
    assert stats["share_captioned"] == 0.0
    assert stats["share_complete"] == 0.0


def test_scrape_once_flags_selector_drift_against_seeded_baseline(
    con: sqlite3.Connection, offline_scrape: dict[str, Any], monkeypatch: pytest.MonkeyPatch
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
    d = EmptyFeedDevice()  # every dump parses 0 cards — a stand-in for selectors gone stale

    stats = scrape.scrape_once(d, con)

    assert "selector drift?" in stats["warning"]


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
    err = adbutils.AdbError("offline")

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
    seconds, attempt = scrape.next_sleep_seconds(exc, 0)
    assert attempt == 0 and seconds >= config.POLL_MIN_H * 3600


def test_empty_retry_schedule_disables_early_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "RETRY_DELAYS_MINUTES", [])
    _, attempt = scrape.next_sleep_seconds(adbutils.AdbError("offline"), 0)
    assert attempt == 0
