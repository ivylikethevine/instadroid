import logging
import os
import time

import pytest
import scraper


class Out:
    def __init__(self, output):
        self.output = output


class VersionedDevice:
    def shell(self, cmd):
        if isinstance(cmd, list) and cmd[:2] == ["dumpsys", "package"]:
            return Out(
                "Packages:\n  Package [com.instagram.android]\n    versionCode=385111379\n"
                "    versionName=445.0.0.45.83\n"
            )
        return Out(
            {"getprop ro.build.version.release": "13", "getprop ro.build.version.sdk": "33"}.get(cmd, "")
        )


def test_device_snapshot_records_instagram_version_and_image(monkeypatch):
    monkeypatch.setenv("REDROID_IMAGE", "erstt/redroid:13.0.0_ndk_ChromeOS")
    snapshot = scraper._device_snapshot(VersionedDevice())
    assert snapshot["android_release"] == "13"
    assert snapshot["ig_version"] == "445.0.0.45.83"
    assert snapshot["redroid_image"] == "erstt/redroid:13.0.0_ndk_ChromeOS"


def test_record_run_stores_versions(tmp_path, monkeypatch):
    monkeypatch.setattr(scraper, "DB_PATH", str(tmp_path / "posts.sqlite"))
    con = scraper.db_init()
    scraper.record_run(
        con, "2026-09-11T00:00:00+00:00", "2026-09-11T00:05:00+00:00", 0, None,
        {"ig_version": "445.0.0.45.83", "redroid_image": "img:tag"},
    )  # fmt: skip
    row = con.execute("SELECT ig_version, redroid_image FROM runs").fetchone()
    assert tuple(row) == ("445.0.0.45.83", "img:tag")


@pytest.fixture
def debug_dir(tmp_path, monkeypatch):
    d = tmp_path / "debug"
    d.mkdir()
    monkeypatch.setattr(scraper, "DEBUG_DIR", d)
    monkeypatch.setattr(scraper, "DEBUG_RETAIN_DAYS", 7)
    return d


def _touch(path, days_old=0.0):
    path.write_bytes(b"x")
    t = time.time() - days_old * 86400
    os.utime(path, (t, t))
    return path


def test_prune_debug_removes_old_loose_artifacts_but_not_other_files(debug_dir):
    old_png = _touch(debug_dir / "following_link.png", days_old=10)
    old_xml = _touch(debug_dir / "feed_menu.xml", days_old=10)
    fresh_png = _touch(debug_dir / "boot.png", days_old=1)
    scratch_db = _touch(debug_dir / "test3.sqlite", days_old=30)  # a DB_PATH may point here
    scratch_dir = debug_dir / "testmedia3"
    scratch_dir.mkdir()

    scraper._prune_debug_dumps()

    assert not old_png.exists() and not old_xml.exists()
    assert fresh_png.exists()
    assert scratch_db.exists() and scratch_dir.exists()


def test_prune_debug_still_caps_dump_pairs_by_count(debug_dir, monkeypatch):
    monkeypatch.setattr(scraper, "DEBUG_KEEP", 2)
    for i in range(4):
        _touch(debug_dir / f"d{i}_hierarchy.xml", days_old=0.1 * (4 - i))
        _touch(debug_dir / f"d{i}_screen.jpg", days_old=0.1 * (4 - i))

    scraper._prune_debug_dumps()

    assert sorted(p.name for p in debug_dir.iterdir()) == [
        "d2_hierarchy.xml",
        "d2_screen.jpg",
        "d3_hierarchy.xml",
        "d3_screen.jpg",
    ]


def test_prune_debug_age_rule_can_be_disabled(debug_dir, monkeypatch):
    monkeypatch.setattr(scraper, "DEBUG_RETAIN_DAYS", 0)
    old = _touch(debug_dir / "header.png", days_old=100)
    scraper._prune_debug_dumps()
    assert old.exists()


def test_prune_debug_tolerates_a_missing_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(scraper, "DEBUG_DIR", tmp_path / "nope")
    scraper._prune_debug_dumps()  # must not raise


def test_healthcheck_requests_are_left_out_of_the_access_log(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "posts.sqlite"))
    monkeypatch.setenv("MEDIA_DIR", str(tmp_path / "media"))
    import app

    f = app._SkipHealthcheck()

    def record(path):
        args = ("127.0.0.1:5000", "GET", path, "1.1", 200)
        return logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d', args, None)

    assert not f.filter(record("/health"))
    assert f.filter(record("/instagram.xml"))
    assert f.filter(record("/instagram.xml?user=healthy_eats"))
