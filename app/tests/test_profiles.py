"""Instagram version profiles: discovery and loading (igprofiles), choosing the profile that covers the
installed version (igprofiles.select / versioning.activate_profile), the build to install
(install._apk_version), and per-version behavior overrides (@versioned)."""

import inspect
import types
from collections.abc import Callable

import igprofiles
import pytest
from igprofiles import BaseProfile, major_of, version_key
from instadroid import config, install, parsing, versioning

ROOT = igprofiles.load(igprofiles.available()[0])
FEED_XML = igprofiles.fixture("v440", "feed_445.xml").read_text()

# --- discovery and loading ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("version", "major"),
    [("446.0.0.49.77", 446), ("445.0.0.45.83", 445), (None, None), ("", None), ("garbage", None)],
)
def test_major_of(version: str | None, major: int | None) -> None:
    assert major_of(version) == major


def test_version_key_orders_builds_numerically() -> None:
    builds = ["446.0.0.49.77", "440.1.0.46.86", "440.0.0.46.86", "44.9.0.0.0"]
    assert sorted(builds, key=version_key) == [
        "44.9.0.0.0",
        "440.0.0.46.86",
        "440.1.0.46.86",
        "446.0.0.49.77",
    ]


def test_available_profiles_start_at_the_floor() -> None:
    names = igprofiles.available()
    assert names == sorted(names)
    assert names[0] == f"v{igprofiles.MIN_MAJOR}"  # the root profile is the supported floor


@pytest.mark.parametrize("name", ["v440", "440", " V440 "])
def test_load_accepts_a_name_with_or_without_the_v(name: str) -> None:
    assert igprofiles.load(name).name == "v440"


@pytest.mark.parametrize(
    ("name", "error"),
    [
        ("v439", "below the supported floor (v440)"),
        ("v999", "no profile directory igprofiles/v999/"),
        ("latest", "is not a profile name"),
    ],
)
def test_load_rejects_invalid_names(name: str, error: str) -> None:
    with pytest.raises(ValueError, match=error.replace("(", r"\(").replace(")", r"\)")):
        igprofiles.load(name)


def _fake_profile_dir(
    monkeypatch: pytest.MonkeyPatch, name: str, profile_cls: type[BaseProfile] | None
) -> None:
    real_available = igprofiles.available
    monkeypatch.setattr(igprofiles, "available", lambda: sorted({*real_available(), name}))
    module = types.SimpleNamespace(Profile=profile_cls)
    real_import = igprofiles.importlib.import_module
    monkeypatch.setattr(
        igprofiles.importlib,
        "import_module",
        lambda mod: module if mod == f"igprofiles.{name}" else real_import(mod),
    )


def test_load_rejects_a_profile_whose_major_does_not_match_its_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Profile(BaseProfile):
        major, selectors = 446, {}

    _fake_profile_dir(monkeypatch, "v447", Profile)
    with pytest.raises(ValueError, match="defines major=446, expected 447"):
        igprofiles.load("v447")


def test_load_rejects_validated_builds_below_the_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    class Profile(BaseProfile):
        major, selectors, validated = 447, {}, ("446.0.0.49.77",)

    _fake_profile_dir(monkeypatch, "v447", Profile)
    with pytest.raises(ValueError, match="validated builds below 447: 446.0.0.49.77"):
        igprofiles.load("v447")


