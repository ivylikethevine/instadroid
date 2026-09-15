"""Semi-automated Instagram version profile development: everything in docs/PROFILES.md's "Adding a version"
except deciding what a changed selector or override should be. Everything works on an exact build.

    new-profile baseline 447.0.0.12.34   # install it, capped run, capture every screen
    new-profile check 447.0.0.12.34      # selector keys each captured screen is missing
    new-profile promote 447.0.0.12.34    # captured screens -> scrubbed replay fixtures
    new-profile validate 447.0.0.12.34   # preconditions, then record the build as validated
    new-profile fork 447.0.0.12.34       # only on drift: a new igprofiles/v447/
    new-profile restore                  # put the default build (igprofiles.DEFAULT_BUILD) back

A build runs under the profile covering it: the highest one at or below its major version. A profile
exists only where Instagram changed something, so a build that `check` finds nothing wrong with is
just validated with the profile it already uses (its fixtures go there too, named <screen>_<major>).
Only when something drifted does `fork` create a profile for that version, subclassing the covering
one, for the new selector values or overrides.

Runs on the host from the dev venv (it writes into app/igprofiles/). Only `baseline` and `restore` touch
the device, through `docker compose run` with this working tree's app/ mounted into the container, so a
selector edit is live on the next run without rebuilding the image. `baseline` refuses to start while
the app service's own scraper is running (both would drive one device), checks redroid's memory
headroom and the host's free memory first (see docs/INCIDENTS.md, "Host freeze"), and asks before it
does anything unless given --yes.

Everything a baseline produces goes to local/data/debug/profile-dev/<major>/, never the real feed:
    dumps/          numbered hierarchy + screenshot of every screen visited (PROFILE_CAPTURE_DIR)
    posts.sqlite    a scratch database, so a broken profile can't put garbage into the feed
    media/          that database's images
    baseline.log    the container output
    report.md       the latest `check`
"""

import argparse
import inspect
import re
import sqlite3
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import igprofiles
from igprofiles import BaseProfile, screens, version_key
from instadroid import diagnostics, parsing, versioning
from shared import sqlrows
from shared.errors import short_error

from devtools import ROOT, promote_dump

PROFILES_DIR = ROOT / "app" / "igprofiles"
DEV_DIR = ROOT / "local" / "data" / "debug" / "profile-dev"  # /debug/profile-dev in the container
CONTAINER_DEV_DIR = "/debug/profile-dev"
# Screens whose dumps the parsers read, so a fixture of them checks parse output, not just selectors.
PARSED_SCREENS = ("feed", "home_feed", "following_list")
# Baseline caps (docs/PROFILES.md): enough to reach every screen, short enough to stay light.
DEFAULT_SCROLLS, DEFAULT_STORIES = 5, 2
# Refuse a baseline when redroid already uses more than this share of its limit, or the host has less
# free memory than this: the run adds Instagram's ~800MiB on top (docs/INCIDENTS.md).
MAX_START_PERCENT = 60
MIN_HOST_AVAILABLE_MIB = 2048


# --- builds and forks ------------------------------------------------------------------------------


def parse_version(version: str, below_floor: bool = False) -> int:
    """The major version of a full Instagram build ("444.0.0.46.85" -> 444). Raises ValueError for
    anything else, or a major below the supported floor unless `below_floor`."""
    m = igprofiles.BUILD.fullmatch(version.strip())
    if not m:
        raise ValueError(f"{version!r} is not a full Instagram build like 444.0.0.46.85 (see APKPure)")
    major = int(m.group(1))
    if major < igprofiles.MIN_MAJOR and not below_floor:
        raise ValueError(f"Instagram {major} is below the supported floor ({igprofiles.MIN_MAJOR})")
    return major


def covering_profile(build: str, below_floor: bool = False) -> BaseProfile:
    """The profile `build` runs under: the highest one at or below its major version. With
    `below_floor`, a build older than every profile runs under the lowest one, which is how the
    scraper itself treats it; that's for evaluating whether the floor could move, and only baseline
    and check accept it."""
    major = parse_version(build, below_floor)
    name = igprofiles.covering(major) or (igprofiles.available()[0] if below_floor else None)
    if name is None:
        raise ValueError(f"no profile covers Instagram {major}: the oldest is {igprofiles.available()[0]}")
    return igprofiles.load(name)


