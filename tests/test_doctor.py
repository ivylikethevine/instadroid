"""`scraper.py doctor`: the report reads the database and plain adb, and never drives the device."""

import sqlite3
import subprocess

import pytest
from instadroid import adbcli, config, control, db, doctor

from tests.support import fake_run, record_run_ago


def _adb(answers: dict[str, str]) -> doctor.Adb:
    """A fake adb answering by the joined argument string; anything else fails (None)."""

    def run(args: list[str]) -> str | None:
        return answers.get(" ".join(args))

    return run


def _device_answers(running: str = "") -> str:
    """The output of doctor.DEVICE_QUERY: each answer followed by the marker line."""
    answers: tuple[str, ...] = (
        "1\n",
        "13\n",
        "redroid_x86_64\n",
        "  Package [com.instagram.android]\n    versionName=445.0.0.45.83\n",
        running,
        f"{1500 * 2**20}\n{3 * 2**30}\noom_kill 2\ninactive_file {300 * 2**20}\n",
    )
    return "".join(f"{answer}{doctor.DEVICE_ANSWER_END}\n" for answer in answers)


HEALTHY: dict[str, str] = {
    "get-state": "device\n",
    f"shell {doctor.DEVICE_QUERY}": _device_answers(),
    "logcat -d -v threadtime": "09-16 10:00:00.000  1 1 I ActivityManager: fine\n",
}


def test_doctor_reports_runs_control_device_and_a_clean_logcat(con: sqlite3.Connection) -> None:
    record_run_ago(con, 300, mem_peak_mb=2100)
    record_run_ago(con, 60, "RuntimeError('no posts parsed')")
    out: str = doctor.report(con, _adb(HEALTHY))
    assert "== control ==\nnot locked" in out
    assert "new=0  ig=?  peak=2100MiB  ok" in out and "ERROR RuntimeError('no posts parsed')" in out
    assert "1 failed run(s) in a row" in out
    assert "adb: device, boot_completed=1" in out and "Android 13 (redroid_x86_64)" in out
    assert (
        "Instagram: 445.0.0.45.83\n" in out and "redroid memory: 1200 MiB of 3072 MiB, 2 OOM kill(s)" in out
    )  # the guard's accounting
    assert "none of the known crash-loop signatures" in out
    assert "scrub usernames" in out


def test_doctor_shows_the_hold_alerts_budget_and_crash_signatures(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "MAX_RUNS_PER_DAY", 1)
    record_run_ago(con, 30, "RuntimeError(\"Instagram wants a human: 'Confirm it's you' screen\")")
    control.set_hold("RuntimeError(\"Instagram wants a human: 'Confirm it's you' screen\")")
    con.execute(
        "INSERT INTO alerts (kind, message, raised_at) VALUES ('login', 'finish it in scrcpy', '2026-09-16T10:00:00+00:00')"
    )
    con.commit()
    answers: dict[str, str] = {
        **HEALTHY,
        f"shell {doctor.DEVICE_QUERY}": _device_answers(running="4321\n"),
        "logcat -d -v threadtime": "09-16 10:00:00.000  1 1 E AppOps: Bad operation #133\n" * 3,
    }
    out: str = doctor.report(con, _adb(answers))
    assert "HELD until `scraper.py unlock`: Instagram needs a person" in out
    assert "alert since 2026-09-16T10:00: Instagram wants a human: finish it in scrcpy" in out
    assert "daily budget reached (MAX_RUNS_PER_DAY=1)" in out
    assert "Instagram: 445.0.0.45.83 (running)" in out
    assert "3x 'Bad operation #'" in out and "fix: corrupt appops.xml" in out


def test_doctor_when_adb_cannot_reach_the_device(con: sqlite3.Connection) -> None:
    out: str = doctor.report(con, _adb({}))
    assert "no runs recorded yet" in out
    assert f"adb can't reach {config.ADB_ADDR} (no answer)" in out and "could not read logcat" in out


def test_doctor_reports_a_manual_lock_and_a_run_with_unreadable_timestamps(con: sqlite3.Connection) -> None:
    control.set_lock(True)
    db.record_run(con, "2026-09-16T10:00:00+00:00", "not-a-timestamp", 0, None, {})
    out: str = doctor.report(con, _adb(HEALTHY))
    assert "manual lock in place (scraper.py unlock releases it)" in out
    assert "2026-09-16T10:00        ?  new=0" in out  # no duration without both timestamps


def test_adb_run_returns_stdout_only_when_adb_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = fake_run(monkeypatch, stdout="device\n")
    assert adbcli.adb_run(["get-state"]) == "device\n"
    assert calls == [["adb", "-s", config.ADB_ADDR, "get-state"]]
    fake_run(monkeypatch, returncode=1, stderr="error: device offline")
    assert adbcli.adb_run(["get-state"]) is None


@pytest.mark.parametrize(
    "failure",
    [FileNotFoundError("adb"), subprocess.TimeoutExpired(["adb"], config.LOGCAT_TIMEOUT)],
    ids=["missing", "hanging"],
)
def test_adb_run_answers_none_when_adb_is_missing_or_hangs(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    fake_run(monkeypatch, raises=failure)
    assert adbcli.adb_run(["get-state"]) is None
