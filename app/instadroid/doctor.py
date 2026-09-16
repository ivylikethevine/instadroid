"""`scraper.py doctor`: one report of everything a bug report or a "why isn't it scraping" question
needs, without driving the device. It reads the database (runs, alerts, the control files, the daily
budget), asks the device over plain adb (boot state, Instagram version and whether it's running,
redroid's memory) and scans `adb logcat -d` for the crash signatures this project has hit, printing
the fix for each. It never launches Instagram or starts uiautomator2, so it's safe to run at any
time, mid-run included. scripts/diagnose.sh is the host-side counterpart, which can also read the
host's kernel log; this runs inside the app container."""

import sqlite3
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime

from shared import sqlrows
from shared.errors import short_error
from shared.timestamps import parse_iso

from . import alerts, config, control, db, device, diagnostics, scrape, versioning

Adb = Callable[[list[str]], str | None]  # runs `adb -s ADB_ADDR <args>`, returns stdout or None on failure


def adb_run(args: list[str]) -> str | None:
    """`adb -s ADB_ADDR <args>` through the adb CLI (the uiautomator2 connection is often exactly what
    failed); None when adb can't run or the device doesn't answer."""
    try:
        out: subprocess.CompletedProcess[str] = subprocess.run(
            ["adb", "-s", config.ADB_ADDR, *args],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=config.LOGCAT_TIMEOUT,
        )
    except OSError, subprocess.TimeoutExpired:
        return None
    return out.stdout if out.returncode == 0 else None


def _duration(run: sqlite3.Row) -> str:
    start: datetime | None = parse_iso(sqlrows.cell_str(run, "started_at"))
    end: datetime | None = parse_iso(sqlrows.cell_str(run, "finished_at"))
    if not start or not end:
        return "?"
    return f"{int((end - start).total_seconds()) // 60}m{int((end - start).total_seconds()) % 60:02d}s"


def _runs_section(con: sqlite3.Connection, now: datetime) -> list[str]:
    lines: list[str] = ["== runs =="]
    rows: list[sqlite3.Row] = sqlrows.fetch_all(con.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 5"))
    if not rows:
        return [*lines, "no runs recorded yet"]
    for r in rows:
        error: str | None = sqlrows.cell_str(r, "error")
        warning: str | None = sqlrows.cell_str(r, "warning") if sqlrows.has_column(r, "warning") else None
        result: str = (
            f"ERROR {short_error(error)}" if error else f"warn: {short_error(warning)}" if warning else "ok"
        )
        peak: int | None = sqlrows.opt_int(r, "mem_peak_mb")
        lines.append(
            f"{(sqlrows.cell_str(r, 'started_at') or '?')[:16]}  {_duration(r):>7}  new={sqlrows.cell_int(r, 'new_posts') or 0}"
            f"  ig={sqlrows.cell_str(r, 'ig_version') or '?'}  peak={f'{peak}MiB' if peak is not None else '?'}  {result}"
        )
    failures: int = db.consecutive_failures(con)
    if failures:
        lines.append(
            f"{failures} failed run(s) in a row: the poll interval is backing off (FAILURE_BACKOFF_MAX_HOURS)"
        )
    budget: float = scrape.budget_wait_seconds(con, now)
    if budget:
        lines.append(
            f"daily budget reached (MAX_RUNS_PER_DAY={config.MAX_RUNS_PER_DAY}): next run in {budget / 3600:.1f}h"
        )
    return lines


def _control_section(con: sqlite3.Connection) -> list[str]:
    lines: list[str] = ["== control =="]
    hold: str | None = control.hold_reason()
    if hold is not None:
        lines.append(f"HELD until `scraper.py unlock`: Instagram needs a person: {short_error(hold)}")
    elif control.locked():
        lines.append("manual lock in place (scraper.py unlock releases it)")
    else:
        lines.append("not locked")
    open_alerts: list[sqlite3.Row] = sqlrows.fetch_all(
        con.execute("SELECT kind, message, raised_at FROM alerts ORDER BY raised_at")
    )
    for a in open_alerts:
        lines.append(
            f"alert since {(sqlrows.cell_str(a, 'raised_at') or '?')[:16]}: {alerts.TITLES.get(sqlrows.must_str(a, 'kind'), '?')}:"
            f" {short_error(sqlrows.must_str(a, 'message'))}"
        )
    return lines


def _device_section(adb: Adb) -> list[str]:
    lines: list[str] = ["== device =="]
    state: str | None = adb(["get-state"])
    if state is None or state.strip() != "device":
        return [
            *lines,
            f"adb can't reach {config.ADB_ADDR} ({(state or 'no answer').strip()}): redroid still booting,"
            " or crashed before adb came up (scripts/diagnose.sh reads the host dmesg for that)",
        ]
    booted: str = (adb(["shell", "getprop", "sys.boot_completed"]) or "").strip()
    lines.append(f"adb: device, boot_completed={booted or '0'}")
    props: str = adb(["shell", "getprop ro.build.version.release; getprop ro.product.name"]) or ""
    release: str
    _: str
    product: str
    release, _, product = props.strip().partition("\n")
    lines.append(f"Android {release or '?'} ({product.strip() or '?'})")
    dumpsys: str = adb(["shell", "dumpsys", "package", config.IG_PKG]) or ""
    version: str | None = next(
        (t.split("=", 1)[1] for t in dumpsys.split() if t.startswith("versionName=")), None
    )
    running: str = (adb(["shell", "pidof", config.IG_PKG]) or "").strip()
    lines.append(f"Instagram: {version or 'not installed'}{' (running)' if running else ''}")
    reading: device.MemoryReading | None = device.parse_memory(
        adb(["shell", "cat", *device.MEMORY_FILES]) or ""
    )
    if reading:  # the guard's accounting (file cache excluded), so it matches `peak=` above
        limit: str = f"{reading['max'] // 2**20} MiB" if reading["max"] is not None else "no limit"
        lines.append(
            f"redroid memory: {reading['current'] // 2**20} MiB of {limit}, {reading['oom_kill']} OOM kill(s)"
            " since the container started"
        )
    return lines


def _logcat_section(adb: Adb) -> list[str]:
    lines: list[str] = ["== logcat crash signatures =="]
    text: str | None = adb(["logcat", "-d", "-v", "threadtime"])
    if not text:
        return [*lines, "could not read logcat"]
    found: bool = False
    for signature, fix in diagnostics.CRASH_SIGNATURES.items():
        count: int = text.count(signature)
        if count:
            found = True
            lines.append(f"{count}x {signature!r}\n   fix: {fix}")
    if not found:
        lines.append("none of the known crash-loop signatures in the current logcat")
    return lines


def report(con: sqlite3.Connection, adb: Adb = adb_run, now: datetime | None = None) -> str:
    """The whole report as text; `adb` is swapped out by the tests."""
    at: datetime = now or datetime.now(UTC)
    sections: list[list[str]] = [
        [f"instadroid doctor  {at.isoformat(timespec='seconds')}  profile={versioning.PROFILE.name}"],
        _control_section(con),
        _runs_section(con, at),
        _device_section(adb),
        _logcat_section(adb),
        ["scrub usernames and captions before pasting this anywhere public (docs/SUPPORT.md)"],
    ]
    return "\n".join("\n".join(s) for s in sections) + "\n"
