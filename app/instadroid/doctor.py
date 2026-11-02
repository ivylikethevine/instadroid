"""`scraper.py doctor`: one report of everything a bug report or a "why isn't it scraping" question
needs, without driving the device. It reads the database (runs, alerts, the control files, the daily
budget), asks the device over plain adb (boot state, Instagram version and whether it's running,
redroid's memory) and scans `adb logcat -d` for the crash signatures this project has hit, printing
the fix for each. It never launches Instagram or starts uiautomator2, so it's safe to run at any
time, mid-run included. scripts/diagnose.sh is the host-side counterpart, which can also read the
host's kernel log; this runs inside the app container."""

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime

from shared import sqlrows
from shared.errors import short_error
from shared.runs import run_result, run_seconds

from . import adbcli, alerts, config, control, db, device, diagnostics, scrape, versioning

# Runs `adb -s ADB_ADDR <args>`, returns stdout or None on failure.
type Adb = Callable[[list[str]], str | None]


def _duration(run: sqlite3.Row) -> str:
    seconds: int | None = run_seconds(run)
    return "?" if seconds is None else f"{seconds // 60}m{seconds % 60:02d}s"


def _runs_section(con: sqlite3.Connection, now: datetime) -> list[str]:
    lines: list[str] = ["== runs =="]
    rows: list[sqlite3.Row] = sqlrows.fetch_all(con.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 5"))
    if not rows:
        return [*lines, "no runs recorded yet"]
    r: sqlite3.Row
    for r in rows:
        severity: str
        result: str
        severity, result = run_result(r)
        if severity == "err":
            result = f"ERROR {result}"
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
    a: sqlite3.Row
    for a in open_alerts:
        lines.append(
            f"alert since {(sqlrows.cell_str(a, 'raised_at') or '?')[:16]}: {alerts.TITLES.get(sqlrows.must_str(a, 'kind'), '?')}:"
            f" {short_error(sqlrows.must_str(a, 'message'))}"
        )
    return lines


# The device section's questions, asked in one `adb shell`, each answer closed by a marker to split the
# output on. Ending on the marker's `echo` keeps the exit status 0 (pidof fails when nothing is running,
# and adb_run drops a failed command's output).
DEVICE_ANSWER_END: str = "--end-of-answer--"
DEVICE_QUERY: str = "; ".join(
    f"{command}; echo {DEVICE_ANSWER_END}"
    for command in (
        "getprop sys.boot_completed",
        "getprop ro.build.version.release",
        "getprop ro.product.name",
        f"dumpsys package {config.IG_PKG}",
        f"pidof {config.IG_PKG}",
        f"cat {' '.join(device.MEMORY_FILES)}",
    )
)


def _device_section(adb: Adb) -> list[str]:
    lines: list[str] = ["== device =="]
    state: str | None = adb(["get-state"])
    if state is None or state.strip() != "device":
        return [
            *lines,
            f"adb can't reach {config.ADB_ADDR} ({(state or 'no answer').strip()}): redroid still booting,"
            " or crashed before adb came up (scripts/diagnose.sh reads the host dmesg for that)",
        ]
    answers: list[str] = [a.strip() for a in (adb(["shell", DEVICE_QUERY]) or "").split(DEVICE_ANSWER_END)]
    booted: str
    release: str
    product: str
    dumpsys: str
    running: str
    memory: str
    booted, release, product, dumpsys, running, memory = (*answers, *[""] * 6)[:6]
    lines.append(f"adb: device, boot_completed={booted or '0'}")
    lines.append(f"Android {release or '?'} ({product or '?'})")
    version: str | None = device.parse_version_name(dumpsys)
    lines.append(f"Instagram: {version or 'not installed'}{' (running)' if running else ''}")
    reading: device.MemoryReading | None = device.parse_memory(memory)
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
    found: dict[str, str] = diagnostics.signatures_in(text)
    if not found:
        return [*lines, "none of the known crash-loop signatures in the current logcat"]
    return lines + [f"{text.count(sig)}x {sig!r}\n   fix: {fix}" for sig, fix in found.items()]


def report(con: sqlite3.Connection, adb: Adb = adbcli.adb_run, now: datetime | None = None) -> str:
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
