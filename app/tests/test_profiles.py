"""Instagram version profiles: discovery and loading (igprofiles), selection and install versions
(scraper.activate_profile / _apk_version), and per-version behavior overrides (@versioned)."""

import types

import igprofiles
import pytest
import scraper
from igprofiles import BaseProfile, major_of

# --- discovery and loading ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("version", "major"),
    [("446.0.0.49.77", 446), ("445.0.0.45.83", 445), (None, None), ("", None), ("garbage", None)],
)
def test_major_of(version, major):
    assert major_of(version) == major


def test_available_profiles_are_the_vxyz_directories_at_or_above_the_floor():
    names = igprofiles.available()
    assert {"v445", "v446"} <= set(names)
    assert names == sorted(names)
    assert all(int(n[1:]) >= igprofiles.MIN_MAJOR for n in names)


def test_default_profile_is_available():
    assert igprofiles.DEFAULT_PROFILE in igprofiles.available()


@pytest.mark.parametrize("name", ["v445", "445", " V445 "])
def test_load_accepts_a_name_with_or_without_the_v(name):
    profile = igprofiles.load(name)
    assert (profile.name, profile.major, profile.apk_version) == ("v445", 445, "445.0.0.45.83")


@pytest.mark.parametrize(
    ("name", "error"),
    [
        ("v439", "below the supported floor (v440)"),
        ("v999", "no profile directory igprofiles/v999/"),
        ("latest", "is not a profile name"),
    ],
)
def test_load_rejects_invalid_names(name, error):
    with pytest.raises(ValueError, match=error.replace("(", r"\(").replace(")", r"\)")):
        igprofiles.load(name)


def _fake_profile_dir(monkeypatch, name, profile_cls):
    monkeypatch.setattr(igprofiles, "available", lambda: ["v445", "v446", name])
    module = types.SimpleNamespace(Profile=profile_cls)
    real_import = igprofiles.importlib.import_module
    monkeypatch.setattr(
        igprofiles.importlib,
        "import_module",
        lambda mod: module if mod == f"igprofiles.{name}" else real_import(mod),
    )


def test_load_rejects_a_profile_whose_major_does_not_match_its_directory(monkeypatch):
    class Profile(BaseProfile):
        major, apk_version, selectors = 446, "446.0.0.49.77", {}

    _fake_profile_dir(monkeypatch, "v447", Profile)
    with pytest.raises(ValueError, match="defines major=446, expected 447"):
        igprofiles.load("v447")


def test_load_rejects_an_apk_version_from_another_major(monkeypatch):
    class Profile(BaseProfile):
        major, apk_version, selectors = 447, "446.0.0.49.77", {}

    _fake_profile_dir(monkeypatch, "v447", Profile)
    with pytest.raises(ValueError, match="is not a 447.x build"):
        igprofiles.load("v447")


def test_load_rejects_a_directory_without_a_profile_class(monkeypatch):
    _fake_profile_dir(monkeypatch, "v447", None)
    with pytest.raises(ValueError, match=r"must define Profile\(BaseProfile\)"):
        igprofiles.load("v447")


def test_select_falls_back_to_the_default_with_a_warning():
    profile, warning = igprofiles.select("")
    assert profile.name == igprofiles.DEFAULT_PROFILE and warning is None
    profile, warning = igprofiles.select("v439")
    assert profile.name == igprofiles.DEFAULT_PROFILE
    assert warning == (
        f"IG_PROFILE='v439': v439 is below the supported floor (v440); using {igprofiles.DEFAULT_PROFILE}"
    )


# --- the contract every profile directory must meet (this is what a 440-444 backfill runs) --------


@pytest.mark.parametrize("name", igprofiles.available())
def test_every_profile_meets_the_contract(name):
    profile = igprofiles.load(name)
    assert profile.major >= igprofiles.MIN_MAJOR
    assert major_of(profile.apk_version) == profile.major
    # Every selector key the scraper reads must exist, so a profile can't silently drop one.
    baseline = igprofiles.load("v445").selectors
    assert set(baseline) <= set(profile.selectors), set(baseline) - set(profile.selectors)
    assert not scraper._unknown_hooks(profile), "an override name matches no @versioned function"


def test_v446_inherits_everything_from_v445_so_far():
    v445, v446 = igprofiles.load("v445"), igprofiles.load("v446")
    assert v446.selectors == v445.selectors
    assert isinstance(v446, type(v445))
    assert v446.apk_version == "446.0.0.49.77"


