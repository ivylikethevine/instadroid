"""redroid memory housekeeping: force-stopping Instagram around a run, the memory guard, OOM kill reporting."""

import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import NoReturn

import pytest
from instadroid import (
    config,
    db,
    device,
    navigation,
    scrape,
    uidevice,
)
from shared import sqlrows

from tests.deviceflows import StopLoop, feed_device, stop_after_first_sleep
from tests.fakedevice import FakeDevice, Out
from tests.support import fetch_row

pytestmark = pytest.mark.usefixtures("fast_offline")


def test_force_stop_is_one_shell_call_and_best_effort() -> None:
    d = feed_device(start="following")

    device.free_device_memory(d)

    assert d.shell_calls == [f"am force-stop {pkg}" for pkg in (*device.CACHED_APP_SWEEP, config.IG_PKG)]

    def raising_shell(cmdargs: str | list[str], timeout: float = 60) -> NoReturn:
        raise RuntimeError("device offline")

    d.shell = raising_shell
    device.force_stop(d, config.IG_PKG)  # must not raise


# --- memory guard ---------------------------------------------------------------------------------

MIB = 1024 * 1024


def cgroup_output(
    current_mib: int, max_mib: int | None = 3072, oom_kill: int = 0, inactive_file_mib: int = 0
) -> str:
    """memory.current, memory.max, memory.events, then (part of) memory.stat, as `cat` prints them."""
    limit = "max" if max_mib is None else str(max_mib * MIB)
    usage = (current_mib + inactive_file_mib) * MIB
    return (
        f"{usage}\n{limit}\nlow 0\nhigh 0\nmax 12\noom 3\noom_kill {oom_kill}\noom_group_kill 0\n"
        f"anon {current_mib * MIB}\nfile {inactive_file_mib * MIB}\ninactive_file {inactive_file_mib * MIB}\n"
    )


def with_cgroup(d: FakeDevice, readings: Iterable[str]) -> FakeDevice:
    """Make FakeDevice `d` answer the memory guard's cgroup read with successive `readings`
    (cgroup_output() strings); the last one repeats."""
    shell = d.shell
    readings = list(readings)

    def fake_shell(cmdargs: str | list[str], timeout: float = 60) -> Out:
        joined = " ".join(cmdargs) if isinstance(cmdargs, list) else cmdargs
        if joined.startswith("cat /sys/fs/cgroup/memory.current"):
            d.shell_calls.append(joined)
            return Out(readings.pop(0) if len(readings) > 1 else readings[0])
        return shell(cmdargs, timeout)

    d.shell = fake_shell
    return d


def test_redroid_memory_parses_the_cgroup_files() -> None:
    d = with_cgroup(feed_device(), [cgroup_output(1843, 3072, oom_kill=7)])
    assert device._redroid_memory(d) == {"current": 1843 * MIB, "max": 3072 * MIB, "oom_kill": 7}
    unlimited = with_cgroup(feed_device(), [cgroup_output(500, None)])
    unlimited_reading = device._redroid_memory(unlimited)
    assert unlimited_reading is not None and unlimited_reading["max"] is None
    assert device._redroid_memory(feed_device()) is None  # no cgroup files: guard off


def test_redroid_memory_excludes_reclaimable_file_cache() -> None:
    # The live reading that stopped a run too early: 2756MiB counted, but ~600MiB was file cache.
    d = with_cgroup(feed_device(), [cgroup_output(2150, 3072, inactive_file_mib=606)])
    reading = device._redroid_memory(d)
    assert reading is not None and reading["current"] == 2150 * MIB
    assert device.MemoryGuard(d).exceeded() is None  # 70% of the limit, not 90%


def test_scrape_once_starts_and_ends_with_instagram_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "MAX_STORIES_PER_RUN", 0)
    monkeypatch.setattr(config, "MAX_SCROLLS", 1)
    d = feed_device()
    scrape.scrape_once(d, db.db_init())
    stops = [i for i, c in enumerate(d.shell_calls) if c == f"am force-stop {config.IG_PKG}"]
    assert len(stops) == 2
    assert stops[0] == 0 or all(
        c.startswith("am force-stop") for c in d.shell_calls[: stops[0]]
    )  # first thing
    assert stops[1] > d.shell_calls.index("dumpsys package com.instagram.android")  # after the run


