import sqlite3
from pathlib import Path
from typing import Unpack

import pytest
from instadroid import config, db, device

IMAGE: str = "erstt/redroid:13.0.0_ndk_ChromeOS"


def _run(
    con: sqlite3.Connection,
    started: str,
    ig: str | None,
    error: str | None = None,
    new_posts: int = 0,
    **stats: Unpack[db.RunMetrics],
) -> None:
    snapshot: device.DeviceSnapshot = (
        {"ig_version": ig, "redroid_image": IMAGE, "selector_profile": "v446"} if ig else {}
    )
    db.record_run(con, started, started, new_posts, error, snapshot, **stats)


def test_version_pairs_summarizes_runs_per_image_and_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "posts.sqlite"))
    con: sqlite3.Connection = db.db_init()
    _run(con, "2026-09-14T10:00:00+00:00", "445.0.0.45.83", new_posts=2)
    _run(con, "2026-09-14T11:00:00+00:00", "446.0.0.49.77", new_posts=1, warning="memory guard")
    _run(con, "2026-09-14T12:00:00+00:00", "446.0.0.49.77", new_posts=6)
    _run(con, "2026-09-14T13:00:00+00:00", "446.0.0.49.77", error="DeviceNotReady")
    _run(con, "2026-09-14T14:00:00+00:00", None, error="adb offline")  # never reached the device

    pairs: list[db.VersionPair] = db.version_pairs(con)
    assert pairs == [
        {
            "redroid_image": IMAGE,
            "ig_version": "445.0.0.45.83",
            "selector_profile": "v446",
            "runs": 1,
            "clean_runs": 1,
            "ok_runs": 1,
            "new_posts": 2,
            "last_run": "2026-09-14T10:00:00+00:00",
        },
        {
            "redroid_image": IMAGE,
            "ig_version": "446.0.0.49.77",
            "selector_profile": "v446",
            "runs": 3,
            "clean_runs": 1,
            "ok_runs": 2,
            "new_posts": 7,
            "last_run": "2026-09-14T13:00:00+00:00",
        },
    ]
