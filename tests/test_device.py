"""Device helpers that tolerate a misbehaving device: launching, snapshots, debug dumps."""

import time
from pathlib import Path

import pytest
from instadroid import config, device, diagnostics, uidevice

from tests.fakedevice import FakeDevice, FakeSelector, Out, hierarchy, node


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
            super().__init__({}, "launcher", shell_replies={"": RuntimeError("resolve-activity unavailable")})
            self.app_start_calls: list[tuple[str, str | None, bool | None]] = []

        def app_start(self, package_name: str, activity: str | None = None, stop: bool = False) -> None:
            self.app_start_calls.append((package_name, activity, stop))

    d: NoResolveDevice = NoResolveDevice()
    device.launch_app(d)  # must not raise RecursionError
    assert d.app_start_calls == [(config.IG_PKG, None, False)]


def test_device_snapshot_tolerates_shell_failures() -> None:
    d: FakeDevice = FakeDevice({}, "launcher", shell_replies={"": RuntimeError("adb not connected")})
    snapshot: device.DeviceSnapshot = device.device_snapshot(d)
    assert snapshot == {
        "android_release": None,
        "android_sdk": None,
        "device_product": None,
        "ig_version": None,
        "redroid_image": None,
    }


def test_device_snapshot_reads_its_properties_in_one_shell() -> None:
    class Counting(FakeDevice):
        shells: int = 0

        def shell(self, cmdargs: str | list[str], timeout: float = 60) -> Out:
            self.shells += isinstance(cmdargs, str)
            return super().shell(cmdargs, timeout)

    d: Counting = Counting({}, "launcher")
    del d.props["ro.product.name"]  # an unset property keeps its place, and the fallback answers
    d.props["ro.build.product"] = "fallback"
    snapshot: device.DeviceSnapshot = device.device_snapshot(d)
    assert d.shells == 1
    assert snapshot.get("android_release") == "13" and snapshot.get("android_sdk") == "33"
    assert snapshot.get("device_product") == "fallback"


def _labels(*texts: str) -> FakeDevice:
    return FakeDevice({"s": hierarchy(*(node(text=t, bounds=(0, 0, 10, 10)) for t in texts))}, "s")


def test_match_keeps_list_order_whatever_the_screen_order() -> None:
    assert device.match(_labels("second", "first"), "text", ["first", "second"], 0.5) == "first"
    assert device.match(_labels("second"), "text", ["first", "second"], 0.5) == "second"


def test_match_waits_for_all_candidates_in_one_query() -> None:
    waits: list[float] = []

    class Waiting(FakeSelector):
        def exists(self, timeout: float = 0) -> bool:
            waits.append(timeout)
            return super().exists(timeout)

    class WaitingDevice(FakeDevice):
        def __call__(self, **kwargs: str | list[str]) -> FakeSelector:
            return Waiting(self, kwargs)

    d: FakeDevice = WaitingDevice({"s": hierarchy()}, "s")
    assert device.match(d, "text", ["a", "b", "c"], 0.5) is None
    assert waits == [1.5]


def test_match_with_no_candidates_finds_nothing_even_on_an_empty_label() -> None:
    assert device.match(_labels(""), "text", [], 1) is None


def test_match_takes_candidates_literally() -> None:
    assert device.match(_labels("Forgot password"), "text", ["Forgot password?"], 0) is None
    assert device.match(_labels("Forgot password?"), "text", ["Forgot password?"], 0) == "Forgot password?"
    assert device.match(_labels("a.b"), "text", ["a.b"], 0) == "a.b"
    assert device.match(_labels("axb"), "text", ["a.b"], 0) is None


def test_match_contains_ignores_case_and_spans_lines() -> None:
    d: FakeDevice = _labels("line one\nEnter The Code\nline three")
    assert device.match(d, "textContains", ["no such words", "enter the code"], 0.5) == "enter the code"


def test_first_returns_a_selector_for_the_candidate_found() -> None:
    d: FakeDevice = FakeDevice({"s": hierarchy(node(desc="Log in", bounds=(0, 0, 10, 10)))}, "s")
    assert device.first(d, text=["Log in"]) is None
    found: uidevice.Selector | None = device.first(d, text=["Log in"], description=["Log In", "Log in"])
    assert found is not None and found.count == 1


def test_by_id_keeps_the_anchoring_it_is_asked_for() -> None:
    class Recording(FakeDevice):
        def __init__(self) -> None:
            super().__init__({}, "launcher")
            self.selectors: list[dict[str, str | list[str]]] = []

        def __call__(self, **kwargs: str | list[str]) -> FakeSelector:
            self.selectors.append(kwargs)
            return super().__call__(**kwargs)

    d: Recording = Recording()
    device.by_id(d, "home_tab_id")
    device.by_id(d, "home_tab_id", anchored=True, text="x")
    assert d.selectors == [
        {"resourceIdMatches": ".*:id/feed_tab"},
        {"resourceIdMatches": ".*:id/feed_tab$", "text": "x"},
    ]


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
