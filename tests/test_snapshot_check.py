"""The /data snapshot check before Instagram is replaced (install.snapshot_problem()): `scraper.py install`
refuses without a recent snapshot, unless --force."""

import os
import runpy
import sys
import time
from pathlib import Path

import pytest
from devtools import ROOT
from instadroid import config, install

from tests.fakedevice import FakeDevice

DAY: float = 86400


@pytest.fixture
def snapshots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder: Path = tmp_path / "snapshots"
    folder.mkdir()
    monkeypatch.setattr(config, "SNAPSHOT_DIR", folder)
    monkeypatch.setattr(config, "SNAPSHOT_MAX_DAYS", 7)
    return folder


def _snapshot(folder: Path, days_old: float) -> None:
    path: Path = folder / f"android-{days_old:g}.tar.gz"
    path.write_bytes(b"")
    at: float = time.time() - days_old * DAY
    os.utime(path, (at, at))


def test_no_snapshot_is_a_problem(snapshots: Path) -> None:
    (snapshots / "unrelated.tar.gz").write_bytes(b"")
    (snapshots / "android-broken.tar.gz").symlink_to(snapshots / "missing")  # unreadable, skipped
    assert install.snapshot_problem() == f"no /data snapshot in {snapshots}"


def test_the_newest_snapshot_decides(snapshots: Path) -> None:
    _snapshot(snapshots, 30)
    assert install.snapshot_problem() == "the newest /data snapshot is 30 days old (SNAPSHOT_MAX_DAYS=7)"
    _snapshot(snapshots, 2)
    assert install.snapshot_problem() is None


def test_zero_turns_the_check_off(snapshots: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "SNAPSHOT_MAX_DAYS", 0)
    assert install.snapshot_problem() is None


def test_install_refuses_without_a_recent_snapshot_unless_forced(
    snapshots: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "posts.sqlite"))
    asked: list[str | None] = []

    def install_version(d: FakeDevice, version: str | None = None) -> str:
        asked.append(version)
        return version or "450.0.0.50.77"

    def connect() -> FakeDevice:
        return FakeDevice({"home": ""}, "home")

    def run(*args: str) -> None:
        monkeypatch.setattr(sys, "argv", ["scraper.py", *args])
        runpy.run_path(str(ROOT / "app" / "scraper.py"), run_name="__main__")

    monkeypatch.setattr(install, "install_instagram_version", install_version)
    monkeypatch.setattr("instadroid.device.connect_device", connect)
    with pytest.raises(SystemExit, match="no /data snapshot in .*or pass --force"):
        run("install", "444.0.0.46.85")
    assert asked == []
    run("install", "--force", "444.0.0.46.85")
    _snapshot(snapshots, 1)
    run("install")
    assert asked == ["444.0.0.46.85", None]
    assert "installed: 450.0.0.50.77" in capsys.readouterr().out
