"""The uiautomator2 connection: errors, app launch, human-like timing and gestures, and the
device-memory housekeeping (force-stops, cgroup memory guard)."""

import math
import os
import random
import re
import time
from collections.abc import Callable, Iterable, Iterator
from datetime import UTC, datetime, timezone
from typing import TypedDict
from weakref import WeakKeyDictionary
from zoneinfo import ZoneInfo

import adbutils
import uiautomator2 as u2
from uiautomator2.exceptions import DeviceError as U2DeviceError

from . import config, tune, uidevice, versioning
from .common import log
from .versioning import versioned


class DeviceSnapshot(TypedDict, total=False):
    """Device and app versions recorded with each run (runs table columns)."""

    android_release: str | None
    android_sdk: str | None
    device_product: str | None
    ig_version: str | None
    redroid_image: str | None
    selector_profile: str


class MemoryReading(TypedDict):
    current: int  # bytes in use, excluding reclaimable file cache
    max: int | None  # the container's limit; None when unlimited
    oom_kill: int  # kernel OOM kills in this container since it started


class DeviceNotReady(RuntimeError):
    """The device or app isn't in a state to be driven yet (e.g. Instagram won't come to the
    foreground). Unlike a login challenge, a retry a few minutes later usually just works."""


def is_transient(e: BaseException) -> bool:
    """Device-side failures that a short wait usually fixes: redroid still booting, adb briefly
    offline, or the uiautomator server failing to attach to an accessibility manager that isn't up
    yet (seen live as LaunchUiAutomationError 'server quit unexpectly', five runs in a row). Login
    challenges and parsing/logic errors are deliberately not transient: retrying those early either
    can't help or, for a challenge, looks worse to Instagram."""
    return isinstance(e, _TRANSIENT)


_TRANSIENT: tuple[
    type[DeviceNotReady], type[adbutils.AdbError], type[adbutils.AdbTimeout], type[U2DeviceError]
] = (DeviceNotReady, adbutils.AdbError, adbutils.AdbTimeout, U2DeviceError)


def transient_error_names() -> set[str]:
    """Class names is_transient() accepts, subclasses included, for matching a stored runs.error
    (which is repr(exception), so it starts with the class name)."""

    def walk(cls: type[BaseException]) -> Iterator[type[BaseException]]:
        yield cls
        sub: type[BaseException]
        for sub in cls.__subclasses__():
            yield from walk(sub)

    return {c.__name__ for base in _TRANSIENT for c in walk(base)}


# Readings remembered per connection (see the two functions below), keyed on the device object itself
# (weakly, so a finished run's connection, or a test's fake device, takes its entries with it).
_window_sizes: WeakKeyDictionary[uidevice.Device, tuple[int, int]] = WeakKeyDictionary()
_ig_versions: WeakKeyDictionary[uidevice.Device, str | None] = WeakKeyDictionary()


def window_size(d: uidevice.Device) -> tuple[int, int]:
    """d.window_size(), read once per connection: uiautomator2 runs two shell commands for it, and
    redroid's resolution and rotation are fixed."""
    size: tuple[int, int] | None
    if (size := _window_sizes.get(d)) is None:
        size = _window_sizes[d] = d.window_size()
    return size


def instagram_version(d: uidevice.Device, *, fresh: bool = False) -> str | None:
    """The installed Instagram versionName, or None if it isn't installed or adb misbehaves. The
    `dumpsys package` behind it is large, so a successful read is remembered for the connection;
    `fresh` reads again (install.py, after installing)."""
    if not fresh and d in _ig_versions:
        return _ig_versions[d]
    try:
        m: re.Match[str] | None = re.search(
            r"versionName=(\S+)", d.shell(["dumpsys", "package", config.IG_PKG]).output or ""
        )
    except Exception:
        return None
    version: str | None
    version = _ig_versions[d] = m.group(1) if m else None
    return version


