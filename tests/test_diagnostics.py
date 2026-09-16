import os
import sqlite3
import time
from pathlib import Path

import pytest
from instadroid import config, db, device, diagnostics
from shared import sqlrows

from tests.fakedevice import FakeDevice, Out
from tests.support import fetch_row


class VersionedDevice(FakeDevice):
    def __init__(self) -> None:
        super().__init__({}, "launcher")

    def shell(self, cmdargs: str | list[str], timeout: float = 60) -> Out:
        if isinstance(cmdargs, list):
            if cmdargs[:2] == ["dumpsys", "package"]:
                return Out(
                    "Packages:\n  Package [com.instagram.android]\n    versionCode=385111379\n"
                    "    versionName=445.0.0.45.83\n"
                )
            return Out("")
        props: dict[str, str] = {
            "getprop ro.build.version.release": "13",
            "getprop ro.build.version.sdk": "33",
        }
        return Out(props.get(cmdargs, ""))


def test_device_snapshot_records_instagram_version_and_image(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDROID_IMAGE", "erstt/redroid:13.0.0_ndk_ChromeOS")
    snapshot: device.DeviceSnapshot = device.device_snapshot(VersionedDevice())
    assert snapshot.get("android_release") == "13"
    assert snapshot.get("ig_version") == "445.0.0.45.83"
    assert snapshot.get("redroid_image") == "erstt/redroid:13.0.0_ndk_ChromeOS"


def test_record_run_stores_versions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "posts.sqlite"))
    con: sqlite3.Connection = db.db_init()
    db.record_run(
        con, "2026-09-11T00:00:00+00:00", "2026-09-11T00:05:00+00:00", 0, None,
        {"ig_version": "445.0.0.45.83", "redroid_image": "img:tag"},
    )  # fmt: skip
    row: sqlite3.Row = fetch_row(con.execute("SELECT ig_version, redroid_image FROM runs"))
    assert sqlrows.values(row) == ("445.0.0.45.83", "img:tag")


@pytest.fixture
def debug_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d: Path = tmp_path / "debug"
    d.mkdir()
    monkeypatch.setattr(config, "DEBUG_DIR", d)
    monkeypatch.setattr(config, "DEBUG_RETAIN_DAYS", 7)
    return d


def _touch(path: Path, days_old: float = 0.0) -> Path:
    path.write_bytes(b"x")
    t: float = time.time() - days_old * 86400
    os.utime(path, (t, t))
    return path


def test_prune_debug_removes_old_loose_artifacts_but_not_other_files(debug_dir: Path) -> None:
    old_png: Path = _touch(debug_dir / "following_link.png", days_old=10)
    old_xml: Path = _touch(debug_dir / "feed_menu.xml", days_old=10)
    fresh_png: Path = _touch(debug_dir / "boot.png", days_old=1)
    scratch_db: Path = _touch(debug_dir / "test3.sqlite", days_old=30)  # a DB_PATH may point here
    scratch_dir: Path = debug_dir / "testmedia3"
    scratch_dir.mkdir()

    diagnostics.prune_debug_dumps()

    assert not old_png.exists() and not old_xml.exists()
    assert fresh_png.exists()
    assert scratch_db.exists() and scratch_dir.exists()


def test_prune_debug_still_caps_dump_pairs_by_count(debug_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "DEBUG_KEEP", 2)
    for i in range(4):
        _touch(debug_dir / f"d{i}_hierarchy.xml", days_old=0.1 * (4 - i))
        _touch(debug_dir / f"d{i}_screen.jpg", days_old=0.1 * (4 - i))

    diagnostics.prune_debug_dumps()

    assert sorted(p.name for p in debug_dir.iterdir()) == [
        "d2_hierarchy.xml",
        "d2_screen.jpg",
        "d3_hierarchy.xml",
        "d3_screen.jpg",
    ]


def test_prune_debug_age_rule_can_be_disabled(debug_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "DEBUG_RETAIN_DAYS", 0)
    old: Path = _touch(debug_dir / "header.png", days_old=100)
    diagnostics.prune_debug_dumps()
    assert old.exists()


def test_prune_debug_tolerates_a_missing_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "DEBUG_DIR", tmp_path / "nope")
    diagnostics.prune_debug_dumps()  # must not raise


def test_prune_debug_logs_a_file_it_cannot_delete_and_carries_on(
    debug_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    stuck: Path = _touch(debug_dir / "old.png", days_old=10)

    def refuse(self: Path, missing_ok: bool = False) -> None:
        raise PermissionError(f"{self.name}: owned by another uid")

    monkeypatch.setattr(Path, "unlink", refuse)
    diagnostics.prune_debug_dumps()  # must not raise
    assert stuck.exists()
    assert "WARN: could not prune debug file 'old.png': PermissionError(" in capsys.readouterr().out
