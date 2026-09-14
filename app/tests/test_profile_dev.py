"""Profile development: capture mode in the scraper, the screen/selector check (igprofiles.screens),
and scripts/new_profile.py (scaffold, preflight, baseline commands, check, promote, validate).
Nothing here runs docker or touches a device."""

import sqlite3
import sys
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import igprofiles
import new_profile
import pytest
from igprofiles import screens
from instadroid import config, db, diagnostics, scrape, versioning

from tests.fakedevice import FakeDevice, hierarchy, node
from tests.test_device_flows import fast_offline, feed_device  # noqa: F401 (fast_offline is autouse)

V445 = igprofiles.load("v445")
FEED_XML = igprofiles.fixture("v445", "feed.xml").read_text()


# --- igprofiles.screens --------------------------------------------------------------------------


def test_every_selector_key_belongs_to_a_screen_or_is_situational() -> None:
    placed = {k for s in screens.SCREENS.values() for k in (*s.required, *s.optional)} | set(
        screens.SITUATIONAL
    )
    for name in igprofiles.available():
        keys = set(igprofiles.load(name).selectors)
        assert keys <= placed, f"{name}: place {keys - placed} in igprofiles/screens.py"
    assert placed <= set(V445.selectors), (
        f"screens.py names keys no profile has: {placed - set(V445.selectors)}"
    )


def test_the_recorded_v445_feed_has_every_required_feed_key() -> None:
    result = screens.check_screen(FEED_XML, "feed", V445.selectors)
    assert result.ok and not result.missing_required
    assert {"share_id", "header_desc", "caption_class"} <= set(result.matched)


def test_a_moved_resource_id_is_reported_missing() -> None:
    moved = FEED_XML.replace("row_feed_button_share", "row_feed_share_button")
    assert screens.check_screen(moved, "feed", V445.selectors).missing_required == ["share_id"]


def test_a_popup_only_dump_looks_empty() -> None:
    popup = hierarchy(node("context_menu", bounds=(0, 1000, 1080, 1022)))
    result = screens.check_screen(popup, "feed", V445.selectors)
    assert result.looks_empty and not result.ok


@pytest.mark.parametrize(
    ("name", "screen"),
    [
        ("last", "feed"),
        ("empty_feed1", "feed"),
        ("feed_switch", "feed"),
        ("feed_switch_menu2", "feed_switch_menu"),
        ("home_feed_open", "home_feed"),
        ("following_list_profile0", "profile"),
        ("following_list_first", "following_list"),
        ("story_some.user", "story_viewer"),
        ("share_sheet", "share_sheet"),
        ("login", "login"),
        ("manual", None),
    ],
)
def test_screen_of_dump(name: str, screen: str | None) -> None:
    assert screens.screen_of_dump(name) == screen


def test_key_matching_follows_the_scrapers_comparisons() -> None:
    nodes = list(
        __import__("lxml.etree")
        .etree.fromstring(
            hierarchy(
                node("feed_tab"),
                node(desc="Turn sound on"),
                node(text="Password"),
                node(cls="com.instagram.ui.widget.textview.IgTextLayoutView", text="user hi… more"),
            ).encode()
        )
        .iter("node")
    )
    s = V445.selectors
    assert screens.key_matches("home_tab_id", s["home_tab_id"], nodes)
    assert not screens.key_matches("profile_tab_id", s["profile_tab_id"], nodes)
    assert screens.key_matches("mute_toggle_desc_prefix", s["mute_toggle_desc_prefix"], nodes)
    assert screens.key_matches("login_password_hints", s["login_password_hints"], nodes)
    assert screens.key_matches("caption_class", s["caption_class"], nodes)
    assert screens.key_matches("caption_more_suffix", s["caption_more_suffix"], nodes)  # search, not match
    assert screens.key_matches("alt_kind", s["alt_kind"], nodes) is None


# --- capture mode --------------------------------------------------------------------------------


@pytest.fixture
def capture_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    out = tmp_path / "capture"
    monkeypatch.setattr(config, "PROFILE_CAPTURE_DIR", str(out))
    return out


def _captures(directory: Path) -> list[str]:
    return sorted(p.name.removesuffix("_hierarchy.xml") for p in directory.glob("*_hierarchy.xml"))