def device_snapshot(d: uidevice.Device) -> DeviceSnapshot:
    """Best-effort device/app versions for the runs table and status page: ro.build.* props, the
    installed Instagram versionName, and the redroid image tag compose passes in — so "which
    Instagram update broke the selectors" is a lookup. Uses plain adb shell calls rather than
    uiautomator2's jsonrpc info, so a wedged automation service can't also blank this out."""

    def prop(name: str) -> str | None:
        try:
            return d.shell(f"getprop {name}").output.strip() or None
        except Exception:
            return None

    return {
        "android_release": prop("ro.build.version.release"),
        "android_sdk": prop("ro.build.version.sdk"),
        "device_product": prop("ro.product.name") or prop("ro.build.product"),
        "ig_version": instagram_version(d),
        "redroid_image": os.environ.get("REDROID_IMAGE") or None,
    }


def connect_device() -> uidevice.Device:
    log("connecting to", config.ADB_ADDR)
    adbutils.adb.connect(config.ADB_ADDR, timeout=30)
    tune.wait_for_boot(config.ADB_ADDR, config.BOOT_WAIT_SECONDS)
    d: uidevice.Device = u2.connect(config.ADB_ADDR)
    d.implicitly_wait(10)
    log("device:", d.info.get("productName"), window_size(d))
    tune.tune_device(d)  # once per process; a no-op on an already-tuned device
    versioning.activate_profile(instagram_version(d))
    return d


@versioned
def launch_app(d: uidevice.Device) -> None:
    """Bring IG_PKG to the foreground. uiautomator2's app_start() defaults to `monkey -c
    LAUNCHER` when no activity is given, which on this device silently no-ops (exit code 251,
    launcher stays focused) — Instagram ships many enabled/disabled activity-aliases for seasonal
    icon themes (`.activity.MainTabActivity.kpop`, `.flame`, `.slime`, ...), and category-based
    resolution (`am start -c LAUNCHER` too) can't disambiguate them. `pm resolve-activity` returns
    the one actually enabled, so start that explicit component instead — falling back to monkey
    only if resolution itself fails."""
    try:
        out: str = d.shell(["cmd", "package", "resolve-activity", "--brief", config.IG_PKG]).output
        activity: str = out.strip().splitlines()[-1].split("/", 1)[1]
        d.app_start(config.IG_PKG, activity=activity, stop=False)
    except Exception as e:
        log(f"WARN: resolve-activity failed ({e!r}); falling back to monkey launch")
        d.app_start(config.IG_PKG, stop=False)


def in_foreground(d: uidevice.Device) -> bool:
    """True when Instagram is the app in front."""
    return d.app_current().get("package") == config.IG_PKG


def ensure_foreground(d: uidevice.Device) -> bool:
    """Relaunch Instagram if something else is in front. True if it had to."""
    if in_foreground(d):
        return False
    launch_app(d)
    human_pause(3, 5)
    return True


def _in_quiet_hours(now: datetime) -> bool:
    """True during the configured local quiet window (only consulted by the "daynight"
    distribution below). Uses DEVICE_TIMEZONE so "local" reflects the account's apparent timezone,
    not the container's own clock, which stays UTC regardless of DEVICE_TIMEZONE."""
    tz: ZoneInfo | timezone = ZoneInfo(config.DEVICE_TIMEZONE) if config.DEVICE_TIMEZONE else UTC
    local_hour: int = now.astimezone(tz).hour
    if config.DAYNIGHT_QUIET_START <= config.DAYNIGHT_QUIET_END:
        return config.DAYNIGHT_QUIET_START <= local_hour < config.DAYNIGHT_QUIET_END
    return (
        local_hour >= config.DAYNIGHT_QUIET_START or local_hour < config.DAYNIGHT_QUIET_END
    )  # wraps midnight


def sample_duration(lo: float, hi: float, now: datetime | None = None) -> float:
    """Draw a duration in [lo, hi] per TIME_DISTRIBUTION. Used for every pause (human_pause,
    human_scroll's swipe duration) and the inter-run poll interval — the two things a
    timing-analysis detector could actually observe, per the roadmap's "configurable time-fuzzing".

      - "uniform" (default): random.uniform(lo, hi) — the original, unchanged behavior.
      - "lognormal": a heavier-tailed, more human-like shape than a flat range — most draws cluster
        near the midpoint, with an occasional longer outlier, instead of every value in [lo, hi]
        being equally likely.
      - "daynight": like "lognormal", but during DAYNIGHT_QUIET_START..DAYNIGHT_QUIET_END local
        hours (default 0-6, i.e. "asleep") the top of the range is stretched, so activity actually
        thins out overnight instead of keeping the same rhythm around the clock.
    """
    if config.TIME_DISTRIBUTION == "uniform" or hi <= lo:
        return random.uniform(lo, hi)
    effective_hi: float = hi
    if config.TIME_DISTRIBUTION == "daynight" and _in_quiet_hours(now or datetime.now(UTC)):
        effective_hi = hi + (hi - lo)
    mid: float = (lo + effective_hi) / 2
    mu: float
    sigma: float
    mu, sigma = math.log(max(mid, 1e-6)), 0.5
    v: float = mid
    for _ in range(8):  # resample a rare out-of-range draw rather than bias the shape by clipping
        v = random.lognormvariate(mu, sigma)
        if lo <= v <= effective_hi:
            return v
    return min(max(v, lo), effective_hi)  # give up after 8 tries, clip instead


