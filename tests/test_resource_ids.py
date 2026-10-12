"""The resource-id report for a new Instagram build (devtools/resource_ids.py), from `aapt2 dump resources` text."""

from pathlib import Path

import igprofiles
import pytest
from devtools import check_new_builds, resource_ids

_WANTED: dict[str, tuple[str, ...]] = resource_ids.selector_ids(igprofiles.load("v424").selectors)
_ALL: list[str] = sorted({name for names in _WANTED.values() for name in names})


def _dump(ids: list[str]) -> str:
    """`aapt2 dump resources` output declaring `ids`, among resources of other types."""
    lines: list[str] = [
        "Binary APK",
        "Package name=com.instagram.android id=7f",
        "  type drawable id=08 entryCount=1",
        "    resource 0x7f080000 drawable/row_feed_button_share",
        "      () (file) res/a.xml type=XML",
        f"  type id id=0b entryCount={len(ids)}",
    ]
    index: int
    name: str
    for index, name in enumerate(ids):
        lines += [f"    resource 0x7f0b{index:04x} id/{name}", "      () (id)"]
    return "\n".join(lines) + "\n"


def test_only_id_resources_are_read_from_a_dump() -> None:
    assert resource_ids.declared_ids(_dump(["feed_tab", "profile_tab"])) == {"feed_tab", "profile_tab"}
    assert resource_ids.declared_ids("error: not an apk\n") == set()


def test_selector_ids_are_instagrams_own_resource_ids() -> None:
    assert _WANTED["share_id"] == ("row_feed_button_share",)
    assert _WANTED["story_tray_ids"] == ("reels_tray_container", "overlay_stories_tray_container")
    assert "feed_list_id" not in _WANTED  # android:id/list is the platform's
    assert "copy_link_desc" not in _WANTED and "header_desc" not in _WANTED


def test_a_build_with_every_selector_id_reports_none_missing() -> None:
    body: str = resource_ids.report("451.0.0.1.1", set(_ALL))
    assert f"All {len(_ALL)} resource ids `v424`'s selectors use are in the base APK." in body
    assert "Against" not in body


def test_a_missing_selector_id_is_listed_under_its_key() -> None:
    ids: set[str] = set(_ALL) - {"row_feed_button_share", "overlay_stories_tray_container"}
    body: str = resource_ids.report("451.0.0.1.1", ids)
    assert f"2 of the {len(_ALL)} resource ids" in body
    assert "- `share_id`: `row_feed_button_share`" in body
    assert "- `story_tray_ids`: `overlay_stories_tray_container`" in body


def test_added_and_removed_ids_are_counted_and_named_up_to_a_limit() -> None:
    extra: set[str] = {f"new_{i:03d}" for i in range(resource_ids.LISTED + 5)}
    body: str = resource_ids.report(
        "451.0.0.1.1", set(_ALL) | extra, "450.0.0.50.77", set(_ALL) | {"old_one"}
    )
    assert f"Against `450.0.0.50.77`: {len(extra)} ids added, 1 removed." in body
    assert "Removed: `old_one`" in body
    assert "`new_000`" in body and "and 5 more" in body
    same: str = resource_ids.report("451.0.0.1.1", set(_ALL), "450.0.0.50.77", set(_ALL))
    assert "0 ids added, 0 removed." in same and "Removed:" not in same and "Added:" not in same


def test_a_build_below_every_profile_has_no_selectors_to_check() -> None:
    assert "No profile covers this build" in resource_ids.report("300.0.0.1.1", {"feed_tab"})


def test_the_command_prints_the_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    new: Path = tmp_path / "new.txt"
    old: Path = tmp_path / "old.txt"
    new.write_text(_dump(_ALL))
    old.write_text(_dump([*_ALL, "old_one"]))
    assert resource_ids.main(["--build", "451.0.0.1.1", "--dump", str(new)]) == 0
    assert "### Resource ids in `451.0.0.1.1`" in capsys.readouterr().out
    argv: list[str] = ["--build", "451.0.0.1.1", "--dump", str(new), "--old-build", "450.0.0.50.77"]
    assert resource_ids.main([*argv, "--old-dump", str(old)]) == 0
    assert "0 ids added, 1 removed." in capsys.readouterr().out


def test_the_command_fails_on_a_dump_without_ids(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    good: Path = tmp_path / "good.txt"
    bad: Path = tmp_path / "bad.txt"
    good.write_text(_dump(_ALL))
    bad.write_text("error: not an apk\n")
    assert resource_ids.main(["--build", "451.0.0.1.1", "--dump", str(bad)]) == 1
    assert "no id resources" in capsys.readouterr().err
    argv: list[str] = ["--build", "451.0.0.1.1", "--dump", str(good), "--old-build", "450.0.0.50.77"]
    assert resource_ids.main(argv) == 1
    assert resource_ids.main([*argv, "--old-dump", str(bad)]) == 1
    assert "--old-build needs --old-dump" in capsys.readouterr().err


def test_check_new_builds_can_list_the_builds_themselves(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    listing: Path = tmp_path / "versions.txt"
    listing.write_text("440.1.0.46.86, 998.0.0.1.2, 999.0.0.3.4\n")
    assert check_new_builds.main(["--versions-file", str(listing), "--builds"]) == 0
    assert capsys.readouterr().out == (
        f"validated {igprofiles.newest_build()}\nnew 998.0.0.1.2\nnew 999.0.0.3.4\n"
    )
    assert check_new_builds.build_lines({}, None) == ""