def render_profile(major: int, parent: str, today: date) -> dict[str, str]:
    """The files of a new profile package for `major`, inheriting everything from `parent`."""
    parent_major = parent[1:]
    init = f'''"""Instagram {major} onward: forked from {parent} by devtools/new_profile.py on {today.isoformat()}.

Holds only what Instagram {major} changed relative to {parent}: selector keys in selectors.py, behavior as
methods named after @versioned functions (docs/PROFILES.md). A profile that changes nothing shouldn't exist.
"""

from igprofiles.{parent} import Profile as Profile{parent_major}

from .selectors import SELECTORS


class Profile(Profile{parent_major}):
    major = {major}
    selectors = SELECTORS
    validated = ()
    notes = "forked from {parent}"
'''
    selectors = f'''"""Selectors for Instagram {major} onward.

{parent}'s, with the keys {major} changed overridden, e.g.
`SELECTORS: Selectors = {{**SELECTORS_{parent_major}, "share_id": "..."}}`.
"""

from igprofiles.base import Selectors
from igprofiles.{parent}.selectors import SELECTORS as SELECTORS_{parent_major}

SELECTORS: Selectors = {{**SELECTORS_{parent_major}}}
'''
    return {"__init__.py": init, "selectors.py": selectors}


def fork(build: str, root: Path = PROFILES_DIR, today: date | None = None) -> Path:
    """Create igprofiles/v<major>/ for `build`, subclassing the profile that covers it now. Only for a
    build whose `check` found drift. Refuses when that profile has already validated builds at or above
    this major: the fork would take them over without anyone checking they still work."""
    major = parse_version(build)
    parent = covering_profile(build)
    if parent.major == major:
        raise ValueError(f"{parent.name} already exists; change it directly")
    if later := [b for b in parent.own_validated if (igprofiles.major_of(b) or 0) >= major]:
        raise ValueError(
            f"{parent.name} has validated builds at or above {major} ({', '.join(later)}), which a v{major} fork"
            f" would take over. If {major} really differs from them, the change point is later: see docs/PROFILES.md"
        )
    target = root / f"v{major}"
    if target.exists():
        raise ValueError(f"{target} already exists")
    target.mkdir(parents=True)
    for name, text in render_profile(major, parent.name, today or date.today()).items():
        (target / name).write_text(text)
    return target


def parent_of(profile: BaseProfile) -> BaseProfile | None:
    """The profile this one subclasses (v424 for a v447 forked from it), or None for the root profile."""
    for cls in inspect.getmro(type(profile))[1:]:
        if cls is not BaseProfile and issubclass(cls, BaseProfile) and "major" in vars(cls):
            return cls()
    return None


# --- baseline --------------------------------------------------------------------------------------


def dev_dir(build: str, root: Path = DEV_DIR) -> Path:
    return root / str(parse_version(build, below_floor=True))


