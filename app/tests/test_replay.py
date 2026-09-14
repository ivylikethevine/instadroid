"""Replay tests: every recorded screen under igprofiles/<profile>/fixtures/ must still parse to what
was recorded in its .expected.json, under that profile. Add one with scripts/promote_dump.py; after
an intentional parser or selector change, re-record with `promote_dump.py --update <profile>`."""

import json
from pathlib import Path

import igprofiles
import pytest
from instadroid import parsing, versioning

CASES = sorted(
    (path.parent.parent.name, path)
    for path in Path(igprofiles.__file__).parent.glob("v*/fixtures/*.expected.json")
)


def test_there_is_at_least_one_replay_fixture():
    assert CASES


@pytest.mark.parametrize(("profile", "expected"), CASES, ids=[f"{p}/{e.name}" for p, e in CASES])
def test_fixture_parses_as_recorded(monkeypatch, profile, expected):
    monkeypatch.setattr(versioning, "PROFILE", igprofiles.load(profile))
    xml = expected.with_name(expected.name.removesuffix(".expected.json") + ".xml").read_text()
    assert json.loads(json.dumps(parsing.parse_screen(xml))) == json.loads(expected.read_text())
