"""The developer tools' command-line entry points (app/devtools/): export_openapi, check_new_builds, promote_dump and
new_profile's subcommands, with anything that would touch docker or a device replaced."""

import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest
from devtools import check_new_builds, export_openapi, new_profile, promote_dump

FEED_445 = Path(__file__).resolve().parents[1] / "app" / "igprofiles" / "v424" / "fixtures" / "feed_445.xml"


def _argv(monkeypatch: pytest.MonkeyPatch, script: str, *args: str) -> None:
    monkeypatch.setattr(sys, "argv", [script, *args])


# --- export_openapi -----------------------------------------------------------------------------


def test_export_openapi_check_passes_on_the_committed_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FEED_TOKEN", raising=False)
    _argv(monkeypatch, "export_openapi.py", "--check")
    assert export_openapi.main() == 0


def test_export_openapi_writes_and_detects_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("FEED_TOKEN", raising=False)
    spec = tmp_path / "docs" / "openapi.json"
    spec.parent.mkdir()
    monkeypatch.setattr(export_openapi, "ROOT", tmp_path)
    monkeypatch.setattr(export_openapi, "SPEC", spec)
    _argv(monkeypatch, "export_openapi.py", "--check")
    assert export_openapi.main() == 1  # missing counts as out of date
    _argv(monkeypatch, "export_openapi.py")
    assert export_openapi.main() == 0 and spec.read_text().startswith("{")
    _argv(monkeypatch, "export_openapi.py", "--check")
    assert export_openapi.main() == 0
    spec.write_text("{}\n")
    assert export_openapi.main() == 1
    assert "is out of date" in capsys.readouterr().out


# --- check_new_builds ---------------------------------------------------------------------------


def test_check_new_builds_prints_an_issue_only_for_newer_majors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    listing = tmp_path / "versions.txt"
    listing.write_text("440.1.0.46.86, 999.0.0.1.2\n")
    _argv(monkeypatch, "check_new_builds.py", "--versions-file", str(listing))
    assert check_new_builds.main() == 0
    assert "| 999 | `999.0.0.1.2` |" in capsys.readouterr().out
    listing.write_text("440.1.0.46.86\n")
    assert check_new_builds.main() == 0 and capsys.readouterr().out == ""


def test_check_new_builds_fails_on_a_listing_without_builds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    listing = tmp_path / "versions.txt"
    listing.write_text("error: apkpure unreachable\n")
    _argv(monkeypatch, "check_new_builds.py", "--versions-file", str(listing))
    assert check_new_builds.main() == 1
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
    _argv(monkeypatch, "promote_dump.py", str(FEED_445), "v424", "feed_445")
    promote_dump.main()
    assert written == [(FEED_445, "v424", "feed_445")]
    out = capsys.readouterr().out
    assert "0 post(s)" in out and "left over" in out
    _argv(monkeypatch, "promote_dump.py", "--update", "v424")
    promote_dump.main()
    assert "wrote app/igprofiles/v424/fixtures/feed_445.expected.json" in capsys.readouterr().out


def test_promote_dump_main_exits_with_the_scrubbing_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def promote(dump: Path, profile: str, name: str) -> promote_dump.Promoted:
        raise ValueError("pseudonymizing changed what the parsers find")

    monkeypatch.setattr(promote_dump, "promote", promote)
    _argv(monkeypatch, "promote_dump.py", str(FEED_445), "v424", "feed_445")
    with pytest.raises(SystemExit, match="pseudonymizing changed"):
        promote_dump.main()


# --- new_profile --------------------------------------------------------------------------------


def _host(**changes: bool | str | int) -> new_profile.HostState:
    state = new_profile.HostState(
        redroid_running=True, app_running=False, redroid_mem="1.0GiB / 3GiB", host_available_mib=8000
    )
    for key, value in changes.items():
        setattr(state, key, value)
    return state


def test_read_host_state_asks_docker_and_proc(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = {"ig-redroid": "true", "ig-app": "false"}

    def output(cmd: Sequence[str]) -> str:
        if cmd[1] == "inspect":
            return answers[cmd[-1]]
        return "1.2GiB / 3GiB"

    monkeypatch.setattr(new_profile, "_output", output)
    state = new_profile.read_host_state()
    assert (state.redroid_running, state.app_running, state.redroid_mem) == (True, False, "1.2GiB / 3GiB")
    assert state.host_available_mib > 0


def test_output_is_empty_when_the_command_cannot_run() -> None:
    assert new_profile._output(["/nonexistent/instadroid-test-binary"]) == ""
    assert new_profile._output(["sh", "-c", "echo hello"]) == "hello"


def test_run_logged_streams_and_appends_to_the_log(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = tmp_path / "baseline.log"
    assert new_profile._run_logged(["sh", "-c", "echo one; echo two"], log) == 0
    assert "one\ntwo" in capsys.readouterr().out
    assert log.read_text().endswith("one\ntwo\n") and log.read_text().startswith("$ sh -c")


def test_restore_refuses_without_a_device_and_installs_the_default_build(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_redroid() -> new_profile.HostState:
        return _host(redroid_running=False, redroid_mem="")

    def decline(prompt: str) -> bool:
        return False

    monkeypatch.setattr(new_profile, "read_host_state", no_redroid)
    assert new_profile.restore(yes=True) == 1
    assert "redroid isn't running" in capsys.readouterr().out

    ran: list[list[str]] = []

    def run(cmd: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        ran.append(list(cmd))
        return subprocess.CompletedProcess(list(cmd), 0)

    monkeypatch.setattr(new_profile, "read_host_state", _host)
    monkeypatch.setattr(new_profile.subprocess, "run", run)
    monkeypatch.setattr(new_profile, "_confirm", decline)
    assert new_profile.restore() == 1 and not ran  # declined
    assert new_profile.restore(yes=True) == 0
    assert ran[0][-2:] == ["install", "445.0.0.45.83"]


def test_new_profile_main_dispatches_and_reports_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def dev_dir(build: str) -> Path:
        return tmp_path / build.split(".")[0]

    def app_running() -> new_profile.HostState:
        return _host(app_running=True)

    monkeypatch.setattr(new_profile, "dev_dir", dev_dir)
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
