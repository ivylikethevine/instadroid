"""Replay tests: every recorded screen under igprofiles/<profile>/fixtures/ must still parse to what
was recorded in its .expected.json, under that profile. Add one with scripts/promote_dump.py; after
an intentional parser or selector change, re-record with `promote_dump.py --update <profile>`."""

import json
from pathlib import Path

import igprofiles
import pytest
from igprofiles import screens
from instadroid import parsing, versioning

CASES = sorted(
    (path.parent.parent.name, path)
    for path in Path(igprofiles.__file__).parent.glob("v*/fixtures/*.expected.json")
)


def test_there_is_at_least_one_replay_fixture() -> None:
    assert CASES


@pytest.mark.parametrize(("profile", "expected"), CASES, ids=[f"{p}/{e.name}" for p, e in CASES])
def test_fixture_parses_as_recorded(monkeypatch: pytest.MonkeyPatch, profile: str, expected: Path) -> None:
    monkeypatch.setattr(versioning, "PROFILE", igprofiles.load(profile))
    xml = expected.with_name(expected.name.removesuffix(".expected.json") + ".xml").read_text()
    assert json.loads(json.dumps(parsing.parse_screen(xml))) == json.loads(expected.read_text())


@pytest.mark.parametrize(("profile", "expected"), CASES, ids=[f"{p}/{e.name}" for p, e in CASES])
def test_fixture_named_after_a_screen_has_that_screens_required_selectors(
    profile: str, expected: Path
) -> None:
    """A fixture named after a screen (feed.xml, home_feed.xml, ...) also pins the selector keys the
    scraper needs on it, beyond what the parsers read: see igprofiles/screens.py."""
    screen = expected.name.removesuffix(".expected.json")
    if screen not in screens.SCREENS:
        pytest.skip(f"{screen} is not a screen name")
    xml = expected.with_name(f"{screen}.xml").read_text()
    result = screens.check_screen(xml, screen, igprofiles.load(profile).selectors)
    assert not result.missing_required