def test_load_rejects_a_directory_without_a_profile_class(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_profile_dir(monkeypatch, "v447", None)
    with pytest.raises(ValueError, match=r"must define Profile\(BaseProfile\)"):
        igprofiles.load("v447")


@pytest.mark.parametrize(
    ("major", "profile"),
    [(None, None), (439, None), (440, "v440"), (443, "v440"), (444, "v444"), (449, "v444"), (999, "v450")],
)
def test_covering_is_the_highest_profile_at_or_below(
    monkeypatch: pytest.MonkeyPatch, major: int | None, profile: str | None
) -> None:
    monkeypatch.setattr(igprofiles, "available", lambda: ["v440", "v444", "v450"])
    assert igprofiles.covering(major) == profile


def test_newest_build_is_the_newest_validated_build_of_any_profile() -> None:
    every = [b for n in igprofiles.available() for b in igprofiles.load(n).own_validated]
    assert igprofiles.newest_build() == max(every, key=version_key)
    assert igprofiles.newest_build("v440") == max(ROOT.own_validated, key=version_key)


def test_validated_builds_are_not_inherited() -> None:
    class Profile(type(ROOT)):
        major = 447

    assert Profile().own_validated == () and Profile().validated == ROOT.validated


def test_select_follows_the_installed_version_and_ig_profile_overrides_it() -> None:
    profile, warning = igprofiles.select("", "445.0.0.45.83")
    assert (profile.name, warning) == (igprofiles.covering(445), None)
    assert igprofiles.select("", None)[0].name == igprofiles.available()[-1]  # nothing installed: newest
    profile, warning = igprofiles.select("v440", "999.0.0.1.1")
    assert (profile.name, warning) == ("v440", None)


def test_select_warns_and_carries_on_for_a_bad_ig_profile_or_a_too_old_install() -> None:
    profile, warning = igprofiles.select("v999", "445.0.0.45.83")
    assert profile.name == igprofiles.covering(445)
    assert warning is not None and warning.startswith(
        "IG_PROFILE='v999': no profile directory igprofiles/v999/"
    )
    profile, warning = igprofiles.select("", "430.0.0.1.1")
    assert profile.name == "v440"
    assert warning == "Instagram 430.0.0.1.1 is older than the oldest profile (v440); using it anyway"


# --- the contract every profile directory must meet --------------------------------------------


@pytest.mark.parametrize("name", igprofiles.available())
def test_every_profile_meets_the_contract(name: str) -> None:
    profile = igprofiles.load(name)
    names = igprofiles.available()
    following = names[names.index(name) + 1] if name != names[-1] else None
    assert profile.major >= igprofiles.MIN_MAJOR
    for build in profile.own_validated:  # each validated build is one this profile actually covers
        assert igprofiles.covering(major_of(build)) == name, build
    # Every selector key the scraper reads must exist, so a profile can't silently drop one.
    assert set(ROOT.selectors) <= set(profile.selectors), set(ROOT.selectors) - set(profile.selectors)
    assert not versioning._unknown_hooks(profile), "an override name matches no @versioned function"
    assert following is None or int(following[1:]) > profile.major


def _changes(profile: BaseProfile, parent: BaseProfile) -> bool:
    own_methods = {
        k for k, v in vars(type(profile)).items() if callable(v) and not k.startswith("_") and k != "name"
    }
    return profile.selectors != parent.selectors or bool(own_methods)


@pytest.mark.parametrize("name", igprofiles.available()[1:])
def test_a_profile_exists_only_where_something_changed(name: str) -> None:
    profile = igprofiles.load(name)
    parent_cls = next(
        c for c in inspect.getmro(type(profile))[1:] if issubclass(c, BaseProfile) and "major" in vars(c)
    )
    assert _changes(profile, parent_cls()), (
        f"{name} changes nothing relative to v{parent_cls.major}: delete it and validate its builds there"
    )


def test_the_change_check_notices_selectors_and_overrides() -> None:
    class Same(type(ROOT)):
        major = 447

    class NewSelector(type(ROOT)):
        major, selectors = 447, {**ROOT.selectors, "share_id": "moved"}

    class Override(type(ROOT)):
        major = 447

        def parse_hierarchy(self, base: Callable[[str], list[parsing.Post]], xml: str) -> list[parsing.Post]:
            return base(xml)

    assert not _changes(Same(), ROOT)
    assert _changes(NewSelector(), ROOT) and _changes(Override(), ROOT)


def test_profile_fixtures_live_in_their_profile_directory() -> None:
    path = igprofiles.fixture("440", "feed_445.xml")
    assert path.parts[-3:] == ("v440", "fixtures", "feed_445.xml") and path.is_file()


# --- selection in the scraper -------------------------------------------------------------------


def test_activate_profile_follows_the_installed_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "IG_PROFILE", "")
    versioning.activate_profile("445.0.0.45.83")
    assert versioning.PROFILE.name == igprofiles.covering(445)
    assert versioning.SELECTORS["header_id"] == versioning.PROFILE.selectors["header_id"]
    assert versioning.PROFILE_WARNING is None


