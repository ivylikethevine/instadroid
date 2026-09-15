"""Replay tests: every recorded screen under igprofiles/<profile>/fixtures/ must still parse to what
was recorded in its .expected.json, under that profile. Add one with devtools/promote_dump.py; after
an intentional parser or selector change, re-record with `promote-dump --update <profile>`."""

from pathlib import Path

import igprofiles
import pytest
from devtools import promote_dump

CASES = sorted(
    (path.parent.parent.name, path)
    for path in Path(igprofiles.__file__).parent.glob("v*/fixtures/*.expected.json")
)


def test_there_is_at_least_one_replay_fixture() -> None:
    assert CASES


@pytest.mark.parametrize(("profile", "expected"), CASES, ids=[f"{p}/{e.name}" for p, e in CASES])
def test_fixture_parses_as_recorded(profile: str, expected: Path) -> None:
    """A fixture named after a screen (feed_445.xml, home_feed_444.xml, ...) also pins the selector keys the
    scraper needs on it, beyond what the parsers read: see promote_dump.fixture_problems()."""
    assert promote_dump.fixture_problems(igprofiles.load(profile), expected) == []
