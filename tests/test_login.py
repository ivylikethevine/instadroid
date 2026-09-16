"""Login, and installing Instagram when the device doesn't have it (navigation.ensure_logged_in, install)."""

import subprocess
import zipfile
from pathlib import Path
from typing import Unpack

import igprofiles
import pytest
from instadroid import (
    config,
    device,
    install,
    navigation,
    versioning,
)

from tests.deviceflows import RunOptions, home_screen
from tests.fakedevice import IG_PKG, FakeDevice, Out, hierarchy, node

pytestmark = pytest.mark.usefixtures("fast_offline")

# --- login ------------------------------------------------------------------------------------


def login_screen(button_goto: str = "notnow") -> str:
    return hierarchy(
        node(
            cls="android.widget.TextView", text="Phone number, username or email", bounds=(0, 500, 1080, 560)
        ),
        node(cls="android.widget.EditText", bounds=(0, 600, 1080, 700)),
        node(cls="android.widget.TextView", text="Password", bounds=(0, 700, 1080, 740)),
        node(cls="android.widget.EditText", bounds=(0, 750, 1080, 850)),
        node(cls="android.widget.Button", desc="Log in", bounds=(0, 900, 1080, 1000), goto=button_goto),
        node(cls="android.widget.TextView", text="Forgot password?", bounds=(0, 1100, 1080, 1150)),
    )


def text_screen(text: str, goto: str | None = None) -> str:
    return hierarchy(node(cls="android.widget.TextView", text=text, bounds=(0, 1000, 1080, 1100), goto=goto))


def test_login_fills_the_form_and_dismisses_interstitials() -> None:
    screens: dict[str, str] = {
        "login": login_screen(),
        "notnow": text_screen("Not now", goto="home"),
        "home": home_screen(),
    }
    d: FakeDevice = FakeDevice(screens, "login")
    navigation.ensure_logged_in(d)
    assert d.typed == [(0, "me"), (1, "hunter2")]
    assert d.screen == "home"
    assert d.launches == ["com.instagram.mainactivity.LauncherActivity"]  # resolved, not monkey


def test_login_taps_through_the_logged_out_welcome_screen() -> None:
    screens: dict[str, str] = {
        "welcome": text_screen("I already have a profile", goto="login"),
        "login": login_screen(button_goto="home"),
        "home": home_screen(),
    }
    d: FakeDevice = FakeDevice(screens, "welcome")
    navigation.ensure_logged_in(d)
    assert "login" in d.history


def test_login_dismisses_a_stray_ok_alert_and_accepts_a_live_session() -> None:
    d: FakeDevice = FakeDevice({"alert": text_screen("OK", goto="home"), "home": home_screen()}, "alert")
    navigation.ensure_logged_in(d)
    assert d.typed == []


@pytest.mark.parametrize(
    ("screens", "start", "match"),
    [
        ({"challenge": text_screen("Help us confirm it's you")}, "challenge", "wants a human"),
        ({"login": login_screen(button_goto="challenge"), "challenge": text_screen("Enter the code")}, "login", "wants a human"),
        ({"login": login_screen(button_goto="")}, "login", "wrong password"),
        ({"markers": text_screen("Forgot password?")}, "markers", "form not recognised"),
    ],
)  # fmt: skip
def test_login_failures_raise_with_a_debug_dump(
    screens: dict[str, str], start: str, match: str, fast_offline: Path
) -> None:
    with pytest.raises(RuntimeError, match=match):
        navigation.ensure_logged_in(FakeDevice(screens, start))
    assert (fast_offline / "debug" / "login_hierarchy.xml").exists()


def test_login_without_credentials_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "IG_PASSWORD", "")
    with pytest.raises(RuntimeError, match="not set"):
        navigation.ensure_logged_in(FakeDevice({"login": login_screen()}, "login"))


def test_login_raises_when_instagram_is_not_installed_and_auto_install_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "IG_AUTO_INSTALL", False)
    with pytest.raises(RuntimeError, match="not installed"):
        navigation.ensure_logged_in(FakeDevice({}, "launcher", installed=()))


