"""What used to be two manual first-time steps, done by the app on its first connect after each
start: waiting for Android to finish booting, and the device tuning scripts/tune-android.sh does
by hand (animations off, sync and location off, the screen never sleeping, DEVICE_TIMEZONE, and the
unused Google/AOSP apps in tune_packages.txt disabled so they never sit resident: CLAUDE.md,
"Memory"). Every command is idempotent, so running it on every start costs one batched adb round
trip and changes nothing on an already-tuned device."""

import shlex
import subprocess
import time
from pathlib import Path

from . import config, uidevice
from .common import log

PACKAGES_FILE: Path = Path(__file__).with_name("tune_packages.txt")


def load_packages(path: Path = PACKAGES_FILE) -> list[str]:
    """The packages to `pm disable-user`, from the file scripts/tune-android.sh reads too."""
    return [
        line.strip() for line in path.read_text().splitlines() if line.strip() and not line.startswith("#")
    ]


def tune_commands(timezone: str = "", packages: list[str] | None = None) -> list[str]:
    """The device shell commands, in order, that tuning runs: the same ones as scripts/tune-android.sh."""
    commands: list[str] = []
    if timezone:
        commands.append(f"service call alarm 3 s16 {shlex.quote(timezone)}")
    commands += [
        "settings put global window_animation_scale 0",
        "settings put global transition_animation_scale 0",
        "settings put global animator_duration_scale 0",
        "settings put global auto_sync 0",
        "settings put system screen_off_timeout 2147483647",
        "settings put secure location_mode 0",
    ]
    commands += [f"pm disable-user --user 0 {p}" for p in (load_packages() if packages is None else packages)]
    return commands


_tuned: bool = False  # once per process: the app's first connect after a start


def tune_device(d: uidevice.Device, *, force: bool = False) -> bool:
    """Apply tune_commands() in one shell round trip, once per process unless `force`. Best-effort: a
    failure is logged, never raised, since an untuned device still scrapes. True when it ran."""
    global _tuned
    if _tuned and not force:
        return False
    if not config.TUNE_ON_CONNECT and not force:
        return False
    commands: list[str] = tune_commands(config.DEVICE_TIMEZONE)
    # Each command echoes + or - so the device's own exit statuses come back in one round trip.
    script: str = "; ".join(f"({c} >/dev/null 2>&1 && echo +) || echo -" for c in commands)
    try:
        out: str = d.shell(script, timeout=120).output or ""
    except Exception as e:
        log("WARN: device tuning failed (scripts/tune-android.sh does the same by hand):", repr(e))
        return False
    _tuned = True
    marks: list[str] = out.split()
    applied: int = marks.count("+")
    failed: list[str] = [c for c, mark in zip(commands, marks, strict=False) if mark == "-"]
    log(
        f"device tuned: {applied} of {len(commands)} commands applied ({len(load_packages())} packages in the list)"
    )
    if failed:
        log("WARN: tuning commands that failed on the device:", "; ".join(failed[:8]))
    return True


def wait_for_boot(addr: str, timeout: float) -> bool:
    """Poll `sys.boot_completed` over the adb CLI until Android has booted or `timeout` seconds pass.
    True when it has. A freshly started redroid takes from seconds to minutes (CLAUDE.md, "redroid boot
    and logs"); without this every `docker compose up` spent its first retries on a booting device."""
    deadline: float = time.monotonic() + timeout
    waited: bool = False
    while True:
        try:
            # `adb connect` again each time: before adbd listens the first connect just fails, and a
            # host:port that never handshook has no transport for the getprop to reach.
            _: subprocess.CompletedProcess[str] = subprocess.run(
                ["adb", "connect", addr], capture_output=True, text=True, timeout=15
            )
            out: subprocess.CompletedProcess[str] = subprocess.run(
                ["adb", "-s", addr, "shell", "getprop", "sys.boot_completed"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            booted: bool = out.returncode == 0 and out.stdout.strip() == "1"
        except OSError, subprocess.TimeoutExpired:
            booted = False
        if booted:
            if waited:
                log("Android has finished booting")
            return True
        if time.monotonic() >= deadline:
            log(
                f"WARN: Android hasn't finished booting after {timeout:.0f}s (BOOT_WAIT_SECONDS); going on anyway"
            )
            return False
        if not waited:
            log(f"waiting for Android to finish booting (up to {timeout:.0f}s)")
            waited = True
        time.sleep(5)
