"""What the app does on its first connect after each start: waiting for Android to finish booting, and
the device tuning `scraper.py tune` also runs by hand (animations off, sync and location off, the screen
never sleeping, the timezone, and the unused Google/AOSP apps in tune_packages.txt disabled so they never
sit resident: CLAUDE.md, "Memory"). Every command is idempotent, so running it on every start costs one
batched adb round trip and changes nothing on an already-tuned device."""

import shlex
import subprocess
import time
from pathlib import Path

from . import adbcli, config, uidevice
from .common import log

PACKAGES_FILE: Path = Path(__file__).with_name("tune_packages.txt")


def load_packages() -> list[str]:
    """The packages to `pm disable-user`."""
    return [
        line.strip()
        for line in PACKAGES_FILE.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]


def tune_commands(timezone: str, packages: list[str]) -> list[str]:
    """The device shell commands, in order, that tuning runs."""
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
    commands += [f"pm disable-user --user 0 {p}" for p in packages]
    return commands


_tuned: bool = False  # once per process: the app's first connect after a start


def reported(command: str) -> str:
    """`command` wrapped to answer on one line: `+` when it worked, else `-<exit status> <its output>`
    (newlines flattened, cut to 300 characters), so the device's reasons come back in the same round
    trip. No "; " inside, so the commands stay "; "-separated."""
    return (
        f"o=$({command} 2>&1);r=$?;"
        "if [ $r -eq 0 ];then echo +;"
        "else echo \"-$r $(echo \"$o\"|tr '\\n' ' '|cut -c1-300)\";fi"
    )


def tune_device(d: uidevice.Device, *, force: bool = False) -> bool:
    """Apply tune_commands() in one shell round trip, once per process unless `force`. Best-effort: a
    failure is logged, never raised, since an untuned device still scrapes. True when it ran."""
    global _tuned
    if _tuned and not force:
        return False
    if not config.TUNE_ON_CONNECT and not force:
        return False
    packages: list[str] = load_packages()
    commands: list[str] = tune_commands(config.DEVICE_TIMEZONE, packages)
    script: str = "; ".join(reported(c) for c in commands)
    try:
        out: str = d.shell(script, timeout=120).output or ""
    except Exception as e:
        log("WARN: device tuning failed (`scraper.py tune` runs it again by hand):", repr(e))
        return False
    _tuned = True
    answers: list[str] = [line.strip() for line in out.splitlines() if line.strip()]
    applied: int = answers.count("+")
    rejected: list[tuple[str, str]] = [
        (c, a[1:]) for c, a in zip(commands, answers, strict=False) if a.startswith("-")
    ]
    # A listed package the image doesn't ship (the Google apps on an AOSP image) is nothing to disable.
    absent: int = sum(1 for _, a in rejected if "Unknown package:" in a)
    failed: list[tuple[str, str]] = [(c, a) for c, a in rejected if "Unknown package:" not in a]
    log(
        f"device tuned: {applied} of {len(commands)} commands applied, {absent} for packages not"
        f" installed, {len(failed)} failed ({len(packages)} packages in the list)"
    )
    if len(answers) != len(commands):
        log(f"WARN: tuning got {len(answers)} answers for {len(commands)} commands:", repr(out[-300:]))
    command: str
    answer: str
    for command, answer in failed:
        status: str
        reason: str
        status, _, reason = answer.partition(" ")
        reason = reason.split("\tat ")[0].strip()  # the Java stack trace adds nothing to the exception
        log(f"WARN: tuning command failed, exit {status}: {command}: {reason or '(no output)'}")
    return True


def tuned() -> bool:
    """Whether this process has tuned the device already (connect_device() does, unless
    TUNE_ON_CONNECT is off)."""
    return _tuned


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
            out: subprocess.CompletedProcess[str] = adbcli.adb_call(
                ["shell", "getprop", "sys.boot_completed"], addr, 15
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