def test_login_trusts_pm_path_over_an_app_list_that_misses_instagram(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """app_list() saying "missing" alone must not trigger a reinstall over a live login."""

    class Settling(FakeDevice):
        def shell(self, cmdargs: str | list[str], timeout: float = 60) -> Out:
            joined: str = " ".join(cmdargs) if isinstance(cmdargs, list) else cmdargs
            if joined == f"pm path {IG_PKG}":
                return Out(f"package:/data/app/{IG_PKG}-1/base.apk")
            return super().shell(cmdargs, timeout)

    installs: list[str] = []

    def fake_install(d: FakeDevice) -> None:
        installs.append("x")

    monkeypatch.setattr(install, "install_instagram", fake_install)
    navigation.ensure_logged_in(Settling({"home": home_screen()}, "launcher", installed=()))
    assert installs == [] and "not reinstalling" in capsys.readouterr().out


def _apk_run(
    monkeypatch: pytest.MonkeyPatch, d: FakeDevice, calls: list[list[str]], *, fail_on: str | None = None
) -> None:
    """Patch subprocess.run to fake apkeep + adb install without touching the network or a
    real device, recording every invocation into `calls`. `fail_on` (argv[0], "apkeep" or "adb")
    makes that step raise CalledProcessError. A successful "adb install"/"install-multiple" flips
    `d`'s installed set, same as a real adb install would."""

    def fake_run(cmd: list[str], **kwargs: Unpack[RunOptions]) -> subprocess.CompletedProcess[str]:
        zf: zipfile.ZipFile
        calls.append(cmd)
        if fail_on and cmd[0] == fail_on:
            raise subprocess.CalledProcessError(1, cmd, output="", stderr="boom")
        if cmd[0] == "apkeep":
            out_dir: Path = Path(cmd[cmd.index("-d") + 2])
            out_dir.mkdir(parents=True, exist_ok=True)
            xapk: Path = out_dir / f"{config.IG_PKG}@1.0.0.xapk"
            with zipfile.ZipFile(xapk, "w") as zf:
                zf.writestr(f"{config.IG_PKG}.apk", b"base")
                zf.writestr("config.arm64_v8a.apk", b"split")
                zf.writestr("manifest.json", b"{}")
        elif cmd[0] == "adb":
            d.install()
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)


def test_ensure_logged_in_installs_instagram_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    d: FakeDevice = FakeDevice({"home": home_screen()}, "launcher", installed=())
    calls: list[list[str]] = []
    _apk_run(monkeypatch, d, calls)
    navigation.ensure_logged_in(d)
    apkeep_call: list[str] = next(c for c in calls if c[0] == "apkeep")
    assert apkeep_call[:3] == ["apkeep", "-a", config.IG_PKG]
    install_call: list[str] = next(c for c in calls if c[0] == "adb")
    assert install_call[3] == "install-multiple"
    assert install_call[4].endswith(f"{config.IG_PKG}.apk")
    assert install_call[5].endswith("config.arm64_v8a.apk")


def test_installing_instagram_reactivates_the_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    d: FakeDevice = FakeDevice({"home": home_screen()}, "launcher", installed=())
    _apk_run(monkeypatch, d, [])
    monkeypatch.setattr(versioning, "PROFILE_WARNING", "stale warning from before the install")
    navigation.ensure_logged_in(d)
    assert versioning.PROFILE.name == "v424" and versioning.PROFILE_WARNING is None  # installed 445.0.0.45.83


def test_auto_install_fetches_the_default_build(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "IG_APK_VERSION", "")
    monkeypatch.setattr(config, "IG_PROFILE", "")
    d: FakeDevice = FakeDevice({"home": home_screen()}, "launcher", installed=(), ig_version="445.0.0.45.83")
    calls: list[list[str]] = []
    _apk_run(monkeypatch, d, calls)
    navigation.ensure_logged_in(d)
    assert next(c for c in calls if c[0] == "apkeep")[2] == f"{config.IG_PKG}@{igprofiles.DEFAULT_BUILD}"
    assert versioning.PROFILE.name == "v424" and versioning.PROFILE_WARNING is None


def test_a_pinned_apk_version_gets_its_own_cache_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    # An unpinned bundle already cached at the top level must not be installed for a pinned version.
    xapk_dir: Path = config.APK_CACHE_DIR / "xapk"
    xapk_dir.mkdir(parents=True)
    (xapk_dir / f"{config.IG_PKG}.apk").write_bytes(b"latest")
    monkeypatch.setattr(config, "IG_APK_VERSION", "445.0.0.45.83")
    d: FakeDevice = FakeDevice({"home": home_screen()}, "launcher", installed=())
    calls: list[list[str]] = []
    _apk_run(monkeypatch, d, calls)
    navigation.ensure_logged_in(d)
    apkeep_call: list[str] = next(c for c in calls if c[0] == "apkeep")
    assert apkeep_call[2] == f"{config.IG_PKG}@445.0.0.45.83"
    assert apkeep_call[-1] == str(config.APK_CACHE_DIR / "445.0.0.45.83")
    install_call: list[str] = next(c for c in calls if c[0] == "adb")
    assert all("/445.0.0.45.83/" in arg for arg in install_call[4:])


