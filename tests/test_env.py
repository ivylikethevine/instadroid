"""The settings both processes read (shared/env.py)."""

from datetime import UTC
from zoneinfo import ZoneInfo

import pytest
from shared import env


def test_tz_names_the_zone_for_the_feeds_and_the_device() -> None:
    environ: dict[str, str] = {"TZ": " :America/Los_Angeles "}  # libc's ":" spelling of a zone file
    assert env.env_timezone(environ) == "America/Los_Angeles"
    assert env.env_device_timezone(environ) == "America/Los_Angeles"
    assert env.env_timezone({}) == "" and env.env_device_timezone({}) == ""


def test_device_timezone_moves_the_device_alone() -> None:
    environ: dict[str, str] = {"TZ": "America/Los_Angeles", "DEVICE_TIMEZONE": "Europe/Berlin"}
    assert env.env_device_timezone(environ) == "Europe/Berlin"
    assert env.env_timezone(environ) == "America/Los_Angeles"
    assert env.env_timezone({"DEVICE_TIMEZONE": "Europe/Berlin"}) == ""


@pytest.mark.parametrize("value", ["Mars/Olympus_Mons", "../etc/passwd", "UTC+25:99"])
def test_an_unknown_zone_falls_back_to_utc_with_a_warning(
    value: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert env.env_timezone({"TZ": value}) == ""
    assert f"unknown TZ {value!r}" in capsys.readouterr().out
    # an unknown DEVICE_TIMEZONE leaves the device on TZ
    assert env.env_device_timezone({"TZ": "Europe/Berlin", "DEVICE_TIMEZONE": value}) == "Europe/Berlin"


def test_zone_is_utc_for_an_empty_setting() -> None:
    assert env.zone("") is UTC
    assert env.zone("Europe/Berlin") == ZoneInfo("Europe/Berlin")
