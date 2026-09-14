"""Semi-automated Instagram version profile development: everything in docs/NEXT.md's "Adding a version"
except deciding what the new selectors or overrides should be.

    python scripts/new_profile.py new 444.0.0.34.72          # scaffold + baseline + check, in one go
    python scripts/new_profile.py scaffold 444.0.0.34.72     # igprofiles/v444/, subclassing the nearest profile
    python scripts/new_profile.py baseline v444              # install that build, capped run, capture every screen
    python scripts/new_profile.py check v444                 # which selector keys each captured screen is missing
    python scripts/new_profile.py promote v444               # captured screens -> scrubbed replay fixtures
    python scripts/new_profile.py validate v444              # preconditions, then mark the profile validated
    python scripts/new_profile.py restore                    # put the default profile's build back on the device

Runs on the host from the dev venv (it writes into app/igprofiles/). Only `baseline`/`new` and `restore`
touch the device, through `docker compose run` with this working tree's app/ mounted into the container,
so a selector edit is live on the next run without rebuilding the image. `baseline` refuses to start
while the app service's own scraper is running (both would drive one device), checks redroid's memory
headroom and the host's free memory first (see CLAUDE.md's host-freeze incident), and asks before it
does anything unless given --yes.

Everything a baseline produces goes to local/data/debug/profile-dev/<profile>/, never the real feed:
    dumps/          numbered hierarchy + screenshot of every screen visited (PROFILE_CAPTURE_DIR)
    posts.sqlite    a scratch database, so a broken profile can't put garbage into the feed
    media/          that database's images
    baseline.log    the container output
    report.md       the latest `check`
"""

import argparse
import inspect
import json
import re
import sqlite3
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "scripts"))

import igprofiles  # noqa: E402
import promote_dump  # noqa: E402
from igprofiles import BaseProfile, screens  # noqa: E402
from instadroid import parsing, versioning  # noqa: E402

PROFILES_DIR = ROOT / "app" / "igprofiles"
DEV_DIR = ROOT / "local" / "data" / "debug" / "profile-dev"  # /debug/profile-dev in the container
CONTAINER_DEV_DIR = "/debug/profile-dev"
_VERSION = re.compile(r"^(\d{3})\.\d+\.\d+\.\d+\.\d+$")
_DUMP = re.compile(r"^(?P<seq>\d{3})-(?P<screen>[a-z_0-9]+?)(?P<fail>-fail)?_hierarchy\.xml$")
# Screens whose dumps the parsers read, so a fixture of them checks parse output, not just selectors.
PARSED_SCREENS = ("feed", "home_feed", "following_list")
# Baseline caps (docs/NEXT.md): enough to reach every screen, short enough to stay light.
DEFAULT_SCROLLS, DEFAULT_STORIES = 5, 2
# Refuse a baseline when redroid already uses more than this share of its limit, or the host has less
# free memory than this: the run adds Instagram's ~800MiB on top (CLAUDE.md).
MAX_START_PERCENT = 60
MIN_HOST_AVAILABLE_MIB = 2048


# --- scaffold --------------------------------------------------------------------------------------


def parse_version(version: str) -> int:
    """The major version of a full Instagram build ("444.0.0.34.72" -> 444). Raises ValueError for
    anything else, or a major below the supported floor."""
    m = _VERSION.match(version.strip())
    if not m:
        raise ValueError(f"{version!r} is not a full Instagram build like 444.0.0.34.72 (see APKPure)")
    major = int(m.group(1))
    if major < igprofiles.MIN_MAJOR:
        raise ValueError(f"Instagram {major} is below the supported floor ({igprofiles.MIN_MAJOR})")
    return major


def nearest_profile(major: int, available: Sequence[str]) -> str:
    """The existing profile closest to `major`, the newer one on a tie: backfilling 444 starts from
    v445, then 443 from v444 once that exists, so each step inherits the fixes of the one before."""
    others = [n for n in available if int(n[1:]) != major]
    if not others:
        raise ValueError("no existing profile to start from")
    return min(others, key=lambda n: (abs(int(n[1:]) - major), -int(n[1:])))