def test_scrape_once_still_stops_instagram_when_the_run_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(d: uidevice.Device) -> NoReturn:
        raise device.DeviceNotReady("feed never opened")

    monkeypatch.setattr(navigation, "open_target_feed", boom)
    d = feed_device()
    with pytest.raises(device.DeviceNotReady):
        scrape.scrape_once(d, db.db_init())
    assert d.shell_calls.count(f"am force-stop {config.IG_PKG}") == 2
    for pkg in device.CACHED_APP_SWEEP:
        assert d.shell_calls.count(f"am force-stop {pkg}") == 2


def test_memory_guard_stops_the_run_before_the_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "MEMORY_GUARD_PERCENT", 85)
    monkeypatch.setattr(config, "MAX_STORIES_PER_RUN", 0)
    # start, before stories, first screen: fine; second screen check: 2700 of 3072 MiB is 88%.
    d = with_cgroup(
        feed_device(), [cgroup_output(900), cgroup_output(1200), cgroup_output(1500), cgroup_output(2700)]
    )
    metrics = scrape.scrape_once(d, db.db_init())["metrics"]
    warning = metrics.get("warning")
    assert warning is not None
    assert "stopped early: redroid memory at 2700 of 3072 MiB (MEMORY_GUARD_PERCENT=85)" in warning
    assert metrics.get("mem_peak_mb") == 2700
    assert metrics.get("oom_kills") == 0


def test_memory_guard_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "MEMORY_GUARD_PERCENT", 0)
    monkeypatch.setattr(config, "MAX_STORIES_PER_RUN", 0)
    monkeypatch.setattr(config, "MAX_SCROLLS", 1)
    metrics = scrape.scrape_once(with_cgroup(feed_device(), [cgroup_output(3000)]), db.db_init())["metrics"]
    assert not (metrics.get("warning") or "").startswith("stopped early")
    assert metrics.get("mem_peak_mb") == 3000  # still measured


def test_memory_guard_skips_stories_when_already_over(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "MAX_SCROLLS", 1)
    d = with_cgroup(feed_device(), [cgroup_output(2900)])
    metrics = scrape.scrape_once(d, db.db_init())["metrics"]
    assert metrics.get("new_stories") == 0
    warning = metrics.get("warning")
    assert warning is not None
    assert "skipped stories: redroid memory at 2900 of 3072 MiB" in warning


def test_run_time_budget_stops_the_run_early(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "RUN_MAX_MINUTES", 30)
    monkeypatch.setattr(config, "MAX_STORIES_PER_RUN", 0)
    ticks = iter([0.0, 10 * 60, 20 * 60, 31 * 60, 40 * 60])  # start, stories check, screen 0, screen 1, ...
    real = device.RunClock

    def fake_clock() -> device.RunClock:
        return real(lambda: next(ticks))

    monkeypatch.setattr(device, "RunClock", fake_clock)
    metrics = scrape.scrape_once(feed_device(), db.db_init())["metrics"]
    warning = metrics.get("warning")
    assert warning is not None
    assert "stopped early: run has taken 31 min (RUN_MAX_MINUTES=30)" in warning
    monkeypatch.setattr(config, "RUN_MAX_MINUTES", 0)
    assert real(lambda: 10**9).exceeded() is None  # 0 disables


def test_oom_kills_during_a_run_are_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "MAX_STORIES_PER_RUN", 0)
    monkeypatch.setattr(config, "MAX_SCROLLS", 1)
    d = with_cgroup(feed_device(), [cgroup_output(900, oom_kill=7), cgroup_output(1000, oom_kill=9)])
    metrics = scrape.scrape_once(d, db.db_init())["metrics"]
    assert metrics.get("oom_kills") == 2
    warning = metrics.get("warning")
    assert warning is not None
    assert "redroid OOM-killed 2 Android process(es)" in warning


def test_main_records_memory_stats(fast_offline: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stop_after_first_sleep(monkeypatch)
    monkeypatch.setattr(device, "connect_device", feed_device)
    stats: scrape.RunStats = {
        "new": 0,
        "metrics": {"new_stories": 0, "warning": None, "mem_peak_mb": 1843, "oom_kills": 1},
    }

    def fake_scrape_once(d: uidevice.Device, con: sqlite3.Connection) -> scrape.RunStats:
        return stats

    monkeypatch.setattr(scrape, "scrape_once", fake_scrape_once)
    with pytest.raises(StopLoop):
        scrape.main()
    con = sqlite3.connect(fast_offline / "posts.sqlite")
    assert sqlrows.values(fetch_row(con.execute("SELECT mem_peak_mb, oom_kills FROM runs"))) == (1843, 1)
