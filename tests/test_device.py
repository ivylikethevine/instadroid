"""Device helpers that tolerate a misbehaving device: launching, snapshots, debug dumps."""

import time
from pathlib import Path

import pytest
from instadroid import config, device, diagnostics

from tests.fakedevice import FakeDevice, Out


def test_dump_debug_does_not_raise_on_a_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # e.g. a stale file left owned by a different uid from a `docker exec -u root` session, or
    # here: DEBUG_DIR itself can't be created because something else already occupies that path.
    blocked: Path = tmp_path / "debug"
    blocked.write_text("not a directory")
    monkeypatch.setattr(config, "DEBUG_DIR", blocked)

    diagnostics.dump_debug(FakeDevice({"blank": "<hierarchy/>"}, "blank"), "whatever")  # must not raise


def test_launch_app_falls_back_to_monkey_launch_without_recursing_forever() -> None:
    # resolve-activity failing used to recurse into _launch_app itself instead of falling back,
    # which is unbounded recursion, not a fallback.
    class NoResolveDevice(FakeDevice):
        def __init__(self) -> None:
            super().__init__({}, "launcher")
            self.app_start_calls: list[tuple[str, str | None, bool | None]] = []

        def shell(self, cmdargs: str | list[str], timeout: float = 60) -> Out:
            raise RuntimeError("resolve-activity unavailable")

        def app_start(self, package_name: str, activity: str | None = None, stop: bool = False) -> None:
            self.app_start_calls.append((package_name, activity, stop))

    d: NoResolveDevice = NoResolveDevice()
    device.launch_app(d)  # must not raise RecursionError
    assert d.app_start_calls == [(config.IG_PKG, None, False)]


def test_device_snapshot_tolerates_shell_failures() -> None:
    class BrokenDevice(FakeDevice):
        def __init__(self) -> None:
            super().__init__({}, "launcher")

        def shell(self, cmdargs: str | list[str], timeout: float = 60) -> Out:
            raise RuntimeError("adb not connected")

    snapshot: device.DeviceSnapshot = device.device_snapshot(BrokenDevice())
    assert snapshot == {
        "android_release": None,
        "android_sdk": None,
        "device_product": None,
        "ig_version": None,
        "redroid_image": None,
    }


def test_instagram_version_is_read_once_per_connection() -> None:
    d: FakeDevice = FakeDevice({}, "launcher")
    assert device.instagram_version(d) == "445.0.0.45.83"
    assert device.instagram_version(d) == "445.0.0.45.83"
    assert d.shell_calls.count(f"dumpsys package {config.IG_PKG}") == 1  # the big dump ran once
    d.ig_version = "446.0.0.49.77"
    assert device.instagram_version(d) == "445.0.0.45.83"  # still the remembered reading
    assert device.instagram_version(d, fresh=True) == "446.0.0.49.77"  # what install.py asks for


def test_human_pause_sleeps_for_the_sampled_duration(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", sleeps.append)
    device.human_pause(0.25, 0.25)
    assert sleeps == [0.25]