def render_profile(major: int, apk_version: str, parent: str, today: date) -> dict[str, str]:
    """The files of a new, unvalidated profile package that inherits everything from `parent`."""
    parent_major = parent[1:]
    init = f'''"""Instagram {major}: scaffolded from {parent} by scripts/new_profile.py on {today.isoformat()}.

Not validated yet. Override only what differs from {parent}: selector keys in selectors.py, behavior as
methods named after @versioned functions (docs/NEXT.md). `python scripts/new_profile.py validate v{major}`
marks it validated once a baseline run and replay fixtures show it works.
"""

from igprofiles.{parent} import Profile as Profile{parent_major}

from .selectors import SELECTORS


class Profile(Profile{parent_major}):
    major = {major}
    apk_version = "{apk_version}"
    selectors = SELECTORS
    validated = False
    notes = "scaffolded from {parent}; not validated"
'''
    selectors = f'''"""Selectors for Instagram {major}.x.

Starts as {parent}'s. Override a key once `new_profile.py check v{major}` shows {major} differs, e.g.
`SELECTORS = {{**SELECTORS_{parent_major}, "share_id": "..."}}`.
"""

from igprofiles.{parent}.selectors import SELECTORS as SELECTORS_{parent_major}

SELECTORS = {{**SELECTORS_{parent_major}}}
'''
    return {"__init__.py": init, "selectors.py": selectors}


def scaffold(
    version: str, parent: str | None = None, root: Path = PROFILES_DIR, today: date | None = None
) -> Path:
    """Create igprofiles/v<major>/ for `version`. Refuses to overwrite an existing profile."""
    major = parse_version(version)
    available = igprofiles.available()
    parent = igprofiles.normalize(parent) if parent else nearest_profile(major, available)
    if parent not in available:
        raise ValueError(f"no profile {parent} to start from (available: {', '.join(available)})")
    target = root / f"v{major}"
    if target.exists():
        raise ValueError(
            f"{target.relative_to(ROOT) if target.is_relative_to(ROOT) else target} already exists"
        )
    target.mkdir(parents=True)
    for name, text in render_profile(major, version.strip(), parent, today or date.today()).items():
        (target / name).write_text(text)
    return target


def parent_of(profile: BaseProfile) -> BaseProfile | None:
    """The profile this one subclasses (v445 for a scaffolded v444), or None for a root profile."""
    for cls in inspect.getmro(type(profile))[1:]:
        if cls is not BaseProfile and issubclass(cls, BaseProfile) and "major" in vars(cls):
            return cls()
    return None


# --- baseline --------------------------------------------------------------------------------------


def dev_dir(profile: str, root: Path = DEV_DIR) -> Path:
    return root / igprofiles.normalize(profile)


def baseline_env(
    profile: str, scrolls: int = DEFAULT_SCROLLS, stories: int = DEFAULT_STORIES, following: bool = False
) -> dict[str, str]:
    """Container environment for a baseline: the profile, capture mode, a scratch database and media
    directory, the run caps, and no FreshRSS ping (nothing it stores belongs in the real feed)."""
    name = igprofiles.normalize(profile)
    base = f"{CONTAINER_DEV_DIR}/{name}"
    return {
        "IG_PROFILE": name,
        "IG_APK_VERSION": "",  # the profile's own build, whatever .env pins
        "PROFILE_CAPTURE_DIR": f"{base}/dumps",
        "DB_PATH": f"{base}/posts.sqlite",
        "MEDIA_DIR": f"{base}/media",
        "MAX_SCROLLS": str(scrolls),
        "MAX_STORIES_PER_RUN": str(stories),
        "FOLLOWING_REFRESH_DAYS": "1" if following else "0",
        "FRESHRSS_REFRESH_URL": "",
        "FRESHRSS_REFRESH_URL_FILE": "",
    }