def human_pause(lo: float = 1.0, hi: float = 3.0) -> None:
    time.sleep(sample_duration(lo, hi))


def swipe_duration() -> float:
    # Slow enough not to fling: a fling scrolls several screens and skips whole posts.
    return sample_duration(config.SCROLL_SWIPE_MIN, config.SCROLL_SWIPE_MAX)


def human_scroll(
    d: uidevice.Device, start: tuple[float, float] = (0.65, 0.8), distance: tuple[float, float] = (0.3, 0.45)
) -> None:
    """Scroll up by a random amount at a random speed, like a thumb would. `start` and `distance`
    are fractions of screen height; the defaults are tuned for feed cards."""
    w: int
    h: int
    w, h = window_size(d)
    x: int = random.randint(int(w * 0.3), int(w * 0.7))
    y1: int = random.randint(int(h * start[0]), int(h * start[1]))
    y2: int = y1 - random.randint(int(h * distance[0]), int(h * distance[1]))
    d.swipe(x, y1, x, y2, duration=swipe_duration())


def human_scroll_list(d: uidevice.Device) -> None:
    """A shorter human_scroll() for the Following list: its ~190px rows are much shorter than a feed
    card, and the feed distance was seen live (2026-09-11) to skip ~3 of 30 accounts per refresh.
    Overlapping screens keep every row on screen for at least one dump."""
    human_scroll(d, start=(0.55, 0.65), distance=(0.15, 0.25))


def first(d: uidevice.Device, **kinds: Iterable[str]) -> uidevice.Selector | None:
    """Return the first existing selector among the given candidate lists."""
    kind: str
    values: Iterable[str]
    for kind, values in kinds.items():
        v: str
        for v in values:
            sel: uidevice.Selector = d(**{kind: v})
            if sel.exists(timeout=1):
                return sel
    return None


# Cached-tier system apps left enabled by tune-android.sh (see there for why: com.android.settings
# is core, the rest may be needed again after a /data reset or a real permission/keychain prompt),
# but that this container's lmkd never reclaims on its own — it judges free memory against the
# host, not the container's mem_limit, so a once-launched cached app just sits there for the rest
# of the container's life (see docs/INCIDENTS.md, "Reducing idle memory"). Force-stopped here instead:
# unlike pm disable-user, this only kills the current process, so whatever needs one again just
# relaunches it — no risk of the packageinstaller-style "required singleton" crash from disabling.
CACHED_APP_SWEEP: tuple[str, str, str, str, str] = (
    "com.android.settings",
    "com.android.permissioncontroller",
    "com.android.managedprovisioning",
    "com.android.keychain",
    "com.android.externalstorage",
)


def force_stop(d: uidevice.Device, *pkgs: str) -> None:
    """Force-stop packages in one adb round trip; `;` keeps going past one that fails. Best-effort:
    an adb failure is logged, never raised."""
    try:
        d.shell("; ".join(f"am force-stop {pkg}" for pkg in pkgs))
    except Exception as e:
        log(f"WARN: could not force-stop {', '.join(pkgs)}:", repr(e))