def test_activate_profile_warns_about_an_unvalidated_major(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "IG_PROFILE", "")
    versioning.activate_profile("999.0.0.1.1")
    newest = max(versioning.PROFILE.own_validated, key=version_key)
    expected = (
        f"Instagram 999.0.0.1.1 hasn't been validated with profile {versioning.PROFILE.name}"
        f" (newest validated: {newest}; see docs/NEXT.md)"
    )
    assert expected == versioning.PROFILE_WARNING


def test_activate_profile_warns_when_ig_profile_is_not_the_covering_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Profile(type(ROOT)):
        major, selectors, validated = 447, {**ROOT.selectors, "share_id": "moved"}, ("447.0.0.1.1",)

    _fake_profile_dir(monkeypatch, "v447", Profile)
    monkeypatch.setattr(config, "IG_PROFILE", "v440")
    versioning.activate_profile("447.0.0.1.1")
    assert versioning.PROFILE.name == "v440"  # the override wins
    assert (versioning.PROFILE_WARNING or "").startswith(
        "IG_PROFILE=v440 is set, but Instagram 447.0.0.1.1 is covered by v447"
    )


def test_activate_profile_reports_a_bad_ig_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "IG_PROFILE", "v999")
    versioning.activate_profile("445.0.0.45.83")
    assert versioning.PROFILE.name == igprofiles.covering(445)  # falls back rather than stopping
    assert (versioning.PROFILE_WARNING or "").startswith(
        "IG_PROFILE='v999': no profile directory igprofiles/v999/"
    )


@pytest.mark.parametrize(
    ("argument", "env", "expected"),
    [
        (None, "", "newest"),  # the newest validated build
        (None, "444.0.0.1.1", "444.0.0.1.1"),  # IG_APK_VERSION overrides it
        (None, "latest", ""),  # apkeep's latest
        ("446.0.0.49.77", "444.0.0.1.1", "446.0.0.49.77"),  # an explicit argument beats both
        ("latest", "", ""),
    ],
)
def test_apk_version_resolution(
    monkeypatch: pytest.MonkeyPatch, argument: str | None, env: str, expected: str
) -> None:
    monkeypatch.setattr(config, "IG_APK_VERSION", env)
    assert install._apk_version(argument) == (
        igprofiles.newest_build("v440") if expected == "newest" else expected
    )


# --- per-version behavior overrides ------------------------------------------------------------


class _WithParserOverride(type(ROOT)):
    """The root profile, with a parse_hierarchy that tags every post, calling the base implementation."""

    def parse_hierarchy(self, base: Callable[[str], list[parsing.Post]], xml: str) -> list[parsing.Post]:
        return [{**p, "tagged_by": self.name} for p in base(xml)]


def test_a_profile_method_overrides_a_versioned_function_and_receives_the_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = parsing.parse_hierarchy(FEED_XML)
    monkeypatch.setattr(versioning, "PROFILE", _WithParserOverride())
    posts = parsing.parse_hierarchy(FEED_XML)
    assert [p["username"] for p in posts] == [p["username"] for p in baseline]
    assert {p["tagged_by"] for p in posts} == {"v440"}


def test_overrides_are_inherited_by_newer_profiles(monkeypatch: pytest.MonkeyPatch) -> None:
    class Newer(_WithParserOverride):
        major = 446

    monkeypatch.setattr(versioning, "PROFILE", Newer())
    assert {p["tagged_by"] for p in parsing.parse_hierarchy(FEED_XML)} == {"v446"}


def test_without_an_override_the_base_implementation_runs() -> None:
    assert "parse_hierarchy" in versioning._VERSIONED
    assert parsing.parse_hierarchy.base is not parsing.parse_hierarchy
    assert parsing.parse_hierarchy(FEED_XML) == parsing.parse_hierarchy.base(FEED_XML)


def test_a_misnamed_override_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    class Typo(type(ROOT)):
        def parse_heirarchy(self, base: Callable[[str], list[parsing.Post]], xml: str) -> list[parsing.Post]:
            return base(xml)

    monkeypatch.setattr(versioning, "select_profile", lambda requested, installed: (Typo(), None))
    versioning.activate_profile(None)  # nothing installed, so no validation warning alongside it
    assert (
        versioning.PROFILE_WARNING
        == "profile v440 defines parse_heirarchy, which match no @versioned function"
    )
