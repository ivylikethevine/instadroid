import igprofiles
import pytest
from instadroid import config, diagnostics, versioning

V445 = igprofiles.load("v445")


@pytest.fixture(autouse=True)
def profile_v445(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every existing fixture and fake screen was captured from Instagram 445, so the suite as a
    whole is the v445 regression suite: pin that profile unless a test selects another itself."""
    monkeypatch.setattr(versioning, "PROFILE", V445)
    monkeypatch.setattr(versioning, "PROFILE_WARNING", None)
    # Explicit rather than "": activate_profile() re-resolves IG_PROFILE on every connect/install,
    # and an empty one means DEFAULT_PROFILE, which moves forward independently of these fixtures.
    monkeypatch.setattr(config, "IG_PROFILE", "v445")
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