def _redroid_memory(d: uidevice.Device) -> MemoryReading | None:
    """redroid's own container memory, read through adb from the cgroup v2 files the container sees
    as /sys/fs/cgroup (readable by the adb shell user): {"current": bytes, "max": bytes or None when
    unlimited, "oom_kill": kernel OOM kills in this container since it started}. None when the files
    aren't there (e.g. a cgroup v1 host) or adb fails, which turns the memory guard off.

    "current" excludes inactive file cache, the same working-set figure `docker stats` shows: the
    kernel reclaims that cache before it ever OOM-kills, so counting it stopped a run at "2756 of
    3072 MiB" while the real usage was ~2.3GiB (2026-09-14)."""
    try:
        return parse_memory(d.shell(["cat", *MEMORY_FILES]).output or "")
    except Exception:
        return None


MEMORY_FILES: tuple[str, str, str, str] = (
    "/sys/fs/cgroup/memory.current",
    "/sys/fs/cgroup/memory.max",
    "/sys/fs/cgroup/memory.events",
    "/sys/fs/cgroup/memory.stat",
)


def parse_memory(out: str) -> MemoryReading | None:
    """The reading in `cat MEMORY_FILES` output (the guard's and `scraper.py doctor`'s one accounting),
    or None when it isn't cgroup v2 output."""
    try:
        lines: list[str] = out.split()
        usage: int
        limit: str
        usage, limit = int(lines[0]), lines[1]
        # memory.events and memory.stat are both "key value" lines, so one dict covers both.
        stats: dict[str, str] = dict(zip(lines[2::2], lines[3::2], strict=False))
        return {
            "current": max(0, usage - int(stats.get("inactive_file", 0))),
            "max": None if limit == "max" else int(limit),
            "oom_kill": int(stats.get("oom_kill", 0)),
        }
    except IndexError, ValueError:
        return None


class MemoryGuard:
    """Tracks redroid's memory across one run: the peak seen, OOM kills since the run started, and
    whether usage has crossed MEMORY_GUARD_PERCENT of the container's limit. Every method is a no-op
    when _redroid_memory() can't read the cgroup."""

    def __init__(self, d: uidevice.Device) -> None:
        self.d = d
        self.peak: int | None = None
        first: MemoryReading | None = self._read()
        self.oom_kill_start = first["oom_kill"] if first else None

    def _read(self) -> MemoryReading | None:
        m: MemoryReading | None = _redroid_memory(self.d)
        if m:
            self.peak = max(self.peak or 0, m["current"])
        return m

    def exceeded(self) -> str | None:
        """A reason string when usage is at or over the guard threshold, else None."""
        m: MemoryReading | None = self._read()
        if not (config.MEMORY_GUARD_PERCENT and m and m["max"]):
            return None
        if m["current"] < m["max"] * config.MEMORY_GUARD_PERCENT / 100:
            return None
        mib: int = 1024 * 1024
        return (
            f"redroid memory at {m['current'] // mib} of {m['max'] // mib} MiB"
            f" (MEMORY_GUARD_PERCENT={config.MEMORY_GUARD_PERCENT:g})"
        )

    def peak_mb(self) -> int | None:
        return self.peak // (1024 * 1024) if self.peak is not None else None

    def oom_kills(self) -> int | None:
        m: MemoryReading | None = self._read()
        if not m or self.oom_kill_start is None:
            return None
        return max(0, m["oom_kill"] - self.oom_kill_start)


class RunClock:
    """The run's time budget (RUN_MAX_MINUTES), checked alongside MemoryGuard: exceeded() names the
    overrun once the run has been going longer than that. `clock` is time.monotonic() unless a test
    supplies its own."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock: Callable[[], float] = clock
        self.started: float = clock()

    def exceeded(self) -> str | None:
        if config.RUN_MAX_MINUTES <= 0:
            return None
        minutes: float = (self.clock() - self.started) / 60
        if minutes < config.RUN_MAX_MINUTES:
            return None
        return f"run has taken {minutes:.0f} min (RUN_MAX_MINUTES={config.RUN_MAX_MINUTES:g})"


def free_device_memory(d: uidevice.Device) -> None:
    """Force-stop the cached system apps and Instagram itself, before a run (so it never starts on
    top of a still-resident Instagram, e.g. left open by `scraper.py login`) and after it. Instagram
    plus its :fbns process measured ~820MiB resident and lmkd never reclaims it here; the next run
    cold-launches it anyway, and the login session lives in /data (see docs/INCIDENTS.md)."""
    force_stop(d, *CACHED_APP_SWEEP, config.IG_PKG)
