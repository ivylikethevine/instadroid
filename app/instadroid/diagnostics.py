"""Debug artifacts: hierarchy/screenshot dumps, failure logcats, and their pruning."""

import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from igprofiles.screens import screen_of_dump
from PIL import Image

from . import config, uidevice
from .common import log

_captured: dict[str, int] = {}  # screen -> pairs saved to PROFILE_CAPTURE_DIR by this process
# A dump is a `<stem>_hierarchy.xml` + `<stem>_screen.jpg` pair (see _write_pair()).
HIERARCHY_SUFFIX, SCREENSHOT_SUFFIX = "_hierarchy.xml", "_screen.jpg"
_DEBUG_ARTIFACT_SUFFIXES = (".xml", ".jpg", ".png", ".txt")  # what age-based pruning may delete


def prune_debug_dumps() -> None:
    """Keep only the newest DEBUG_KEEP hierarchy+screenshot pairs and DEBUG_KEEP failure logcats, and
    delete any debug artifact (.xml/.jpg/.png/.txt, top level only) older than DEBUG_RETAIN_DAYS — manual dumps and one-off
    screenshots don't belong to a pair and otherwise never age out. Anything else in DEBUG_DIR
    (e.g. a scratch test*.sqlite a DB_PATH may still point at) is left alone. Best-effort: a file
    that can't be removed is logged and skipped, never raised."""
    try:
        doomed: list[Path] = []
        hierarchies = sorted(config.DEBUG_DIR.glob(f"*{HIERARCHY_SUFFIX}"), key=lambda p: p.stat().st_mtime)
        for old in hierarchies[: -config.DEBUG_KEEP]:
            stem = old.name.removesuffix(HIERARCHY_SUFFIX)
            doomed += [
                old,
                config.DEBUG_DIR / f"{stem}{SCREENSHOT_SUFFIX}",
                config.DEBUG_DIR / f"{stem}_screen.png",
            ]
        doomed += sorted(config.DEBUG_DIR.glob("logcat_*.txt"), key=lambda p: p.stat().st_mtime)[
            : -config.DEBUG_KEEP
        ]
        if config.DEBUG_RETAIN_DAYS > 0:
            cutoff = time.time() - config.DEBUG_RETAIN_DAYS * 86400
            doomed += [
                f
                for f in config.DEBUG_DIR.iterdir()
                if f.suffix.lower() in _DEBUG_ARTIFACT_SUFFIXES and f.is_file() and f.stat().st_mtime < cutoff
            ]
    except OSError as e:  # e.g. DEBUG_DIR doesn't exist yet, or a file vanished mid-scan
        log("WARN: could not scan debug dumps for pruning:", repr(e))
        return
    for f in doomed:
        try:
            f.unlink(missing_ok=True)
        except OSError as e:
            log(f"WARN: could not prune debug file {f.name!r}:", repr(e))


CRASH_SIGNATURES_FILE = Path(__file__).with_name("crash_signatures.tsv")


def load_crash_signatures(path: Path = CRASH_SIGNATURES_FILE) -> dict[str, str]:
    """{logcat text: fix} for the crash-loop signatures this project has actually hit, from the
    tab-separated table scripts/diagnose.sh reads too. Blank and `#` lines are skipped."""
    rows = (
        line.split("\t", 1) for line in path.read_text().splitlines() if line and not line.startswith("#")
    )
    return {row[0]: row[1] if len(row) > 1 else "" for row in rows}


CRASH_SIGNATURES = load_crash_signatures()
# What a failure logcat keeps besides error/fatal lines: the known crash signatures, plus kills and ANRs.
_LOGCAT_SIGNATURES = re.compile(
    "|".join(
        map(re.escape, [*CRASH_SIGNATURES, "ANR in ", "lowmemorykiller", "am_kill", "am_crash", "am_anr"])
    )
)
_LOGCAT_PRIORITY = re.compile(r"^\S+\s+\S+\s+\d+\s+\d+\s+([VDIWEF])\s")  # `-v threadtime` lines


def _filter_logcat(text: str) -> list[str]:
    """Error/fatal lines and known crash signatures from `logcat -v threadtime` output, last
    LOGCAT_TAIL_LINES of them."""
    kept: list[str] = []
    for line in text.splitlines():
        m = _LOGCAT_PRIORITY.match(line)
        if (m and m.group(1) in "EF") or _LOGCAT_SIGNATURES.search(line):
            kept.append(line)
    return kept[-config.LOGCAT_TAIL_LINES :] if config.LOGCAT_TAIL_LINES > 0 else kept


