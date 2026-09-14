import igprofiles
import pytest
from instadroid import config, diagnostics, versioning

V440 = igprofiles.load("v440")


@pytest.fixture(autouse=True)
def profile_v440(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fake screens were written against Instagram 445, which the root profile v440 covers, so the
    suite as a whole is the v440 regression suite: pin that profile unless a test selects another."""
    monkeypatch.setattr(versioning, "PROFILE", V440)
    monkeypatch.setattr(versioning, "PROFILE_WARNING", None)
    # Explicit rather than "": activate_profile() re-resolves the profile on every connect/install, and
    # an empty IG_PROFILE follows the installed version, which a future profile could claim.
    monkeypatch.setattr(config, "IG_PROFILE", "v440")
    monkeypatch.setattr(config, "IG_APK_VERSION", "")


@pytest.fixture(autouse=True)
def no_real_logcat(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """main() saves a logcat after a device failure by running the real `adb` binary, which on a
    developer host could reach a live redroid. Record the calls instead; the logcat tests call the
    original through test_device_flows.SAVE_FAILURE_LOGCAT."""
    calls: list[str] = []
    monkeypatch.setattr(diagnostics, "save_failure_logcat", lambda error: calls.append(error))
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
