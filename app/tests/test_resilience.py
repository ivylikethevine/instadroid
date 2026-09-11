import adbutils
import pytest
import scraper
from uiautomator2.exceptions import HTTPError, LaunchUiAutomationError, UiObjectNotFoundError

# A feed list with nothing identifiable in it: parse_hierarchy() returns [] for this.
EMPTY_XML = '<hierarchy><node resource-id="android:id/list" bounds="[0,0][1080,2340]" /></hierarchy>'


@pytest.fixture
def con(tmp_path, monkeypatch):
    monkeypatch.setattr(scraper, "DB_PATH", str(tmp_path / "posts.sqlite"))
    monkeypatch.setattr(scraper, "MEDIA_DIR", tmp_path / "media")
    monkeypatch.setattr(scraper, "DEBUG_DIR", tmp_path / "debug")
    return scraper.db_init()


class EmptyFeedDevice:
    clipboard = ""

    def __init__(self):
        self.dumps = 0
        self.pressed = []

    def dump_hierarchy(self):
        self.dumps += 1
        return EMPTY_XML

    def press(self, key):
        self.pressed.append(key)


@pytest.fixture
def offline_scrape(monkeypatch):
    """Stub out everything in scrape_once() that navigates, so only the scroll loop runs."""
    calls = {"open_feed": 0, "dumps": [], "scrolls": 0}

    def open_feed(d):
        calls["open_feed"] += 1

    def scroll(d):
        calls["scrolls"] += 1

    monkeypatch.setattr(scraper, "open_following_feed", open_feed)
    monkeypatch.setattr(scraper, "scrape_stories", lambda d, con: 0)
    monkeypatch.setattr(scraper, "human_scroll", scroll)
    monkeypatch.setattr(scraper, "human_pause", lambda *a, **k: None)
    monkeypatch.setattr(scraper, "_dump_debug", lambda d, name, xml=None: calls["dumps"].append(name))
    return calls


def test_empty_screens_reopen_the_feed_once_then_stop_the_run(con, offline_scrape, monkeypatch):
    monkeypatch.setattr(scraper, "MAX_SCROLLS", 25)
    monkeypatch.setattr(scraper, "EMPTY_SCREEN_LIMIT", 3)
    d = EmptyFeedDevice()

    stats = scraper.scrape_once(d, con)

    assert d.dumps == 6  # 3 empty screens, reopen, 3 more, stop — not all 25
    assert offline_scrape["open_feed"] == 2  # the initial open plus exactly one reopen
    assert offline_scrape["dumps"] == ["last", "empty_feed0", "empty_feed1"]
    assert "reopened the feed" in stats["warning"]
    assert "stopped early" in stats["warning"]
    assert d.pressed[-1] == "home"  # still leaves the app in a natural state


def test_empty_screen_guard_can_be_disabled(con, offline_scrape, monkeypatch):
    monkeypatch.setattr(scraper, "MAX_SCROLLS", 5)
    monkeypatch.setattr(scraper, "EMPTY_SCREEN_LIMIT", 0)
    d = EmptyFeedDevice()

    stats = scraper.scrape_once(d, con)

    assert d.dumps == 5
    assert offline_scrape["open_feed"] == 1
    assert stats["warning"] is None


def test_record_run_stores_a_warning(con):
    scraper.record_run(
        con, "2026-09-11T00:00:00+00:00", "2026-09-11T00:05:00+00:00", 0, None, {}, warning="w"
    )
    assert con.execute("SELECT warning FROM runs").fetchone()[0] == "w"


@pytest.mark.parametrize(
    "exc",
    [
        scraper.DeviceNotReady("could not bring com.instagram.android to the foreground"),
        adbutils.AdbError("device 127.0.0.1:5555 not online"),
        LaunchUiAutomationError("server quit unexpectly"),
        HTTPError("uiautomator jsonrpc unreachable"),
    ],
)
def test_device_failures_are_transient(exc):
    assert scraper.is_transient(exc)


@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("Instagram wants a human: 'Confirm it's you' screen"),
        RuntimeError("still on login screen after submit (wrong password?)"),
        UiObjectNotFoundError("selector drifted"),
        ValueError("parse bug"),
    ],
)
def test_challenges_and_logic_errors_are_not_transient(exc):
    assert not scraper.is_transient(exc)


def test_transient_failures_retry_on_the_short_schedule_then_fall_back_to_polling(monkeypatch):
    monkeypatch.setattr(scraper, "TIME_DISTRIBUTION", "uniform")
    monkeypatch.setattr(scraper, "RETRY_DELAYS_MINUTES", [2.0, 5.0])
    monkeypatch.setattr(scraper, "POLL_MIN_H", 2.5)
    monkeypatch.setattr(scraper, "POLL_MAX_H", 4.5)
    err = adbutils.AdbError("offline")

    seconds, attempt = scraper.next_sleep_seconds(err, 0)
    assert attempt == 1 and 2 * 60 <= seconds <= 3 * 60
    seconds, attempt = scraper.next_sleep_seconds(err, attempt)
    assert attempt == 2 and 5 * 60 <= seconds <= 7.5 * 60
    seconds, attempt = scraper.next_sleep_seconds(err, attempt)  # retries exhausted
    assert attempt == 0 and 2.5 * 3600 <= seconds <= 4.5 * 3600


@pytest.mark.parametrize("exc", [None, RuntimeError("Instagram wants a human: 'Enter the code' screen")])
def test_success_and_challenges_sleep_a_normal_poll_interval(exc, monkeypatch):
    monkeypatch.setattr(scraper, "TIME_DISTRIBUTION", "uniform")
    monkeypatch.setattr(scraper, "RETRY_DELAYS_MINUTES", [2.0])
    seconds, attempt = scraper.next_sleep_seconds(exc, 0)
    assert attempt == 0 and seconds >= scraper.POLL_MIN_H * 3600


def test_empty_retry_schedule_disables_early_retries(monkeypatch):
    monkeypatch.setattr(scraper, "RETRY_DELAYS_MINUTES", [])
    _, attempt = scraper.next_sleep_seconds(adbutils.AdbError("offline"), 0)
    assert attempt == 0
