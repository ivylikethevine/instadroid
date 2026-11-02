"""The developer tools' command-line entry points (app/devtools/): export_openapi, check_new_builds, promote_dump and
new_profile's subcommands, with anything that would touch docker or a device replaced."""

import runpy
import sys
import types
from collections.abc import Sequence
from pathlib import Path

import igprofiles
import pytest
from devtools import check_new_builds, export_openapi, new_profile, promote_dump, resource_ids

from tests.profilecli import decline, host_state
from tests.support import FEED_445, fake_run


@pytest.mark.parametrize(
    "module",
    [new_profile, promote_dump, check_new_builds, export_openapi, resource_ids],
    ids=["new_profile", "promote_dump", "check_new_builds", "export_openapi", "resource_ids"],
)
def test_each_tool_runs_as_a_script(
    module: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Run as `python -m devtools.<tool> ...`, the `__main__` block hands the command line to main() and
    exits with its return code (--help exits 0 from argparse)."""
    monkeypatch.setattr(sys, "argv", [module.__name__, "--help"])
    exit_info: pytest.ExceptionInfo[SystemExit]
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(str(module.__file__), run_name="__main__")
    assert exit_info.value.code == 0
    assert capsys.readouterr().out.startswith("usage: ")


# --- export_openapi -----------------------------------------------------------------------------


def test_export_openapi_check_passes_on_the_committed_spec() -> None:
    assert export_openapi.main(["--check"]) == 0


def test_export_openapi_writes_and_detects_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    spec: Path = tmp_path / "docs" / "openapi.json"
    spec.parent.mkdir()
    monkeypatch.setattr(export_openapi, "ROOT", tmp_path)
    monkeypatch.setattr(export_openapi, "SPEC", spec)
    assert export_openapi.main(["--check"]) == 1  # missing counts as out of date
    assert export_openapi.main([]) == 0 and spec.read_text().startswith("{")
    assert export_openapi.main(["--check"]) == 0
    spec.write_text("{}\n")
    assert export_openapi.main(["--check"]) == 1
    assert "is out of date" in capsys.readouterr().out


# --- check_new_builds ---------------------------------------------------------------------------


def test_check_new_builds_prints_an_issue_only_for_newer_majors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    listing: Path = tmp_path / "versions.txt"
    argv: list[str] = ["--versions-file", str(listing)]
    listing.write_text("440.1.0.46.86, 999.0.0.1.2\n")
    assert check_new_builds.main(argv) == 0
    assert "| 999 | `999.0.0.1.2` |" in capsys.readouterr().out
    listing.write_text("440.1.0.46.86\n")
    assert check_new_builds.main(argv) == 0 and capsys.readouterr().out == ""


def test_check_new_builds_fails_on_a_listing_without_builds(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    listing: Path = tmp_path / "versions.txt"
    listing.write_text("error: apkpure unreachable\n")
    assert check_new_builds.main(["--versions-file", str(listing)]) == 1
    assert "did apkeep fail?" in capsys.readouterr().err


# --- promote_dump -------------------------------------------------------------------------------


def test_promote_dump_main_promotes_and_rerecords(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    written: list[tuple[Path, str, str]] = []

    def promote(dump: Path, profile: str, name: str) -> promote_dump.Promoted:
        written.append((dump, profile, name))
        return promote_dump.Promoted(
            tmp_path / f"{name}.xml", {"posts": [], "story_tray": [], "following_list": []}, ["left over"]
        )

    def rerecord(profile: str) -> list[Path]:
        return [promote_dump.ROOT / "app" / "igprofiles" / profile / "fixtures" / "feed_445.expected.json"]

    monkeypatch.setattr(promote_dump, "promote", promote)
    monkeypatch.setattr(promote_dump, "rerecord", rerecord)
    monkeypatch.setattr(promote_dump, "ROOT", tmp_path)
    promote_dump.main([str(FEED_445), "v424", "feed_445"])
    assert written == [(FEED_445, "v424", "feed_445")]
    out: str = capsys.readouterr().out
    assert "0 post(s)" in out and "left over" in out
    promote_dump.main(["--update", "v424"])
    assert "wrote app/igprofiles/v424/fixtures/feed_445.expected.json" in capsys.readouterr().out


def test_promote_dump_main_exits_with_the_scrubbing_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def promote(dump: Path, profile: str, name: str) -> promote_dump.Promoted:
        raise ValueError("pseudonymizing changed what the parsers find")

    monkeypatch.setattr(promote_dump, "promote", promote)
    with pytest.raises(SystemExit, match="pseudonymizing changed"):
        promote_dump.main([str(FEED_445), "v424", "feed_445"])


# --- new_profile --------------------------------------------------------------------------------


def test_read_host_state_asks_docker_and_proc(monkeypatch: pytest.MonkeyPatch) -> None:
    answers: dict[str, str] = {"ig-redroid": "true", "ig-app": "false"}

    def output(cmd: Sequence[str]) -> str:
        if cmd[1] == "inspect":
            return answers[cmd[-1]]
        return "1.2GiB / 3GiB"

    monkeypatch.setattr(new_profile, "_output", output)
    state: new_profile.HostState = new_profile.read_host_state()
    assert (state.redroid_running, state.app_running, state.redroid_mem) == (True, False, "1.2GiB / 3GiB")
    assert state.host_available_mib > 0


def test_output_is_empty_when_the_command_cannot_run() -> None:
    assert new_profile._output(["/nonexistent/instadroid-test-binary"]) == ""
    assert new_profile._output(["sh", "-c", "echo hello"]) == "hello"


def test_run_logged_streams_and_appends_to_the_log(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log: Path = tmp_path / "baseline.log"
    assert new_profile._run_logged(["sh", "-c", "echo one; echo two"], log) == 0
    assert "one\ntwo" in capsys.readouterr().out
    assert log.read_text().endswith("one\ntwo\n") and log.read_text().startswith("$ sh -c")


def test_restore_refuses_without_a_device_and_installs_the_default_build(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_redroid() -> new_profile.HostState:
        return host_state(redroid_running=False, redroid_mem="")

    monkeypatch.setattr(new_profile, "read_host_state", no_redroid)
    assert new_profile.restore(yes=True) == 1
    assert "redroid isn't running" in capsys.readouterr().out

    ran: list[list[str]] = fake_run(monkeypatch)
    monkeypatch.setattr(new_profile, "read_host_state", host_state)
    monkeypatch.setattr(new_profile, "_confirm", decline)
    assert new_profile.restore() == 1 and not ran  # declined
    assert new_profile.restore(yes=True) == 0
    assert ran[0][-2:] == ["install", igprofiles.DEFAULT_BUILD]


@pytest.mark.usefixtures("dev_dirs")
def test_new_profile_main_dispatches_and_reports_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def app_running() -> new_profile.HostState:
        return host_state(app_running=True)

    assert new_profile.main(["check", "445.0.0.45.83"]) == 1  # nothing captured
    assert "No captured screens" in capsys.readouterr().out
    assert new_profile.main(["promote", "445.0.0.45.83"]) == 1
    assert new_profile.main(["validate", "446.0.0.49.77"]) == 0  # already validated
    assert "already validated" in capsys.readouterr().out
    assert new_profile.main(["fork", "440.1.0.46.86"]) == 2  # v424 exists: an error, nothing written
    assert "error:" in capsys.readouterr().err
    monkeypatch.setattr(new_profile, "read_host_state", app_running)
    assert new_profile.main(["baseline", "445.0.0.45.83", "--yes"]) == 1
    assert (
        new_profile.main(["restore", "--yes"]) == 1
    )  # the app service is running: refused before any install
