"""Selector-profile resolution (igprofiles.resolve) and activation (scraper.activate_profile)."""

from pathlib import Path

import pytest
import scraper
from igprofiles import PROFILES, V445, major_of, resolve


class V450(V445):
    major = 450
    selectors = {**V445.selectors, "share_id": "row_feed_button_share_v450"}


FAKE = (V445, V450)


@pytest.mark.parametrize(
    ("version", "major"),
    [("446.0.0.49.77", 446), ("445.0.0.45.83", 445), (None, None), ("", None), ("garbage", None)],
)
def test_major_of(version, major):
    assert major_of(version) == major


def test_exact_major_match_has_no_warning():
    assert resolve("450.0.0.1.2", profiles=FAKE) == (V450, None)
    assert resolve("445.0.0.45.83", profiles=FAKE) == (V445, None)


def test_a_version_between_profiles_uses_the_highest_older_one_and_warns():
    profile, warning = resolve("446.0.0.49.77", profiles=FAKE)
    assert profile is V445
    assert warning == "no selector profile for Instagram 446.0.0.49.77; using 445"


def test_a_version_newer_than_every_profile_uses_the_newest_and_warns():
    profile, warning = resolve("460.0.0.1.1", profiles=FAKE)
    assert profile is V450 and "460.0.0.1.1" in warning


def test_a_version_older_than_every_profile_uses_the_oldest_and_warns():
    profile, warning = resolve("300.0.0.0.0", profiles=FAKE)
    assert profile is V445 and warning


@pytest.mark.parametrize("version", [None, "garbage"])
def test_an_unknown_version_uses_the_newest_profile_and_warns(version):
    profile, warning = resolve(version, profiles=FAKE)
    assert profile is V450 and warning.startswith("Instagram version unknown")


def test_override_forces_a_profile_and_warns_when_it_differs_from_the_installed_version():
    assert resolve("450.0.0.1.2", override="445", profiles=FAKE) == (
        V445,
        "IG_SELECTOR_PROFILE forces selector profile 445 (Instagram 450.0.0.1.2 installed)",
    )
    assert resolve("445.0.0.45.83", override="445", profiles=FAKE) == (V445, None)


def test_an_override_matching_no_profile_falls_back_to_the_newest_and_warns():
    profile, warning = resolve("445.0.0.45.83", override="999", profiles=FAKE)
    assert profile is V450 and "matches no profile (445, 450)" in warning


def test_a_subclass_profile_inherits_every_key_it_does_not_override():
    changed = {k for k in V450.selectors if V450.selectors[k] is not V445.selectors[k]}
    assert changed == {"share_id"}
    assert V445.selectors["share_id"] == "row_feed_button_share"  # the base is untouched


def test_registry_is_ascending_by_major():
    majors = [p.major for p in PROFILES]
    assert majors == sorted(majors) and len(set(majors)) == len(majors)


def test_activate_profile_rebinds_the_selectors_call_sites_read(monkeypatch):
    monkeypatch.setattr(scraper, "PROFILES", FAKE)
    monkeypatch.setattr(scraper, "resolve_profile", lambda v, o: resolve(v, o, profiles=FAKE))
    xml = (Path(__file__).parent / "fixture_feed.xml").read_text()
    assert scraper.parse_hierarchy(xml)  # 445 selectors find the fixture's cards

    # A profile whose header id doesn't exist in this (445-era) dump finds no headed card at all.
    class V451(V450):
        major = 451
        selectors = {**V450.selectors, "header_id": "no_such_header"}

    monkeypatch.setattr(scraper, "resolve_profile", lambda v, o: (V451, None))
    scraper.activate_profile("451.0.0.0.0")
    assert scraper.PROFILE is V451 and scraper.SELECTORS is V451.selectors
    assert not any(p["header_bounds"] for p in scraper.parse_hierarchy(xml))


def test_activate_profile_honours_the_override(monkeypatch):
    monkeypatch.setattr(scraper, "resolve_profile", lambda v, o: resolve(v, o, profiles=FAKE))
    monkeypatch.setattr(scraper, "IG_SELECTOR_PROFILE", "445")
    scraper.activate_profile("450.0.0.1.2")
    assert scraper.PROFILE is V445 and "forces selector profile 445" in scraper.PROFILE_WARNING