def compose_run(args: Sequence[str], env: dict[str, str], root: Path = ROOT) -> list[str]:
    """`docker compose run` for the app service with this working tree's app/ mounted over the image's,
    so the profile being developed (and any selector edit) is what runs, without a rebuild."""
    cmd = ["docker", "compose", "--project-directory", str(root), "run", "--rm", "--no-deps"]
    cmd += ["-v", f"{root / 'app'}:/app:ro"]
    for key, value in env.items():
        cmd += ["-e", f"{key}={value}"]
    return [*cmd, "app", "python", "scraper.py", *args]


_UNITS = {"B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "kB": 1000, "MB": 1000**2, "GB": 1000**3}


def parse_mem_usage(text: str) -> tuple[int, int]:
    """(used, limit) bytes from `docker stats`' MemUsage column, e.g. "1.03GiB / 3GiB"."""
    sizes = re.findall(r"([\d.]+)\s*([KMG]?i?B|kB|MB|GB)", text)
    if len(sizes) != 2:
        raise ValueError(f"unrecognized docker stats memory usage {text!r}")
    used, limit = (int(float(n) * _UNITS[u]) for n, u in sizes)
    return used, limit


def host_available_mib(meminfo: str) -> int:
    m = re.search(r"^MemAvailable:\s+(\d+) kB", meminfo, re.M)
    return int(m.group(1)) // 1024 if m else 0


@dataclass
class HostState:
    redroid_running: bool
    app_running: bool
    redroid_mem: str  # docker stats MemUsage, "" if unknown
    host_available_mib: int


def preflight_problems(state: HostState, memory: bool = True) -> list[str]:
    """Reasons not to drive the device now; empty means go. `memory=False` skips the headroom checks,
    for an install alone (restore), which doesn't open Instagram."""
    problems = []
    if not state.redroid_running:
        problems.append("redroid isn't running: `docker compose up -d redroid` first")
    if state.app_running:
        problems.append(
            "the app service is running, and its scraper loop drives the same device:"
            " `docker compose stop app` first (`docker compose start app` after, once the device is restored)"
        )
    if not memory:
        return problems
    if state.redroid_mem:
        used, limit = parse_mem_usage(state.redroid_mem)
        if limit and used * 100 > limit * MAX_START_PERCENT:
            problems.append(
                f"redroid already uses {state.redroid_mem} (over {MAX_START_PERCENT}%): force-stop Instagram"
                " (`adb -s 127.0.0.1:5555 shell am force-stop com.instagram.android`) and check again"
            )
    elif state.redroid_running:
        problems.append("couldn't read redroid's memory usage from `docker stats`")
    if state.host_available_mib < MIN_HOST_AVAILABLE_MIB:
        problems.append(
            f"the host has {state.host_available_mib} MiB available, under {MIN_HOST_AVAILABLE_MIB} MiB:"
            " close something first"
        )
    return problems


def _output(cmd: Sequence[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout.strip()
    except OSError, subprocess.TimeoutExpired:
        return ""


def read_host_state() -> HostState:
    def running(container: str) -> bool:
        return _output(["docker", "inspect", "-f", "{{.State.Running}}", container]) == "true"

    redroid = running("ig-redroid")
    try:
        meminfo = Path("/proc/meminfo").read_text()
    except OSError:
        meminfo = ""
    return HostState(
        redroid_running=redroid,
        app_running=running("ig-app"),
        redroid_mem=_output(["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", "ig-redroid"])
        if redroid
        else "",
        host_available_mib=host_available_mib(meminfo),
    )


def _run_logged(cmd: Sequence[str], log_path: Path) -> int:
    """Run `cmd` from the repo root, streaming its output to the terminal and appending it to log_path."""
    print("$", " ".join(cmd), flush=True)
    with (
        log_path.open("a") as log,
        subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) as proc,
    ):
        log.write("$ " + " ".join(cmd) + "\n")
        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stdout.write(line)
            log.write(line)
    return proc.returncode


def _confirm(prompt: str) -> bool:
    return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")


def baseline(
    profile_name: str,
    scrolls: int = DEFAULT_SCROLLS,
    stories: int = DEFAULT_STORIES,
    following: bool = False,
    install: bool = True,
    yes: bool = False,
) -> int:
    profile = igprofiles.load(profile_name)
    if problems := preflight_problems(read_host_state()):
        print("Not starting the baseline run:")
        for p in problems:
            print("  -", p)
        return 1
    out = dev_dir(profile.name)
    print(
        f"Baseline for {profile.name}: {'install Instagram ' + profile.apk_version + ' (replacing what is installed, a downgrade included), then ' if install else ''}"
        f"one run capped at {scrolls} screens and {stories} stories"
        f"{', plus the Following list' if following else ''}, into a scratch database.\n"
        f"Every screen visited is saved to {out.relative_to(ROOT)}/dumps. A downgrade may need a fresh login;"
        " a login challenge stops the run for you to finish in scrcpy."
    )
    if not yes and not _confirm("Drive the device now?"):
        return 1
    for sub in ("dumps", "media"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    env = baseline_env(profile.name, scrolls, stories, following)
    log_path = out / "baseline.log"
    if install and _run_logged(compose_run(["install"], env), log_path):
        print(f"install failed; see {log_path.relative_to(ROOT)}. Is {profile.apk_version} still on APKPure?")
        return 1
    code = _run_logged(compose_run(["once"], env), log_path)
    print(f"\nrun {'failed' if code else 'finished'}; checking what was captured\n")
    check(profile.name)
    print(
        f"\nThe device now has Instagram {profile.apk_version}. Before `docker compose start app`, run"
        " `python scripts/new_profile.py restore` to put the default profile's build back."
    )
    return code


def restore(yes: bool = False) -> int:
    default = igprofiles.load(igprofiles.DEFAULT_PROFILE)
    if problems := preflight_problems(read_host_state(), memory=False):
        print("Not restoring:", *problems, sep="\n  - ")
        return 1
    if not yes and not _confirm(f"Install {default.apk_version} ({default.name}) on the device?"):
        return 1
    env = {"IG_PROFILE": default.name, "IG_APK_VERSION": ""}
    return subprocess.run(compose_run(["install"], env), cwd=ROOT).returncode


# --- check -----------------------------------------------------------------------------------------


@dataclass
class DumpReport:
    path: Path
    screen: str
    failure: bool
    check: screens.ScreenCheck
    parsed: str  # what the parsers find under the profile, "" for a screen they don't read
    parsed_parent: str  # the same under the parent profile, when it differs


@dataclass
class RunSummary:
    ig_version: str | None
    error: str | None
    warning: str | None
    new_posts: int
    new_stories: int
    mem_peak_mb: int | None
    redroid_image: str | None = None
    android_release: str | None = None


def captured_dumps(dumps: Path) -> list[tuple[Path, str, bool]]:
    """(path, screen, failure) for every capture in `dumps`, in capture order."""
    found = []
    for path in sorted(dumps.glob("*_hierarchy.xml")) if dumps.is_dir() else []:
        if m := _DUMP.match(path.name):
            found.append((path, m["screen"], bool(m["fail"])))
    return found


def _with_profile[T](profile: BaseProfile, fn: Callable[[], T]) -> T:
    previous = versioning.PROFILE
    versioning.PROFILE = profile
    try:
        return fn()
    finally:
        versioning.PROFILE = previous


def _parse_summary(xml: str, screen: str) -> str:
    if screen not in PARSED_SCREENS:
        return ""
    found = parsing.parse_screen(xml)
    posts = found["posts"]
    return (
        f"{len(posts)} post(s) ({sum(bool(p['caption']) for p in posts)} captioned,"
        f" {sum(bool(p['complete']) for p in posts)} complete), {len(found['story_tray'])} story tray item(s),"
        f" {len(found['following_list'])} following row(s)"
    )


def check_dumps(profile: BaseProfile, dumps: Path) -> list[DumpReport]:
    parent = parent_of(profile)
    reports = []
    for path, screen, failure in captured_dumps(dumps):
        xml = path.read_text()
        parsed = _with_profile(profile, lambda xml=xml, screen=screen: _parse_summary(xml, screen))
        parsed_parent = (
            _with_profile(parent, lambda xml=xml, screen=screen: _parse_summary(xml, screen))
            if parent
            else ""
        )
        reports.append(
            DumpReport(
                path=path,
                screen=screen,
                failure=failure,
                check=screens.check_screen(xml, screen, profile.selectors),
                parsed=parsed,
                parsed_parent=parsed_parent if parsed_parent != parsed else "",
            )
        )
    return reports


def run_summary(db_path: Path) -> RunSummary | None:
    """The latest run recorded in a baseline's scratch database, or None."""
    if not db_path.exists():
        return None
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        try:
            row = con.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        except sqlite3.OperationalError:
            return None
    if not row:
        return None
    keys = row.keys()
    return RunSummary(
        ig_version=row["ig_version"] if "ig_version" in keys else None,
        error=row["error"],
        warning=row["warning"] if "warning" in keys else None,
        new_posts=row["new_posts"] or 0,
        new_stories=(row["new_stories"] if "new_stories" in keys else 0) or 0,
        mem_peak_mb=row["mem_peak_mb"] if "mem_peak_mb" in keys else None,
        redroid_image=row["redroid_image"] if "redroid_image" in keys else None,
        android_release=row["android_release"],
    )


def render_report(profile: BaseProfile, reports: list[DumpReport], run: RunSummary | None) -> str:
    parent = parent_of(profile)
    lines = [
        f"# {profile.name} check ({profile.apk_version}{', parent ' + parent.name if parent else ''})",
        "",
    ]
    if run:
        status = f"error: `{run.error.splitlines()[0]}`" if run.error else "no error"
        lines += [
            f"Latest baseline run: Instagram {run.ig_version or '?'}, {status}, {run.new_posts} new post(s),"
            f" {run.new_stories} new stor(ies), peak {run.mem_peak_mb or '?'} MiB.",
        ]
        if run.warning:
            lines.append(f"Run warning: {run.warning}")
        if run.ig_version and igprofiles.major_of(run.ig_version) != profile.major:
            lines.append(f"**The run scraped Instagram {run.ig_version}, not {profile.major}.x.**")
        lines.append("")
    if not reports:
        return "\n".join([*lines, "No captured screens. Run `new_profile.py baseline` first.", ""])
    lines += ["| Dump | Screen | Result | Missing required keys | Parsed |", "|---|---|---|---|---|"]
    for r in reports:
        if r.check.looks_empty:
            result = "⚠ almost no Instagram UI (a popup holding focus, the launcher, a crash dialog?)"
        elif r.check.missing_required:
            result = "⚠ selectors missing"
        else:
            result = "ok"
        if r.failure:
            result += " (failure dump)"
        parsed = r.parsed + (
            f"; under {parent.name}: {r.parsed_parent}" if parent and r.parsed_parent else ""
        )
        missing = ", ".join(f"`{k}`" for k in r.check.missing_required) or "—"
        lines.append(
            f"| {r.path.name.removesuffix('_hierarchy.xml')} | {r.screen} | {result} | {missing} | {parsed or '—'} |"
        )
    lines.append("")
    by_screen: dict[str, list[DumpReport]] = {}
    for r in reports:
        by_screen.setdefault(r.screen, []).append(r)
    lines.append("## By screen")
    lines.append("")
    for screen, spec in screens.SCREENS.items():
        got = [r for r in by_screen.get(screen, []) if not r.check.looks_empty]
        if not got:
            lines.append(f"- **{screen}**: not captured ({spec.description}), so its keys are unchecked.")
            continue
        never = [k for k in spec.required if all(k in r.check.missing_required for r in got)]
        sometimes = [k for k in spec.optional if all(k in r.check.missing_optional for r in got)]
        verdict = (
            f"required keys never matched: {', '.join(f'`{k}`' for k in never)}"
            if never
            else "all required keys matched"
        )
        note = f"; optional, not seen: {', '.join(sometimes)}" if sometimes else ""
        lines.append(f"- **{screen}** ({len(got)} dump(s)): {verdict}{note}")
    lines += [
        "",
        "A required key missing from every dump of its screen is almost certainly drift: find the new value in",
        "the dump (resource-id, content-desc or text) and override that key in "
        f"`app/igprofiles/{profile.name}/selectors.py`, then check again. The screenshot next to each dump",
        "shows what was on screen.",
        "",
    ]
    return "\n".join(lines)


def check(profile_name: str, dumps: Path | None = None) -> bool:
    """Print and save the report; True when every captured screen has its required keys."""
    profile = igprofiles.load(profile_name)
    out = dev_dir(profile.name)
    reports = check_dumps(profile, dumps or out / "dumps")
    text = render_report(profile, reports, run_summary(out / "posts.sqlite"))
    print(text)
    if out.is_dir():
        (out / "report.md").write_text(text)
    return bool(reports) and all(r.check.ok for r in reports if not r.failure)


# --- promote ---------------------------------------------------------------------------------------


def pick_fixtures(reports: list[DumpReport], wanted: Sequence[str] = PARSED_SCREENS) -> dict[str, Path]:
    """{screen: dump} for the first clean, non-failure capture of each wanted screen. A feed screen
    also needs at least one parsed post, or its fixture would only prove that nothing parses."""
    picked: dict[str, Path] = {}
    for r in reports:
        if r.screen not in wanted or r.screen in picked or r.failure or not r.check.ok:
            continue
        if r.screen == "feed" and r.parsed.startswith("0 post"):
            continue
        picked[r.screen] = r.path
    return picked


def promote(profile_name: str, wanted: Sequence[str] = PARSED_SCREENS) -> int:
    profile = igprofiles.load(profile_name)
    picked = pick_fixtures(check_dumps(profile, dev_dir(profile.name) / "dumps"), wanted)
    for screen in wanted:
        if screen not in picked:
            print(f"- {screen}: no clean capture to promote")
    for screen, dump in picked.items():
        try:
            promote_dump.print_promoted(promote_dump.promote(dump, profile.name, screen))
        except ValueError as e:
            print(f"- {screen}: {e}")
        print()
    return 0 if picked else 1


# --- validate --------------------------------------------------------------------------------------


def validation_problems(profile: BaseProfile, run: RunSummary | None) -> list[str]:
    """What still stands between `profile` and validated."""
    problems = []
    fixtures = sorted(igprofiles.fixture(profile.name, "").glob("*.expected.json"))
    if not fixtures:
        problems.append(f"no replay fixtures: `new_profile.py promote {profile.name}`")

    for expected in fixtures:
        xml = expected.with_name(expected.name.removesuffix(".expected.json") + ".xml").read_text()
        parsed = _with_profile(profile, lambda xml=xml: json.loads(json.dumps(parsing.parse_screen(xml))))
        if parsed != json.loads(expected.read_text()):
            problems.append(
                f"{expected.name} no longer parses as recorded (re-record with promote_dump.py --update)"
            )
        screen = expected.name.removesuffix(".expected.json")
        if screen in screens.SCREENS:
            result = screens.check_screen(xml, screen, profile.selectors)
            if result.missing_required:
                problems.append(
                    f"fixture {screen}.xml is missing required keys {', '.join(result.missing_required)}"
                )
    if run is None:
        problems.append(f"no baseline run recorded: `new_profile.py baseline {profile.name}`")
    else:
        if igprofiles.major_of(run.ig_version) != profile.major:
            problems.append(
                f"the latest baseline run scraped Instagram {run.ig_version}, not {profile.major}.x"
            )
        if run.error:
            problems.append(f"the latest baseline run failed: {run.error.splitlines()[0]}")
        if not run.new_posts:
            problems.append("the latest baseline run stored no posts")
    return problems


def mark_validated(init: Path) -> None:
    text = init.read_text()
    if not re.search(r"^    validated = False$", text, re.M):
        raise ValueError(f"no `validated = False` line in {init}")
    init.write_text(re.sub(r"^    validated = False$", "    validated = True", text, count=1, flags=re.M))


def validate(profile_name: str) -> int:
    profile = igprofiles.load(profile_name)
    if profile.validated:
        print(f"{profile.name} is already validated")
        return 0
    run = run_summary(dev_dir(profile.name) / "posts.sqlite")
    if problems := validation_problems(profile, run):
        print(f"{profile.name} can't be marked validated yet:")
        for p in problems:
            print("  -", p)
        return 1
    mark_validated(PROFILES_DIR / profile.name / "__init__.py")
    assert run is not None
    print(f"marked {profile.name} validated. Still by hand:")
    print(f"  - update `notes` (and the docstring) in app/igprofiles/{profile.name}/__init__.py")
    print("  - add a row to docs/COMPATIBILITY.md and a run log entry to docs/NEXT.md:")
    print(
        f"    | `{run.redroid_image or '?'}` | {run.android_release or '?'} | {run.ig_version} | `{profile.name}` | ✅ works | {date.today().isoformat()} |"
        f" {run.new_posts} post(s), {run.new_stories} stor(ies) in the baseline run. |"
    )
    return 0


# --- CLI -------------------------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    def run_options(p: argparse.ArgumentParser) -> None:
        p.add_argument("--scrolls", type=int, default=DEFAULT_SCROLLS, help="MAX_SCROLLS for the run")
        p.add_argument("--stories", type=int, default=DEFAULT_STORIES, help="MAX_STORIES_PER_RUN for the run")
        p.add_argument("--following", action="store_true", help="also visit the Following list")
        p.add_argument("--yes", action="store_true", help="don't ask before driving the device")

    p = sub.add_parser("new", help="scaffold + baseline + check")
    p.add_argument("version")
    p.add_argument("--parent")
    run_options(p)
    p = sub.add_parser("scaffold", help="create igprofiles/vXYZ/")
    p.add_argument("version")
    p.add_argument("--parent", help="profile to subclass (default: the nearest one)")
    p = sub.add_parser("baseline", help="install the build, capped run, capture screens")
    p.add_argument("profile")
    p.add_argument("--no-install", action="store_true", help="the right build is already installed")
    run_options(p)
    p = sub.add_parser("check", help="report selector keys per captured screen")
    p.add_argument("profile")
    p.add_argument("--dumps", type=Path, help="directory of captured dumps (default: the baseline's)")
    p = sub.add_parser("promote", help="captured screens -> replay fixtures")
    p.add_argument("profile")
    p.add_argument("--screens", default=",".join(PARSED_SCREENS), help="comma-separated screens")
    p = sub.add_parser("validate", help="check preconditions and mark validated")
    p.add_argument("profile")
    p = sub.add_parser("restore", help="install the default profile's build again")
    p.add_argument("--yes", action="store_true")

    opts = ap.parse_args(argv)
    try:
        match opts.command:
            case "new":
                path = scaffold(opts.version, opts.parent)
                print(f"created {path.relative_to(ROOT)}")
                return baseline(path.name, opts.scrolls, opts.stories, opts.following, yes=opts.yes)
            case "scaffold":
                path = scaffold(opts.version, opts.parent)
                print(
                    f"created {path.relative_to(ROOT)}; next: `python scripts/new_profile.py baseline {path.name}`"
                )
                return 0
            case "baseline":
                return baseline(
                    opts.profile, opts.scrolls, opts.stories, opts.following, not opts.no_install, opts.yes
                )
            case "check":
                return 0 if check(opts.profile, opts.dumps) else 1
            case "promote":
                return promote(opts.profile, [s for s in opts.screens.split(",") if s])
            case "validate":
                return validate(opts.profile)
            case "restore":
                return restore(opts.yes)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