def test_capture_is_off_by_default(tmp_path: Path) -> None:
    diagnostics.capture_screen(FakeDevice({"a": FEED_XML}, "a"), "feed")
    assert not list(tmp_path.rglob("*_hierarchy.xml"))


def test_capture_numbers_screens_caps_each_and_always_keeps_failures(
    capture_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "CAPTURE_PER_SCREEN", 2)
    d = FakeDevice({"a": FEED_XML}, "a")
    for _ in range(3):
        diagnostics.capture_screen(d, "feed")
    diagnostics.capture_screen(d, "share_sheet")
    diagnostics.dump_debug(d, "empty_feed0")  # a failure dump lands in the capture too, past the cap
    assert _captures(capture_dir) == ["001-feed", "002-feed", "003-share_sheet", "004-feed-fail"]
    assert (capture_dir / "001-feed_screen.jpg").exists()
    assert (capture_dir / "001-feed_hierarchy.xml").read_text() == FEED_XML


def test_a_capture_failure_never_breaks_the_run(capture_dir: Path) -> None:
    capture_dir.write_text("a file where the directory should be")
    diagnostics.capture_screen(FakeDevice({"a": FEED_XML}, "a"), "feed")  # logs, doesn't raise


def test_a_scrape_run_in_capture_mode_saves_every_screen_it_visits(
    capture_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "PROFILE_CAPTURE_DIR", "")
    plain = scrape.scrape_once(feed_device(), db.db_init())
    monkeypatch.setattr(config, "PROFILE_CAPTURE_DIR", str(capture_dir))
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "captured.sqlite"))
    monkeypatch.setattr(config, "MEDIA_DIR", tmp_path / "captured-media")
    stats = scrape.scrape_once(feed_device(), db.db_init())
    assert stats == plain and stats["new"] == 3  # capturing doesn't change the run
    captured = {name.split("-")[1] for name in _captures(capture_dir)}
    assert {
        "feed_switch_menu",
        "following_feed",
        "home_feed",
        "story_viewer",
        "share_sheet",
        "feed",
    } <= captured
    reports = new_profile.check_dumps(V445, capture_dir)
    feed = [r for r in reports if r.screen == "feed"]
    assert feed and all(r.check.ok for r in feed)
    assert all(r.check.ok for r in reports if not r.failure)
    # The fake's no-clipboard share sheet has no Copy link: exactly the dump a real failure leaves.
    assert all(r.check.missing_required == ["copy_link_desc"] for r in reports if r.failure)


# --- scaffold ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("major", "available", "parent"),
    [
        (444, ["v445", "v446"], "v445"),
        (443, ["v444", "v445", "v446"], "v444"),
        (447, ["v445", "v446"], "v446"),
        (442, ["v440", "v444"], "v444"),  # a tie goes to the newer profile
    ],
)
def test_nearest_profile(major: int, available: list[str], parent: str) -> None:
    assert new_profile.nearest_profile(major, available) == parent


@pytest.mark.parametrize("version", ["444", "v444", "444.0.0", "latest", "439.0.0.1.2"])
def test_parse_version_wants_a_full_supported_build(version: str) -> None:
    with pytest.raises(ValueError):
        new_profile.parse_version(version)


