"""scraper.py's subcommands, run the way `python scraper.py ...` runs them, with the device, the scrape and
the installer replaced so nothing reaches a real device."""

import runpy
import sqlite3
import sys
from pathlib import Path

import pytest
from devtools import ROOT
from instadroid import config, db, device, diagnostics, install, navigation, scrape

from tests.fakedevice import FakeDevice
from tests.support import record_run_ago

SCRAPER = str(ROOT / "app" / "scraper.py")


def _run(monkeypatch: pytest.MonkeyPatch, *args: str) -> None:
    monkeypatch.setattr(sys, "argv", ["scraper.py", *args])
    _ = runpy.run_path(SCRAPER, run_name="__main__")


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeDevice:
    """A scratch database and debug directory, and a fake device for whatever connects."""
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "posts.sqlite"))
    monkeypatch.setattr(config, "DEBUG_DIR", tmp_path / "debug")
    fake = FakeDevice({"home": ""}, "home")

    def connect() -> FakeDevice:
        return fake

    monkeypatch.setattr(device, "connect_device", connect)
    return fake


def test_profiles_lists_what_each_covers_and_the_default_install(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _run(monkeypatch, "profiles")
    out = capsys.readouterr().out
    assert "v424" in out and "covers Instagram 424 and newer" in out
    assert "validated: 424.0.0.49.64, 440.1.0.46.86" in out and "default install: 445.0.0.45.83" in out


def test_once_prints_the_run_and_exits_non_zero_when_it_failed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def good(con: sqlite3.Connection) -> tuple[scrape.RunStats, None]:
        return {"new": 2, "metrics": {"new_stories": 1}}, None

    monkeypatch.setattr(scrape, "run_recorded", good)
    _run(monkeypatch, "once")
    assert "2 new posts, 1 new stories" in capsys.readouterr().out

    def bad(con: sqlite3.Connection) -> tuple[dict[str, int], Exception]:
        return {}, RuntimeError("adb offline")

    monkeypatch.setattr(scrape, "run_recorded", bad)
    with pytest.raises(SystemExit, match="run failed: RuntimeError"):
        _run(monkeypatch, "once")


def test_once_refuses_past_the_daily_run_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "MAX_RUNS_PER_DAY", 1)
    with db.db_init() as con:
        record_run_ago(con, 30)
    ran: list[int] = []

    def run(con: sqlite3.Connection) -> tuple[scrape.RunStats, None]:
        ran.append(1)
        return {"new": 0, "metrics": {}}, None

    monkeypatch.setattr(scrape, "run_recorded", run)
    with pytest.raises(SystemExit, match="1 runs already started in the last 24h"):
        _run(monkeypatch, "once")
    assert ran == []
    monkeypatch.setattr(config, "MAX_RUNS_PER_DAY", 0)
    _run(monkeypatch, "once")
    assert ran == [1]


def test_login_stops_instagram_afterwards_even_when_it_raises(
    monkeypatch: pytest.MonkeyPatch, isolated: FakeDevice, capsys: pytest.CaptureFixture[str]
) -> None:
    stopped: list[str] = []

    def force_stop(d: FakeDevice, package: str) -> None:
        stopped.append(package)

    def logged_in(d: FakeDevice) -> None:
        pass

    def challenge(d: FakeDevice) -> None:
        raise RuntimeError("Instagram wants a human")

    monkeypatch.setattr(device, "force_stop", force_stop)
    monkeypatch.setattr(navigation, "ensure_logged_in", logged_in)
    _run(monkeypatch, "login")
    assert "logged in" in capsys.readouterr().out
    monkeypatch.setattr(navigation, "ensure_logged_in", challenge)
    with pytest.raises(RuntimeError):
        _run(monkeypatch, "login")
    assert stopped == [config.IG_PKG, config.IG_PKG]


def test_install_passes_the_version_and_rejects_extra_arguments(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    asked: list[str | None] = []

    def install_version(d: FakeDevice, version: str | None = None) -> str:
        asked.append(version)
        return version or "446.0.0.49.77"

    monkeypatch.setattr(install, "install_instagram_version", install_version)
    _run(monkeypatch, "install")
    _run(monkeypatch, "install", "444.0.0.46.85")
    assert asked == [None, "444.0.0.46.85"]
    assert "installed: 444.0.0.46.85" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        _run(monkeypatch, "install", "1", "2")


def test_dump_saves_the_current_screen(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    dumps: list[str] = []

    def dump_debug(d: FakeDevice, name: str, xml: str | None = None) -> None:
        dumps.append(name)

    monkeypatch.setattr(diagnostics, "dump_debug", dump_debug)
    _run(monkeypatch, "dump")
    assert dumps == ["manual"] and "wrote" in capsys.readouterr().out


def test_compat_lists_run_pairs(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _run(monkeypatch, "compat")
    assert "(no runs that reached the device yet)" in capsys.readouterr().out
    con = db.db_init()
    snapshot: device.DeviceSnapshot = {
        "ig_version": "446.0.0.49.77",
        "redroid_image": "erstt/redroid:13",
        "selector_profile": "v424",
    }
    db.record_run(con, "2026-09-14T10:00:00+00:00", "2026-09-14T10:05:00+00:00", 3, None, snapshot)
    con.close()
    _run(monkeypatch, "compat")
    assert "erstt/redroid:13 | 446.0.0.49.77 | v424 | 1 (1, 1) | 3 | 2026-09-14" in capsys.readouterr().out


def test_lock_unlock_and_scrape_now_write_the_control_files(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    control_dir = config.CONTROL_DIR
    _run(monkeypatch, "lock")
    assert (control_dir / "manual.lock").exists() and "locked: True" in capsys.readouterr().out
    _run(monkeypatch, "unlock")
    assert not (control_dir / "manual.lock").exists() and "unlocked: False" in capsys.readouterr().out
    _run(monkeypatch, "scrape-now")
    assert (control_dir / "scrape-now").exists() and "requested" in capsys.readouterr().out


def test_backup_writes_a_copy_now(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _run(monkeypatch, "backup")
    assert list(Path(config.BACKUP_DIR).glob("posts-*.sqlite"))
    assert "wrote" in capsys.readouterr().out


def test_rename_moves_history_and_checks_its_arguments(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    con = db.db_init()
    con.execute(
        "INSERT INTO posts (id, username, scraped_at) VALUES ('p1', 'old_name', '2026-09-14T00:00:00+00:00')"
    )
    con.commit()
    con.close()
    _run(monkeypatch, "rename", "old_name", "new_name")
    assert "moved 1 post(s) from 'old_name' to 'new_name'" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        _run(monkeypatch, "rename", "only_one")


def test_no_subcommand_runs_the_poll_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[bool] = []

    def main() -> None:
        started.append(True)

    monkeypatch.setattr(scrape, "main", main)
    _run(monkeypatch)
    assert started == [True]