def save_failure_logcat(error: str) -> Path | None:
    """After a device failure, save a filtered `adb logcat -d` to DEBUG_DIR/logcat_<utc>.txt, so the
    evidence CLAUDE.md says to read before restarting anything is already on disk. Uses the adb CLI
    rather than the uiautomator2 connection, which is often exactly what failed. Best-effort: a device
    too far gone for adb just gets a log line."""
    try:
        out = subprocess.run(
            ["adb", "-s", config.ADB_ADDR, "logcat", "-d", "-v", "threadtime"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=config.LOGCAT_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        log("WARN: could not read logcat after the failure:", repr(e))
        return None
    if out.returncode != 0 or not out.stdout.strip():
        log("WARN: could not read logcat after the failure:", (out.stderr or "no output").strip()[:200])
        return None
    lines = _filter_logcat(out.stdout)
    path = config.DEBUG_DIR / f"logcat_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.txt"
    header = [
        f"# run failed: {error.splitlines()[0][:500] if error else '?'}",
        f"# adb logcat -d: error/fatal lines and known crash signatures, last {len(lines)} kept",
        *(
            f"# seen {sig!r}: {fix}"
            for sig, fix in CRASH_SIGNATURES.items()
            if any(sig in ln for ln in lines)
        ),
    ]
    try:
        config.DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(header + lines) + "\n")
    except OSError as e:
        log("WARN: could not write the failure logcat:", repr(e))
        return None
    prune_debug_dumps()
    log(f"saved filtered logcat ({len(lines)} lines) to {path}")
    return path


def _write_pair(directory: Path, stem: str, xml: str, image: Image.Image) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{stem}{HIERARCHY_SUFFIX}").write_text(xml)
    image.convert("RGB").save(directory / f"{stem}{SCREENSHOT_SUFFIX}", quality=70)


_CAPTURE_NAME = re.compile(rf"^\d{{3}}-(?P<screen>[a-z_0-9]+?)(?P<fail>-fail)?{re.escape(HIERARCHY_SUFFIX)}$")


def dump_stem(seq: int, screen: str, failure: bool) -> str:
    """A profile capture's file stem, `<seq>-<screen>[-fail]`: capture order, then the screen it shows."""
    return f"{seq:03d}-{screen}" + ("-fail" if failure else "")


def parse_dump_name(filename: str) -> tuple[str, bool] | None:
    """(screen, failure) from a profile capture's hierarchy file name (dump_stem() + HIERARCHY_SUFFIX),
    or None for any other file."""
    m = _CAPTURE_NAME.match(filename)
    return (m["screen"], bool(m["fail"])) if m else None


def dump_debug(d: uidevice.Device, name: str, xml: str | None = None) -> None:
    """Save a hierarchy + screenshot pair for later inspection. Pass `xml` when the caller
    already has a fresh dump, to avoid a redundant device round-trip. Best-effort: a debug dump is
    a diagnostic aid, not part of the scrape itself, so a write failure here (e.g. a stale file
    left owned by a different uid from a `docker exec -u root` session) must not crash the whole
    run — it just means this one dump is missing from $DEBUG_DIR. In profile capture mode the pair
    is also kept in PROFILE_CAPTURE_DIR, since these are exactly the screens a new profile breaks on."""
    try:
        xml = xml if xml is not None else d.dump_hierarchy()
        image = d.screenshot()
        _write_pair(config.DEBUG_DIR, name, xml, image)
        prune_debug_dumps()
    except OSError as e:
        log(f"WARN: could not write debug dump {name!r}:", repr(e))
        return
    capture_screen(d, screen_of_dump(name) or name, xml, failure=True, image=image)


def capture_screen(
    d: uidevice.Device,
    screen: str,
    xml: str | None = None,
    failure: bool = False,
    image: Image.Image | None = None,
) -> None:
    """Profile capture mode (PROFILE_CAPTURE_DIR set, see devtools/new_profile.py): save this screen
    as a dump_stem() pair, `<seq>-<screen>[-fail]_hierarchy.xml` + `_screen.jpg`, up to
    CAPTURE_PER_SCREEN per screen (failure dumps always), so a baseline run under a new profile leaves
    one reviewable dump of every screen it reached. Pass `xml`/`image` when the caller already has them,
    to skip the device round-trip. A no-op otherwise, and never raises: capturing must not change a run."""
    if not config.PROFILE_CAPTURE_DIR:
        return
    count = _captured.get(screen, 0)
    if not failure and count >= config.CAPTURE_PER_SCREEN:
        return
    _captured[screen] = count + 1
    seq = sum(_captured.values())
    stem = dump_stem(seq, screen, failure)
    try:
        _write_pair(
            Path(config.PROFILE_CAPTURE_DIR),
            stem,
            xml if xml is not None else d.dump_hierarchy(),
            image if image is not None else d.screenshot(),
        )
    except Exception as e:  # a device hiccup or a full disk; the run itself goes on
        log(f"WARN: could not capture screen {stem!r}:", repr(e))