@pytest.fixture
def scratch_profiles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A directory scaffolded profiles land in, importable as igprofiles.vXYZ next to the real ones. Tests
    scaffold a hypothetical v447, a version with no real profile, whose nearest profile is v446."""
    root = tmp_path / "igprofiles"
    root.mkdir()
    monkeypatch.setattr(igprofiles, "__path__", [str(root), *igprofiles.__path__])
    real_available = igprofiles.available
    monkeypatch.setattr(
        igprofiles, "available", lambda: sorted({*real_available(), *(p.name for p in root.glob("v*"))})
    )
    monkeypatch.setattr(new_profile, "PROFILES_DIR", root)
    monkeypatch.setattr(igprofiles, "fixture", lambda name, filename: root / name / "fixtures" / filename)
    yield root
    for mod in [m for m in sys.modules if m.startswith("igprofiles.v447")]:
        del sys.modules[mod]


def test_scaffold_creates_an_unvalidated_profile_inheriting_the_nearest_one(scratch_profiles: Path) -> None:
    path = new_profile.scaffold("447.0.0.34.72", root=scratch_profiles, today=date(2026, 9, 14))
    assert path == scratch_profiles / "v447"
    profile = igprofiles.load("v447")
    assert (profile.major, profile.apk_version, profile.validated) == (447, "447.0.0.34.72", False)
    assert isinstance(profile, type(V445))
    assert profile.selectors == V445.selectors and profile.selectors is not V445.selectors
    assert new_profile.parent_of(profile).name == "v446"  # type: ignore[union-attr]
    assert not versioning._unknown_hooks(profile)
    assert "2026-09-14" in (path / "__init__.py").read_text()
    with pytest.raises(ValueError, match="already exists"):
        new_profile.scaffold("447.0.0.34.72", root=scratch_profiles)


def test_scaffold_rejects_an_unknown_parent(scratch_profiles: Path) -> None:
    with pytest.raises(ValueError, match="no profile v999"):
        new_profile.scaffold("447.0.0.34.72", parent="v999", root=scratch_profiles)


def test_an_unvalidated_profile_runs_with_a_warning(
    scratch_profiles: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    new_profile.scaffold("447.0.0.34.72", root=scratch_profiles)
    monkeypatch.setattr(config, "IG_PROFILE", "v447")
    versioning.activate_profile("447.0.0.34.72")
    assert versioning.PROFILE.name == "v447"
    assert versioning.PROFILE_WARNING == "profile v447 is not validated yet (see docs/NEXT.md)"


def test_mark_validated_flips_only_the_flag(scratch_profiles: Path) -> None:
    init = new_profile.scaffold("447.0.0.34.72", root=scratch_profiles) / "__init__.py"
    before = init.read_text()
    new_profile.mark_validated(init)
    assert init.read_text() == before.replace("validated = False", "validated = True")
    with pytest.raises(ValueError):
        new_profile.mark_validated(init)


# --- baseline ------------------------------------------------------------------------------------


def test_baseline_runs_the_working_tree_against_a_scratch_database() -> None:
    env = new_profile.baseline_env("444", scrolls=5, stories=2)
    assert env["IG_PROFILE"] == "v444"
    assert env["PROFILE_CAPTURE_DIR"] == "/debug/profile-dev/v444/dumps"
    assert env["DB_PATH"] == "/debug/profile-dev/v444/posts.sqlite"
    assert env["MEDIA_DIR"] == "/debug/profile-dev/v444/media"
    assert (env["MAX_SCROLLS"], env["MAX_STORIES_PER_RUN"], env["FOLLOWING_REFRESH_DAYS"]) == ("5", "2", "0")
    assert env["FRESHRSS_REFRESH_URL"] == env["FRESHRSS_REFRESH_URL_FILE"] == ""
    cmd = new_profile.compose_run(["once"], env, root=Path("/repo"))
    assert cmd[:7] == ["docker", "compose", "--project-directory", "/repo", "run", "--rm", "--no-deps"]
    assert cmd[cmd.index("-v") + 1] == "/repo/app:/app:ro"
    assert "IG_PROFILE=v444" in cmd and cmd[-4:] == ["app", "python", "scraper.py", "once"]


@pytest.mark.parametrize(
    ("text", "used", "limit"),
    [
        ("1.03GiB / 3GiB", int(1.03 * 1024**3), 3 * 1024**3),
        ("745.7MiB / 3GiB", int(745.7 * 1024**2), 3 * 1024**3),
    ],
)
def test_parse_mem_usage(text: str, used: int, limit: int) -> None:
    assert new_profile.parse_mem_usage(text) == (used, limit)


def test_host_available_mib() -> None:
    assert new_profile.host_available_mib("MemTotal: 64000000 kB\nMemAvailable:   4194304 kB\n") == 4096


def _state(**kw: object) -> new_profile.HostState:
    values: dict = {
        "redroid_running": True,
        "app_running": False,
        "redroid_mem": "1.0GiB / 3GiB",
        "host_available_mib": 8000,
    } | kw
    return new_profile.HostState(**values)


def test_preflight_passes_with_headroom_and_the_scraper_stopped() -> None:
    assert new_profile.preflight_problems(_state()) == []


@pytest.mark.parametrize(
    ("kw", "problem"),
    [
        ({"redroid_running": False, "redroid_mem": ""}, "redroid isn't running"),
        ({"app_running": True}, "docker compose stop app"),
        ({"redroid_mem": "2.1GiB / 3GiB"}, "force-stop Instagram"),
        ({"redroid_mem": ""}, "couldn't read redroid's memory"),
        ({"host_available_mib": 1500}, "the host has 1500 MiB available"),
    ],
)
def test_preflight_refuses(kw: dict[str, object], problem: str) -> None:
    problems = new_profile.preflight_problems(_state(**kw))
    assert any(problem in p for p in problems), problems


def test_an_install_alone_skips_the_memory_checks() -> None:
    assert new_profile.preflight_problems(_state(redroid_mem="2.9GiB / 3GiB"), memory=False) == []


def test_baseline_stops_before_touching_the_device_when_preflight_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(new_profile, "read_host_state", lambda: _state(app_running=True))
    monkeypatch.setattr(new_profile, "_run_logged", lambda *a: pytest.fail("ran a command"))
    assert new_profile.baseline("v445", yes=True) == 1
    assert "docker compose stop app" in capsys.readouterr().out


def test_baseline_asks_first(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(new_profile, "read_host_state", lambda: _state())
    monkeypatch.setattr(new_profile, "_confirm", lambda prompt: False)
    monkeypatch.setattr(new_profile, "_run_logged", lambda *a: pytest.fail("ran a command"))
    assert new_profile.baseline("v445") == 1


def test_baseline_installs_runs_and_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(new_profile, "DEV_DIR", tmp_path / "dev")
    monkeypatch.setattr(new_profile, "ROOT", tmp_path)
    monkeypatch.setattr(new_profile, "dev_dir", lambda p: tmp_path / "dev" / igprofiles.normalize(p))
    monkeypatch.setattr(new_profile, "read_host_state", lambda: _state())
    commands: list[list[str]] = []
    monkeypatch.setattr(new_profile, "_run_logged", lambda cmd, log: commands.append(list(cmd)) or 0)
    assert new_profile.baseline("v445", yes=True) == 0
    assert [c[-1] for c in commands] == ["install", "once"]
    assert (tmp_path / "dev" / "v445" / "dumps").is_dir()
    out = capsys.readouterr().out
    assert "No captured screens" in out and "new_profile.py restore" in out


# --- check, promote, validate --------------------------------------------------------------------


def _dumps(directory: Path, files: dict[str, str]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for stem, xml in files.items():
        (directory / f"{stem}_hierarchy.xml").write_text(xml)
    return directory


def test_check_report_flags_drift_popups_and_uncaptured_screens(tmp_path: Path) -> None:
    dumps = _dumps(
        tmp_path,
        {
            "001-feed": FEED_XML,
            "002-feed": FEED_XML.replace("row_feed_button_share", "moved"),
            "003-feed_switch_menu-fail": hierarchy(node("context_menu")),
        },
    )
    reports = new_profile.check_dumps(V445, dumps)
    assert [(r.screen, r.failure, r.check.ok) for r in reports] == [
        ("feed", False, True),
        ("feed", False, False),
        ("feed_switch_menu", True, False),
    ]
    assert reports[0].parsed.startswith("2 post(s)")
    text = new_profile.render_report(V445, reports, None)
    assert "| 002-feed | feed | ⚠ selectors missing | `share_id` |" in text
    assert "almost no Instagram UI" in text and "(failure dump)" in text
    assert "- **feed** (2 dump(s)): all required keys matched" in text  # share_id matched in 001
    assert "- **story_viewer**: not captured" in text


def test_check_compares_parsing_with_the_parent_when_it_differs(
    scratch_profiles: Path, tmp_path: Path
) -> None:
    path = new_profile.scaffold("447.0.0.34.72", root=scratch_profiles)
    (path / "selectors.py").write_text(
        (path / "selectors.py").read_text() + 'SELECTORS["share_id"] = "row_feed_share_button"\n'
    )
    profile = igprofiles.load("v447")
    report = new_profile.check_dumps(profile, _dumps(tmp_path / "d", {"001-feed": FEED_XML}))[0]
    assert report.check.missing_required == ["share_id"]
    assert report.parsed_parent and report.parsed != report.parsed_parent


def _record_run(db_path: Path, **values: object) -> None:
    con = sqlite3.connect(db_path)
    con.execute(
        "CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT, new_posts INTEGER,"
        " error TEXT, warning TEXT, ig_version TEXT, new_stories INTEGER, mem_peak_mb INTEGER,"
        " redroid_image TEXT, android_release TEXT)"
    )
    row = {"ig_version": "447.0.0.34.72", "error": None, "new_posts": 3, "new_stories": 1} | values
    con.execute(
        f"INSERT INTO runs ({','.join(row)}) VALUES ({','.join('?' * len(row))})", tuple(row.values())
    )
    con.commit()
    con.close()


def test_validation_needs_fixtures_and_a_clean_baseline_of_the_right_version(
    scratch_profiles: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    new_profile.scaffold("447.0.0.34.72", root=scratch_profiles)
    profile = igprofiles.load("v447")
    problems = new_profile.validation_problems(profile, None)
    assert any("no replay fixtures" in p for p in problems) and any("no baseline run" in p for p in problems)

    dev = tmp_path / "dev" / "v447"
    monkeypatch.setattr(new_profile, "dev_dir", lambda p: dev)
    _dumps(dev / "dumps", {"001-feed": FEED_XML, "002-feed": FEED_XML})
    assert new_profile.promote("v447") == 0
    fixtures = scratch_profiles / "v447" / "fixtures"
    assert (fixtures / "feed.xml").exists() and (fixtures / "feed.expected.json").exists()

    db_path = dev / "posts.sqlite"
    _record_run(db_path, ig_version="445.0.0.45.83", new_posts=0, error="DeviceNotReady('x')")
    problems = new_profile.validation_problems(profile, new_profile.run_summary(db_path))
    assert any("scraped Instagram 445.0.0.45.83" in p for p in problems)
    assert any("failed" in p for p in problems) and any("no posts" in p for p in problems)

    _record_run(db_path)
    assert new_profile.validation_problems(profile, new_profile.run_summary(db_path)) == []
    assert new_profile.validate("v447") == 0
    assert "validated = True" in (scratch_profiles / "v447" / "__init__.py").read_text()


def test_promote_skips_screens_without_a_clean_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    empty_feed = hierarchy(
        node("android:id/list"), node("row_feed_profile_header"), node("row_feed_button_share")
    )
    reports = new_profile.check_dumps(
        V445, _dumps(tmp_path, {"001-feed": empty_feed, "002-home_feed-fail": FEED_XML})
    )
    assert new_profile.pick_fixtures(reports) == {}


def test_default_profile_is_validated() -> None:
    assert igprofiles.load(igprofiles.DEFAULT_PROFILE).validated


def test_pseudonymize_catches_names_no_parser_returns() -> None:
    import promote_dump

    xml = hierarchy(
        node("reels_tray_container", children=[node(desc="me.myself's story, 0 of 24, Unseen.")]),
        node("row_feed_profile_header", desc="suggested.acct posted a video in Some Cafe 5 days ago"),
        node(cls="android.widget.Button", desc="Follow Freddie Mercury"),
        node(desc="suggested.acct and 3 others"),
        node(cls="android.widget.Button", desc="@a_friend"),
        node(desc="Profile picture of other.person"),
        node(cls="android.widget.EditText", hint="Add a comment for other.person..."),
        node("secondary_label", text=" Big Band · Some Song"),
        node(cls="android.widget.Button", desc="collab.shop and other.person"),
    )
    clean = promote_dump.pseudonymize(xml)
    for name in (
        "me.myself",
        "suggested.acct",
        "Freddie Mercury",
        "Some Cafe",
        "a_friend",
        "other.person",
        "Big Band",
        "Some Song",
    ):
        assert name not in clean, name
    assert "Follow Display 1" in clean and "@user" in clean
