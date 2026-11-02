"""The adb CLI, for what must not depend on the uiautomator2 connection, which is often exactly what
failed."""

import subprocess

from . import config


def adb_call(args: list[str], addr: str, timeout: float) -> subprocess.CompletedProcess[str]:
    """`adb -s <addr> <args>`. Raises OSError when adb can't run, TimeoutExpired when it hangs."""
    return subprocess.run(
        ["adb", "-s", addr, *args],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=timeout,
    )


def adb_run(args: list[str]) -> str | None:
    """`adb -s ADB_ADDR <args>`'s stdout; None when adb can't run or the device doesn't answer."""
    try:
        out: subprocess.CompletedProcess[str] = adb_call(args, config.ADB_ADDR, config.LOGCAT_TIMEOUT)
    except OSError, subprocess.TimeoutExpired:
        return None
    return out.stdout if out.returncode == 0 else None