def test_profile_fixtures_live_in_their_version_directory():
    path = igprofiles.fixture("445", "feed.xml")
    assert path.parts[-3:] == ("v445", "fixtures", "feed.xml") and path.is_file()


# --- selection in the scraper -------------------------------------------------------------------


def test_activate_profile_loads_the_ig_profile_directory(monkeypatch):
    monkeypatch.setattr(scraper, "IG_PROFILE", "v446")
    scraper.activate_profile("446.0.0.49.77")
    assert scraper.PROFILE.name == "v446" and scraper.SELECTORS is scraper.PROFILE.selectors
    assert scraper.PROFILE_WARNING is None


def test_activate_profile_does_not_suggest_a_profile_that_does_not_exist():
    scraper.activate_profile("460.0.0.1.1")
    assert scraper.PROFILE.name == "v445"
    assert scraper.PROFILE_WARNING == (
        "Instagram 460.0.0.1.1 is installed but profile v445 targets 445.x;"
        " run `scraper.py install` to get 445.0.0.45.83"
    )


def test_activate_profile_reports_a_bad_ig_profile(monkeypatch):
    monkeypatch.setattr(scraper, "IG_PROFILE", "v999")
    scraper.activate_profile("445.0.0.45.83")
    assert scraper.PROFILE.name == igprofiles.DEFAULT_PROFILE  # falls back rather than stopping
    assert (scraper.PROFILE_WARNING or "").startswith(
        "IG_PROFILE='v999': no profile directory igprofiles/v999/"
    )


@pytest.mark.parametrize(
    ("argument", "env", "expected"),
    [
        (None, "", "445.0.0.45.83"),  # the active profile's own build
        (None, "444.0.0.1.1", "444.0.0.1.1"),  # IG_APK_VERSION overrides it
        (None, "latest", ""),  # apkeep's latest
        ("446.0.0.49.77", "444.0.0.1.1", "446.0.0.49.77"),  # an explicit argument beats both
        ("latest", "", ""),
    ],
)
def test_apk_version_resolution(monkeypatch, argument, env, expected):
    monkeypatch.setattr(scraper, "IG_APK_VERSION", env)
    assert scraper._apk_version(argument) == expected


# --- per-version behavior overrides ------------------------------------------------------------


class _WithParserOverride(type(igprofiles.load("v445"))):
    """A v445 profile whose parse_hierarchy tags every post, calling the base implementation."""

    def parse_hierarchy(self, base, xml):
        return [{**p, "tagged_by": self.name} for p in base(xml)]


def test_a_profile_method_overrides_a_versioned_function_and_receives_the_base(monkeypatch):
    xml = igprofiles.fixture("v445", "feed.xml").read_text()
    baseline = scraper.parse_hierarchy(xml)
    monkeypatch.setattr(scraper, "PROFILE", _WithParserOverride())
    posts = scraper.parse_hierarchy(xml)
    assert [p["username"] for p in posts] == [p["username"] for p in baseline]
    assert {p["tagged_by"] for p in posts} == {"v445"}


def test_overrides_are_inherited_by_newer_profiles(monkeypatch):
    class Newer(_WithParserOverride):
        major = 446

    xml = igprofiles.fixture("v445", "feed.xml").read_text()
    monkeypatch.setattr(scraper, "PROFILE", Newer())
    assert {p["tagged_by"] for p in scraper.parse_hierarchy(xml)} == {"v446"}


def test_without_an_override_the_base_implementation_runs():
    assert "parse_hierarchy" in scraper._VERSIONED
    assert scraper.parse_hierarchy.base is not scraper.parse_hierarchy
    xml = igprofiles.fixture("v445", "feed.xml").read_text()
    assert scraper.parse_hierarchy(xml) == scraper.parse_hierarchy.base(xml)


def test_a_misnamed_override_is_reported(monkeypatch):
    class Typo(type(igprofiles.load("v445"))):
        def parse_heirarchy(self, base, xml):
            return base(xml)

    monkeypatch.setattr(scraper, "select_profile", lambda requested: (Typo(), None))
    scraper.activate_profile("445.0.0.45.83")
    assert (
        scraper.PROFILE_WARNING == "profile v445 defines parse_heirarchy, which match no @versioned function"
    )