def baseline_env(
    build: str, scrolls: int = DEFAULT_SCROLLS, stories: int = DEFAULT_STORIES, following: bool = False
) -> dict[str, str]:
    """Container environment for a baseline: automatic profile selection, capture mode, a scratch
    database and media directory, the run caps, and no FreshRSS ping (nothing it stores belongs in the
    real feed)."""
    base = f"{CONTAINER_DEV_DIR}/{parse_version(build, below_floor=True)}"
    return {
        "IG_PROFILE": "",  # whichever profile covers the build, whatever .env forces
        "IG_APK_VERSION": "",
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
    sizes = [(m[1], m[2]) for m in re.finditer(r"([\d.]+)\s*([KMG]?i?B|kB|MB|GB)", text)]
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
    problems: list[str] = []
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
        for line in map(str, proc.stdout):  # typeshed types a Popen's pipe as IO[Any]; these are str lines
            sys.stdout.write(line)
            log.write(line)
    return proc.returncode


def _confirm(prompt: str) -> bool:
    return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")


def baseline(
    build: str,
    scrolls: int = DEFAULT_SCROLLS,
    stories: int = DEFAULT_STORIES,
    following: bool = False,
    install: bool = True,
    yes: bool = False,
    below_floor: bool = False,
) -> int:
    profile = covering_profile(build, below_floor)
    if problems := preflight_problems(read_host_state()):
        print("Not starting the baseline run:")
        for p in problems:
            print("  -", p)
        return 1
    out = dev_dir(build)
    steps = (
        f"install Instagram {build} (replacing what is installed, a downgrade included), then "
        if install
        else ""
    )
    print(
        f"Baseline for Instagram {build} under profile {profile.name}: {steps}one run capped at {scrolls}"
        f" screens and {stories} stories{', plus the Following list' if following else ''}, into a scratch"
        f" database.\nEvery screen visited is saved to {out.relative_to(ROOT)}/dumps. A downgrade may need a"
        " fresh login; a login challenge stops the run for you to finish in scrcpy."
    )
    if not yes and not _confirm("Drive the device now?"):
        return 1
    for sub in ("dumps", "media"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    env = baseline_env(build, scrolls, stories, following)
    log_path = out / "baseline.log"
    if install and _run_logged(compose_run(["install", build], env), log_path):
        print(f"install failed; see {log_path.relative_to(ROOT)}. Is {build} still on APKPure?")
        return 1
    code = _run_logged(compose_run(["once"], env), log_path)
    print(f"\nrun {'failed' if code else 'finished'}; checking what was captured\n")
    check(build, below_floor=below_floor)
    print(
        f"\nThe device now has Instagram {build}. Before `docker compose start app`, run"
        " `new-profile restore` to put the default build back."
    )
    return code


def restore(yes: bool = False) -> int:
    build = igprofiles.default_build()
    if build is None:
        print("Not restoring: no default build and none validated")
        return 1
    if problems := preflight_problems(read_host_state(), memory=False):
        print("Not restoring:", *problems, sep="\n  - ")
        return 1
    if not yes and not _confirm(f"Install Instagram {build} on the device?"):
        return 1
    env = {"IG_PROFILE": "", "IG_APK_VERSION": ""}
    return subprocess.run(compose_run(["install", build], env), cwd=ROOT).returncode


# --- check -----------------------------------------------------------------------------------------


@dataclass
class DumpReport:
    path: Path
    screen: str
    failure: bool
    check: screens.ScreenCheck
    parsed: str  # what the parsers find under the profile, "" for a screen they don't read
    parsed_parent: str  # the same under the parent profile, when it differs
    items: int = 0  # posts + story tray items + following rows parsed under the profile


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
    found: list[tuple[Path, str, bool]] = []
    for path in sorted(dumps.glob(f"*{diagnostics.HIERARCHY_SUFFIX}")) if dumps.is_dir() else []:
        if parts := diagnostics.parse_dump_name(path.name):
            found.append((path, *parts))
    return found


def _parse(xml: str, screen: str) -> tuple[str, int]:
    """What the parsers find in a dump under the active profile: a summary, and the number of posts,
    story tray items and following rows. ("", 0) for a screen they don't read."""
    if screen not in PARSED_SCREENS:
        return "", 0
    found = parsing.parse_screen(xml)
    posts = found["posts"]
    summary = (
        f"{len(posts)} post(s) ({sum(bool(p['caption']) for p in posts)} captioned,"
        f" {sum(bool(p['complete']) for p in posts)} complete), {len(found['story_tray'])} story tray item(s),"
        f" {len(found['following_list'])} following row(s)"
    )
    return summary, len(posts) + len(found["story_tray"]) + len(found["following_list"])


def check_dumps(profile: BaseProfile, dumps: Path) -> list[DumpReport]:
    parent = parent_of(profile)
    reports: list[DumpReport] = []
    for path, screen, failure in captured_dumps(dumps):
        xml = path.read_text()
        with versioning.using(profile):
            parsed, items = _parse(xml, screen)
        parsed_parent = ""
        if parent:
            with versioning.using(parent):
                parsed_parent, _ = _parse(xml, screen)
        reports.append(
            DumpReport(
                path=path,
                screen=screen,
                failure=failure,
                check=screens.check_screen(xml, screen, profile.selectors),
                parsed=parsed,
                parsed_parent=parsed_parent if parsed_parent != parsed else "",
                items=items,
            )
        )
    return reports


def run_summary(db_path: Path) -> RunSummary | None:
    """The latest run recorded in a baseline's scratch database, or None."""
    if not db_path.exists():
        return None
    with sqlite3.connect(db_path) as con:
        try:
            row = sqlrows.fetch_one(con.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1"))
        except sqlite3.OperationalError:
            return None
    if row is None:
        return None
    # opt_*: an older runs table may not have every column yet
    return RunSummary(
        ig_version=sqlrows.opt_str(row, "ig_version"),
        error=sqlrows.opt_str(row, "error"),
        warning=sqlrows.opt_str(row, "warning"),
        new_posts=sqlrows.opt_int(row, "new_posts") or 0,
        new_stories=sqlrows.opt_int(row, "new_stories") or 0,
        mem_peak_mb=sqlrows.opt_int(row, "mem_peak_mb"),
        redroid_image=sqlrows.opt_str(row, "redroid_image"),
        android_release=sqlrows.opt_str(row, "android_release"),
    )


def render_report(build: str, profile: BaseProfile, reports: list[DumpReport], run: RunSummary | None) -> str:
    parent = parent_of(profile)
    lines = [
        f"# Instagram {build} check (profile {profile.name}{', parent ' + parent.name if parent else ''})",
        "",
    ]
    if run:
        status = f"error: `{short_error(run.error, None)}`" if run.error else "no error"
        lines += [
            f"Latest baseline run: Instagram {run.ig_version or '?'}, {status}, {run.new_posts} new post(s),"
            f" {run.new_stories} new stor(ies), peak {run.mem_peak_mb or '?'} MiB.",
        ]
        if run.warning:
            lines.append(f"Run warning: {run.warning}")
        if run.ig_version and run.ig_version != build:
            lines.append(f"**The run scraped Instagram {run.ig_version}, not {build}.**")
        lines.append("")
    if not reports:
        return "\n".join([*lines, "No captured screens. Run `new-profile baseline` first.", ""])
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
            f"| {r.path.name.removesuffix(diagnostics.HIERARCHY_SUFFIX)} | {r.screen} | {result} | {missing} | {parsed or '—'} |"
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
        "the dump (resource-id, content-desc or text), then `new-profile fork` this build and override that",
        "key in the new profile's selectors.py, and check again. The screenshot next to each dump shows what",
        f"was on screen. With nothing missing, `promote` and `validate` record {build} under {profile.name}.",
        "",
    ]
    return "\n".join(lines)


def check(build: str, dumps: Path | None = None, below_floor: bool = False) -> bool:
    """Print and save the report; True when every captured screen has its required keys."""
    profile = covering_profile(build, below_floor)
    out = dev_dir(build)
    reports = check_dumps(profile, dumps or out / "dumps")
    text = render_report(build, profile, reports, run_summary(out / "posts.sqlite"))
    print(text)
    if out.is_dir():
        (out / "report.md").write_text(text)
    return bool(reports) and all(r.check.ok for r in reports if not r.failure)


# --- promote ---------------------------------------------------------------------------------------


def pick_fixtures(reports: list[DumpReport], wanted: Sequence[str] = PARSED_SCREENS) -> dict[str, Path]:
    """{screen: dump} for the clean, non-failure capture of each wanted screen with the most parsed
    items (the earliest on a tie). It needs at least one, or its fixture would only prove that nothing
    parses."""
    best: dict[str, DumpReport] = {}
    for r in reports:
        if r.screen not in wanted or r.failure or not r.check.ok or not r.items:
            continue
        if r.screen not in best or r.items > best[r.screen].items:
            best[r.screen] = r
    return {screen: r.path for screen, r in best.items()}


def promote(build: str, wanted: Sequence[str] = PARSED_SCREENS) -> int:
    """Scrub the best capture of each wanted screen into the covering profile's fixtures, named
    <screen>_<major> so every validated version keeps its own."""
    profile = covering_profile(build)
    major = parse_version(build)
    picked = pick_fixtures(check_dumps(profile, dev_dir(build) / "dumps"), wanted)
    for screen in wanted:
        if screen not in picked:
            print(f"- {screen}: no clean capture to promote")
    for screen, dump in picked.items():
        try:
            promote_dump.print_promoted(promote_dump.promote(dump, profile.name, f"{screen}_{major}"))
        except ValueError as e:
            print(f"- {screen}: {e}")
        print()
    return 0 if picked else 1


# --- validate --------------------------------------------------------------------------------------


def validation_problems(profile: BaseProfile, build: str, run: RunSummary | None) -> list[str]:
    """What still stands between `build` and validated with `profile`."""
    problems: list[str] = []
    major = parse_version(build)
    fixtures = sorted(igprofiles.fixture(profile.name, "").glob(f"*_{major}.expected.json"))
    if not fixtures:
        problems.append(f"no replay fixtures for {major}: `new-profile promote {build}`")
    for recorded in fixtures:
        problems += promote_dump.fixture_problems(profile, recorded)
    if run is None:
        problems.append(f"no baseline run recorded: `new-profile baseline {build}`")
    else:
        if run.ig_version != build:
            problems.append(f"the latest baseline run scraped Instagram {run.ig_version}, not {build}")
        if run.error:
            problems.append(f"the latest baseline run failed: {short_error(run.error, None)}")
        if not run.new_posts:
            problems.append("the latest baseline run stored no posts")
    return problems


_VALIDATED = re.compile(r"^    validated = \((?P<body>[^)]*)\)\n", re.M)


def add_validated(init: Path, build: str) -> None:
    """Add `build` to the `validated` tuple in a profile's __init__.py (one build per line, oldest
    first), or give the class one after its `selectors` line."""
    text = init.read_text()
    m = _VALIDATED.search(text)
    builds = {b[1] for b in re.finditer(r'"([^"]+)"', m["body"])} if m else set[str]()
    lines = "".join(f'        "{b}",\n' for b in sorted(builds | {build}, key=version_key))
    block = f"    validated = (\n{lines}    )\n"
    if m:
        text = text[: m.start()] + block + text[m.end() :]
    elif selectors := re.search(r"^    selectors = .*\n", text, re.M):
        text = text[: selectors.end()] + block + text[selectors.end() :]
    else:
        raise ValueError(f"no `validated` or `selectors` line in {init}")
    init.write_text(text)


def validate(build: str) -> int:
    profile = covering_profile(build)
    if build in profile.own_validated:
        print(f"Instagram {build} is already validated with {profile.name}")
        return 0
    run = run_summary(dev_dir(build) / "posts.sqlite")
    if problems := validation_problems(profile, build, run):
        print(f"Instagram {build} can't be marked validated with {profile.name} yet:")
        for p in problems:
            print("  -", p)
        return 1
    add_validated(PROFILES_DIR / profile.name / "__init__.py", build)
    assert run is not None
    print(f"added {build} to {profile.name}'s validated builds. Still by hand:")
    print("  - add a row to docs/COMPATIBILITY.md and a run log entry to docs/RUNLOG.md:")
    print(
        f"    | `{run.redroid_image or '?'}` | {run.android_release or '?'} | {build} | `{profile.name}` | ✅ works"
        f" | {date.today().isoformat()} | {run.new_posts} post(s), {run.new_stories} stor(ies) in the baseline run. |"
    )
    return 0


# --- CLI -------------------------------------------------------------------------------------------


class Options(argparse.Namespace):
    """The parsed command line; each subcommand sets only its own options."""

    command: str
    build: str
    scrolls: int
    stories: int
    following: bool
    yes: bool
    no_install: bool
    below_floor: bool
    dumps: Path | None
    screens: str


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    def floor_option(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--below-floor",
            action="store_true",
            help="allow a build older than every profile, run under the lowest (evaluating the floor)",
        )

    def run_options(p: argparse.ArgumentParser) -> None:
        p.add_argument("--scrolls", type=int, default=DEFAULT_SCROLLS, help="MAX_SCROLLS for the run")
        p.add_argument("--stories", type=int, default=DEFAULT_STORIES, help="MAX_STORIES_PER_RUN for the run")
        p.add_argument("--following", action="store_true", help="also visit the Following list")
        p.add_argument("--yes", action="store_true", help="don't ask before driving the device")

    p = sub.add_parser("baseline", help="install the build, capped run, capture screens")
    p.add_argument("build")
    p.add_argument("--no-install", action="store_true", help="the build is already installed")
    run_options(p)
    floor_option(p)
    p = sub.add_parser("check", help="report selector keys per captured screen")
    p.add_argument("build")
    p.add_argument("--dumps", type=Path, help="directory of captured dumps (default: the baseline's)")
    floor_option(p)
    p = sub.add_parser("promote", help="captured screens -> replay fixtures")
    p.add_argument("build")
    p.add_argument("--screens", default=",".join(PARSED_SCREENS), help="comma-separated screens")
    p = sub.add_parser("validate", help="check preconditions and record the build as validated")
    p.add_argument("build")
    p = sub.add_parser("fork", help="create a profile for a build whose check found drift")
    p.add_argument("build")
    p = sub.add_parser("restore", help="install the default build again")
    p.add_argument("--yes", action="store_true")

    opts = ap.parse_args(argv, namespace=Options())
    try:
        match opts.command:
            case "baseline":
                return baseline(
                    opts.build,
                    opts.scrolls,
                    opts.stories,
                    opts.following,
                    not opts.no_install,
                    opts.yes,
                    opts.below_floor,
                )
            case "check":
                return 0 if check(opts.build, opts.dumps, opts.below_floor) else 1
            case "promote":
                return promote(opts.build, [s for s in opts.screens.split(",") if s])
            case "validate":
                return validate(opts.build)
            case "fork":
                path = fork(opts.build)
                print(
                    f"created {path.relative_to(ROOT)}; override what drifted, then `check {opts.build}` again"
                )
                return 0
            case "restore":
                return restore(opts.yes)
            case _:  # argparse accepts no other command
                pass
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