def test_install_version_replaces_a_newer_install_in_place(monkeypatch: pytest.MonkeyPatch) -> None:
    d: FakeDevice = FakeDevice({"home": home_screen()}, "launcher", ig_version="446.0.0.49.77")

    def downgrade(pkg: str = config.IG_PKG) -> None:
        d.ig_version = "445.0.0.45.83"  # what the downgrade installs

    d.install = downgrade
    calls: list[list[str]] = []
    _apk_run(monkeypatch, d, calls)
    assert install.install_instagram_version(d, "445.0.0.45.83") == "445.0.0.45.83"
    assert next(c for c in calls if c[0] == "apkeep")[2] == f"{config.IG_PKG}@445.0.0.45.83"
    install_call: list[str] = next(c for c in calls if c[0] == "adb")
    assert install_call[3:6] == ["install-multiple", "-r", "-d"]


def test_install_version_defaults_to_the_pinned_version_and_skips_when_already_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "IG_APK_VERSION", "445.0.0.45.83")
    d: FakeDevice = FakeDevice({"home": home_screen()}, "launcher")  # already reports 445.0.0.45.83
    calls: list[list[str]] = []
    _apk_run(monkeypatch, d, calls)
    assert install.install_instagram_version(d) == "445.0.0.45.83"
    assert calls == []


def test_install_version_raises_when_the_device_reports_another_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    d: FakeDevice = FakeDevice({"home": home_screen()}, "launcher", ig_version="446.0.0.49.77")
    _apk_run(monkeypatch, d, [])  # the fake install leaves the version untouched
    with pytest.raises(device.DeviceNotReady, match="device reports 446.0.0.49.77"):
        install.install_instagram_version(d, "445.0.0.45.83")


def test_ensure_logged_in_reuses_a_cached_apk(monkeypatch: pytest.MonkeyPatch) -> None:
    xapk_dir: Path = config.APK_CACHE_DIR / "xapk"
    xapk_dir.mkdir(parents=True)
    (xapk_dir / f"{config.IG_PKG}.apk").write_bytes(b"base")
    d: FakeDevice = FakeDevice({"home": home_screen()}, "launcher", installed=())
    calls: list[list[str]] = []
    _apk_run(monkeypatch, d, calls)
    navigation.ensure_logged_in(d)
    assert not any(c[0] == "apkeep" for c in calls)
    install_call: list[str] = next(c for c in calls if c[0] == "adb")
    assert install_call[3] == "install"  # single apk: no -multiple


def test_ensure_logged_in_raises_transiently_when_apkeep_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    d: FakeDevice = FakeDevice({}, "launcher", installed=())
    _apk_run(monkeypatch, d, [], fail_on="apkeep")
    with pytest.raises(device.DeviceNotReady):
        navigation.ensure_logged_in(d)


def test_ensure_logged_in_raises_transiently_when_install_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    d: FakeDevice = FakeDevice({}, "launcher", installed=())
    _apk_run(monkeypatch, d, [], fail_on="adb")
    with pytest.raises(device.DeviceNotReady):
        navigation.ensure_logged_in(d)


def test_app_that_never_foregrounds_is_a_transient_device_failure() -> None:
    d: FakeDevice = FakeDevice({}, "launcher", launch_blocked=True)
    exc: pytest.ExceptionInfo[device.DeviceNotReady]
    with pytest.raises(device.DeviceNotReady) as exc:
        navigation.ensure_logged_in(d)
    assert device.is_transient(exc.value)
    assert len(d.launches) == 4  # the first launch plus three retries


def test_an_app_that_dies_right_after_launch_is_not_logged_in() -> None:
    class CrashingDevice(FakeDevice):
        """Instagram comes to the front, then crashes back to the launcher a moment later."""

        checks = 0

        def app_current(self) -> dict[str, str]:
            self.checks += 1
            return {"package": config.IG_PKG if self.checks == 1 else "com.android.launcher3"}

    d: CrashingDevice = CrashingDevice({"home": home_screen()}, "launcher")
    exc: pytest.ExceptionInfo[device.DeviceNotReady]
    with pytest.raises(device.DeviceNotReady, match="left the foreground right after launch") as exc:
        navigation.ensure_logged_in(d)
    assert device.is_transient(exc.value)  # retried, with a logcat saved, like any device failure
