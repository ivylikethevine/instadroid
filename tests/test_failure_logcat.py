"""The filtered logcat saved after a device failure (diagnostics.save_failure_logcat)."""

import os
import subprocess
from pathlib import Path
from typing import NoReturn, Unpack

import adbutils
import pytest
from instadroid import (
    config,
    device,
    diagnostics,
    scrape,
)

from tests.deviceflows import (
    SAVE_FAILURE_LOGCAT,
    RunOptions,
    StopLoop,
    stop_after_first_sleep,
)

pytestmark: pytest.MarkDecorator = pytest.mark.usefixtures("fast_offline")

# --- failure logcat -------------------------------------------------------------------------------

LOGCAT: str = """\
09-14 17:16:39.100  1234  1250 I ActivityManager: Start proc 5678:com.instagram.android
09-14 17:16:39.200  1234  1250 D Something: chatter
09-14 17:16:40.000   512   530 E AndroidRuntime: FATAL EXCEPTION IN SYSTEM PROCESS: main
09-14 17:16:40.010   512   530 F libc    : Fatal signal 6 (SIGABRT)
09-14 17:16:41.000   400   400 I lowmemorykiller: Kill 'com.android.settings' (8123), uid 1000
09-14 17:16:42.000   512   540 W Watchdog: *** WATCHDOG KILLING SYSTEM PROCESS: Blocked in handler
"""


def test_filter_logcat_keeps_errors_fatals_and_known_signatures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "LOGCAT_TAIL_LINES", 2000)
    kept: list[str] = diagnostics._filter_logcat(LOGCAT)
    assert [line.split(": ", 1)[0].split()[-1] for line in kept] == [
        "AndroidRuntime",
        "libc",
        "lowmemorykiller",
        "Watchdog",
    ]
    monkeypatch.setattr(config, "LOGCAT_TAIL_LINES", 2)
    assert len(diagnostics._filter_logcat(LOGCAT)) == 2  # the tail, not the head
    assert "WATCHDOG KILLING" in diagnostics._filter_logcat(LOGCAT)[-1]


def test_save_failure_logcat_writes_a_filtered_file(
    fast_offline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Unpack[RunOptions]) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=LOGCAT, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    path: Path | None = SAVE_FAILURE_LOGCAT(
        "DeviceNotReady('could not bring com.instagram.android to the foreground')"
    )
    assert path is not None
    assert calls == [["adb", "-s", config.ADB_ADDR, "logcat", "-d", "-v", "threadtime"]]
    assert path.parent == config.DEBUG_DIR and path.name.startswith("logcat_") and path.suffix == ".txt"
    text: str = path.read_text()
    assert text.startswith("# run failed: DeviceNotReady('could not bring")
    assert "FATAL EXCEPTION" in text and "Something: chatter" not in text
    fixes: dict[str, str] = diagnostics.CRASH_SIGNATURES
    assert f"# seen 'WATCHDOG KILLING': {fixes['WATCHDOG KILLING']}" in text  # the fix diagnose.sh prints
    assert "Idmap" not in text


def test_crash_signatures_load_from_the_table_diagnose_sh_reads() -> None:
    signatures: dict[str, str] = diagnostics.load_crash_signatures()
    assert list(signatures)[:2] == ["WATCHDOG KILLING", "FATAL EXCEPTION"]
    assert "Can't downgrade database" in signatures and "Bad operation #" in signatures
    assert all(fix for fix in signatures.values())
    assert "reset-resource-cache.sh" in signatures["Version mismatch in Idmap"]


def test_crash_signature_table_skips_comments_and_blank_lines(tmp_path: Path) -> None:
    table: Path = tmp_path / "signatures.tsv"
    table.write_text("# signature\tfix\n\nOne thing\tdo this\nBare signature\n")
    assert diagnostics.load_crash_signatures(table) == {"One thing": "do this", "Bare signature": ""}


def test_save_failure_logcat_tolerates_an_unreachable_device(
    fast_offline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    def offline_run(cmd: list[str], **kwargs: Unpack[RunOptions]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="error: device offline")

    monkeypatch.setattr(subprocess, "run", offline_run)
    assert SAVE_FAILURE_LOGCAT("AdbError('offline')") is None
    assert not list(config.DEBUG_DIR.glob("logcat_*")) if config.DEBUG_DIR.exists() else True


def test_save_failure_logcat_tolerates_a_missing_or_hanging_adb(
    fast_offline: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def missing(cmd: list[str], **kwargs: Unpack[RunOptions]) -> NoReturn:
        raise FileNotFoundError("adb")

    def hanging(cmd: list[str], **kwargs: Unpack[RunOptions]) -> NoReturn:
        raise subprocess.TimeoutExpired(cmd, config.LOGCAT_TIMEOUT)

    monkeypatch.setattr(subprocess, "run", missing)
    assert SAVE_FAILURE_LOGCAT("AdbError('offline')") is None
    monkeypatch.setattr(subprocess, "run", hanging)
    assert SAVE_FAILURE_LOGCAT("AdbError('offline')") is None
    out: str = capsys.readouterr().out
    assert "could not read logcat after the failure: FileNotFoundError('adb')" in out
    assert "could not read logcat after the failure: TimeoutExpired(" in out
    assert not config.DEBUG_DIR.exists()


def test_save_failure_logcat_tolerates_an_unwritable_debug_dir(
    fast_offline: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    blocker: Path = fast_offline / "blocker"
    blocker.write_text("a file where DEBUG_DIR's parent should be")
    monkeypatch.setattr(config, "DEBUG_DIR", blocker / "debug")

    def fake_run(cmd: list[str], **kwargs: Unpack[RunOptions]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout=LOGCAT, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert SAVE_FAILURE_LOGCAT("AdbError('offline')") is None
    assert "WARN: could not write the failure logcat: NotADirectoryError(" in capsys.readouterr().out


def test_main_saves_a_logcat_only_for_device_failures(
    fast_offline: Path, monkeypatch: pytest.MonkeyPatch, no_real_logcat: list[str]
) -> None:
    def run_main_once(error: BaseException) -> None:
        stop_after_first_sleep(monkeypatch)

        def failing() -> NoReturn:
            raise error

        monkeypatch.setattr(device, "connect_device", failing)
        with pytest.raises(StopLoop):
            scrape.main()

    run_main_once(adbutils.AdbError("device 127.0.0.1:5555 not online"))
    assert len(no_real_logcat) == 1 and no_real_logcat[0].startswith("AdbError")
    run_main_once(RuntimeError("Instagram wants a human"))
    assert len(no_real_logcat) == 1  # a login challenge isn't a device failure


def test_failure_logcats_are_pruned_like_other_debug_files(
    fast_offline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "DEBUG_KEEP", 2)
    config.DEBUG_DIR.mkdir(parents=True)
    i: int
    for i in range(4):
        f: Path = config.DEBUG_DIR / f"logcat_2026091{i}T000000Z.txt"
        f.write_text("x")
        os.utime(f, (1_800_000_000 + i, 1_800_000_000 + i))
    monkeypatch.setattr(config, "DEBUG_RETAIN_DAYS", 0)
    diagnostics.prune_debug_dumps()
    assert sorted(f.name for f in config.DEBUG_DIR.iterdir()) == [
        "logcat_20260912T000000Z.txt",
        "logcat_20260913T000000Z.txt",
    ]
