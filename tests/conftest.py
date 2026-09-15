import sqlite3
import time
from pathlib import Path

import igprofiles
import pytest
from instadroid import capture, config, db, device, diagnostics, versioning

V440 = igprofiles.load("v424")


@pytest.fixture(autouse=True)
def profile_v440(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fake screens were written against Instagram 445, which the root profile v424 covers, so the
    suite as a whole is the v424 regression suite: pin that profile unless a test selects another."""
    monkeypatch.setattr(versioning, "PROFILE", V440)
    monkeypatch.setattr(versioning, "PROFILE_WARNING", None)
    # Explicit rather than "": activate_profile() re-resolves the profile on every connect/install, and
    # an empty IG_PROFILE follows the installed version, which a future profile could claim.
    monkeypatch.setattr(config, "IG_PROFILE", "v424")
    monkeypatch.setattr(config, "IG_APK_VERSION", "")


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
    monkeypatch.setattr(config, "CONTROL_DIR", str(tmp_path_factory.mktemp("control")))


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
    monkeypatch.setattr(capture, "_last_url", "")
    monkeypatch.setattr(config, "CLIPBOARD_TIMEOUT", 0.01)
    return tmp_path


@pytest.fixture
def con_and_media(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[sqlite3.Connection, Path]:
    """A fresh database (db_init()) and media directory under tmp_path."""
    db_file = tmp_path / "posts.sqlite"
    media = tmp_path / "media"
    media.mkdir()
    monkeypatch.setattr(config, "DB_PATH", str(db_file))
    monkeypatch.setattr(config, "MEDIA_DIR", media)
    con = db.db_init()
    return con, media
