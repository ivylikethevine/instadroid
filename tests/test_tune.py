"""Device tuning and the boot wait on the app's first connect (instadroid/tune.py)."""

import subprocess
import time
from collections.abc import Callable

import pytest
from instadroid import config, tune

from tests.deviceflows import feed_device
from tests.fakedevice import FakeDevice
from tests.support import fake_run

REAL_WAIT_FOR_BOOT: Callable[[str, float], bool] = (
    tune.wait_for_boot
)  # conftest's autouse fixture stubs the attribute for the suite


@pytest.fixture
def untuned(monkeypatch: pytest.MonkeyPatch) -> None:
    """A process that hasn't tuned yet, with tuning on: what conftest turns off for the rest of the suite."""
    monkeypatch.setattr(tune, "_tuned", False)
    monkeypatch.setattr(config, "TUNE_ON_CONNECT", True)


def test_the_package_list_never_holds_the_installer() -> None:
    packages: list[str] = tune.load_packages()
    assert len(packages) == 42 and "com.android.vending" in packages and "com.android.traceur" in packages
    assert "com.android.packageinstaller" not in packages  # docs/INCIDENTS.md: crash-loops the next boot
    assert "com.android.settings" not in packages and "com.android.phone" not in packages


def test_tune_commands_in_order() -> None:
    commands: list[str] = tune.tune_commands("America/Los_Angeles", ["a.b", "c.d"])
    assert commands[0] == "service call alarm 3 s16 America/Los_Angeles"
    assert "settings put global window_animation_scale 0" in commands
    assert "settings put system screen_off_timeout 2147483647" in commands
    assert commands[-2:] == ["pm disable-user --user 0 a.b", "pm disable-user --user 0 c.d"]
    assert not tune.tune_commands("", [])[0].startswith("service call")  # no timezone: not applied


@pytest.mark.usefixtures("untuned")
def test_tune_device_runs_once_per_process_in_one_round_trip(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    d: FakeDevice = feed_device()
    assert tune.tune_device(d)
    calls: list[str] = [c for c in d.shell_calls if "pm disable-user" in c or "settings put" in c]
    assert len(calls) == 6 + 42 and calls[0].startswith("(settings put global window_animation_scale 0")
    assert "device tuned" in capsys.readouterr().out
    before: int = len(d.shell_calls)
    assert not tune.tune_device(d) and len(d.shell_calls) == before  # already done this process
    monkeypatch.setattr(config, "TUNE_ON_CONNECT", False)
    monkeypatch.setattr(tune, "_tuned", False)
    assert not tune.tune_device(d)  # opted out
    assert tune.tune_device(d, force=True)  # `scraper.py tune` still works


def test_wait_for_boot_polls_until_the_property_flips(monkeypatch: pytest.MonkeyPatch) -> None:
    answers: list[str] = ["", "0", "1"]
    sleeps: list[float] = []

    def run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        if cmd[1] == "connect":  # re-connected before every poll: no transport until adbd listens
            return subprocess.CompletedProcess(cmd, 0, "connected to 127.0.0.1:5555\n", "")
        assert cmd[:4] == ["adb", "-s", "127.0.0.1:5555", "shell"]
        return subprocess.CompletedProcess(cmd, 0, answers.pop(0) + "\n", "")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(time, "sleep", sleeps.append)
    assert REAL_WAIT_FOR_BOOT("127.0.0.1:5555", 600)
    assert sleeps == [5, 5] and answers == []


def test_wait_for_boot_gives_up_after_the_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    clock: list[float] = [0.0]

    def monotonic() -> float:
        clock[0] += 100
        return clock[0]

    def sleeps_nothing(seconds: float) -> None:
        pass

    fake_run(monkeypatch, raises=OSError("no adb"))
    monkeypatch.setattr(time, "monotonic", monotonic)
    monkeypatch.setattr(time, "sleep", sleeps_nothing)
    assert not REAL_WAIT_FOR_BOOT("127.0.0.1:5555", 150)


@pytest.mark.usefixtures("untuned")
def test_a_tuning_failure_is_logged_and_left_for_the_next_connect(
    capsys: pytest.CaptureFixture[str],
) -> None:
    d: FakeDevice = FakeDevice({}, "launcher", shell_replies={"": RuntimeError("adb: device offline")})
    assert not tune.tune_device(d)  # never raises: an untuned device still scrapes
    assert "device tuning failed" in capsys.readouterr().out
    assert not tune._tuned  # so the next connect tries again


@pytest.mark.usefixtures("untuned")
def test_tune_device_names_the_commands_the_device_rejected(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The second command's exit status was non-zero.
    assert tune.tune_device(FakeDevice({}, "launcher", shell_replies={"": "+ - +"}))
    out: str = capsys.readouterr().out
    assert "device tuned: 2 of 48 commands applied" in out
    assert (
        "tuning commands that failed on the device: settings put global transition_animation_scale 0" in out
    )
