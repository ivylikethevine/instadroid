import sqlite3
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import igprofiles
import pytest
from igprofiles import BaseProfile
from instadroid import capture, config, db, device, diagnostics, tune, versioning

V424: BaseProfile = igprofiles.load("v424")


@pytest.fixture(autouse=True)
def profile_v424(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fake screens were written against Instagram 445, which the root profile v424 covers, so the
    suite as a whole is the v424 regression suite: pin that profile unless a test selects another."""
    monkeypatch.setattr(versioning, "PROFILE", V424)
    monkeypatch.setattr(versioning, "PROFILE_WARNING", None)
    # Explicit rather than "": activate_profile() re-resolves the profile on every connect/install, and
    # an empty IG_PROFILE follows the installed version, which a future profile could claim.
    monkeypatch.setattr(config, "IG_PROFILE", "v424")
    monkeypatch.setattr(config, "IG_APK_VERSION", "")


@pytest.fixture(autouse=True)
def no_real_boot_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """connect_device() polls the real `adb` binary for sys.boot_completed and then tunes the device;
    neither may reach a live redroid from the suite. The tuning stays testable through the fake
    device's shell (tests/test_tune.py resets `_tuned`)."""

    def booted(addr: str, timeout: float) -> bool:
        return True

    monkeypatch.setattr(tune, "wait_for_boot", booted)
    monkeypatch.setattr(tune, "_tuned", True)


@pytest.fixture(autouse=True)
def no_real_logcat(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """main() saves a logcat after a device failure by running the real `adb` binary, which on a
    developer host could reach a live redroid. Record the calls instead; the logcat tests call the
    original through tests.deviceflows.SAVE_FAILURE_LOGCAT."""
    calls: list[str] = []

    def record(error: str) -> None:
        calls.append(error)

    monkeypatch.setattr(diagnostics, "save_failure_logcat", record)
    return calls


@pytest.fixture(autouse=True)
def no_profile_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    """Capture mode stays off (whatever the environment says), with a fresh per-screen count."""
    monkeypatch.setattr(config, "PROFILE_CAPTURE_DIR", "")
    monkeypatch.setattr(diagnostics, "_captured", {})


@pytest.fixture(autouse=True)
def backups_in_tmp(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Database backups at the end of a run go to a throwaway directory, never /db/backups."""
    monkeypatch.setattr(config, "BACKUP_DIR", str(tmp_path_factory.mktemp("backups")))


@pytest.fixture(autouse=True)
def no_alert_delivery(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test posts a real alert, whatever ALERT_URL the environment has."""
    monkeypatch.setattr(config, "ALERT_URL", "")


@pytest.fixture(autouse=True)
def control_files_in_tmp(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """The poll loop's lock and scrape-now files live in a throwaway directory, never /db."""
    monkeypatch.setattr(config, "CONTROL_DIR", tmp_path_factory.mktemp("control"))


@pytest.fixture(autouse=True)
def close_databases(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Tests open databases with db_init() and leave them to the test's end; close each one at teardown
    rather than when it's garbage-collected, which warns ResourceWarning."""
    opened: list[sqlite3.Connection] = []
    real_init: Callable[[], sqlite3.Connection] = db.db_init

    def tracked_init() -> sqlite3.Connection:
        con: sqlite3.Connection = real_init()
        opened.append(con)
        return con

    monkeypatch.setattr(db, "db_init", tracked_init)
    yield
    con: sqlite3.Connection
    for con in opened:
        con.close()


@pytest.fixture(autouse=True)
def no_permalink_backfill(monkeypatch: pytest.MonkeyPatch) -> None:
    """Flow tests seed stored hash-id posts without expecting a Copy link attempt on them; the backfill
    tests (test_permalink_backfill.py) turn it back on."""
    monkeypatch.setattr(config, "PERMALINK_BACKFILL_PER_RUN", 0)


@pytest.fixture
def fast_offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The device-flow setup: databases and files under tmp_path, no pauses or sleeps, test credentials.
    Device-flow modules turn it on for every test with `pytestmark = pytest.mark.usefixtures("fast_offline")`."""
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "posts.sqlite"))
    monkeypatch.setattr(config, "MEDIA_DIR", tmp_path / "media")
    monkeypatch.setattr(config, "DEBUG_DIR", tmp_path / "debug")
    monkeypatch.setattr(config, "APK_CACHE_DIR", tmp_path / "apk")
    monkeypatch.setattr(config, "IG_APK_VERSION", "latest")  # the top-level cache layout

    def no_pause(lo: float = 1.0, hi: float = 3.0) -> None:
        pass

    def no_sleep(seconds: float) -> None:
        pass

    monkeypatch.setattr(device, "human_pause", no_pause)
    monkeypatch.setattr(time, "sleep", no_sleep)
    monkeypatch.setattr(config, "IG_USERNAME", "me")
    monkeypatch.setattr(config, "IG_PASSWORD", "hunter2")
    monkeypatch.setattr(capture, "_last_code", "")
    monkeypatch.setattr(config, "CLIPBOARD_TIMEOUT", 0.01)
    return tmp_path


@pytest.fixture
def con(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    """A fresh database (db_init()) at tmp_path/posts.sqlite, with an empty media directory tmp_path/media
    (config.MEDIA_DIR). A module needing more settings overrides it:
    `def con(con: sqlite3.Connection, monkeypatch) -> ...`."""
    media: Path = tmp_path / "media"
    media.mkdir()
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "posts.sqlite"))
    monkeypatch.setattr(config, "MEDIA_DIR", media)
    return db.db_init()
