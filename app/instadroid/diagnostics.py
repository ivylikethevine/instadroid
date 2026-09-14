"""Debug artifacts: hierarchy/screenshot dumps, failure logcats, and their pruning."""

import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import uiautomator2 as u2
from igprofiles.screens import screen_of_dump
from PIL import Image

from . import config
from .common import log

_captured: dict[str, int] = {}  # screen -> pairs saved to PROFILE_CAPTURE_DIR by this process


def prune_debug_dumps() -> None:
    """Keep only the newest DEBUG_KEEP hierarchy+screenshot pairs and DEBUG_KEEP failure logcats, and
    delete any debug artifact (.xml/.jpg/.png/.txt, top level only) older than DEBUG_RETAIN_DAYS — manual dumps and one-off
    screenshots don't belong to a pair and otherwise never age out. Anything else in DEBUG_DIR
    (e.g. a scratch test*.sqlite a DB_PATH may still point at) is left alone. Best-effort: a file
    that can't be removed is logged and skipped, never raised."""
    try:
        doomed = []
        hierarchies = sorted(config.DEBUG_DIR.glob("*_hierarchy.xml"), key=lambda p: p.stat().st_mtime)
        for old in hierarchies[: -config.DEBUG_KEEP]:
            stem = old.name.removesuffix("_hierarchy.xml")
            doomed += [old, config.DEBUG_DIR / f"{stem}_screen.jpg", config.DEBUG_DIR / f"{stem}_screen.png"]
        doomed += sorted(config.DEBUG_DIR.glob("logcat_*.txt"), key=lambda p: p.stat().st_mtime)[
            : -config.DEBUG_KEEP
        ]
        if config.DEBUG_RETAIN_DAYS > 0:
            cutoff = time.time() - config.DEBUG_RETAIN_DAYS * 86400
            doomed += [
                f
                for f in config.DEBUG_DIR.iterdir()
                if f.suffix.lower() in config._DEBUG_ARTIFACT_SUFFIXES
                and f.is_file()
                and f.stat().st_mtime < cutoff
            ]
    except OSError as e:  # e.g. DEBUG_DIR doesn't exist yet, or a file vanished mid-scan
        log("WARN: could not scan debug dumps for pruning:", repr(e))
        return
    for f in doomed:
        try:
            f.unlink(missing_ok=True)
        except OSError as e:
            log(f"WARN: could not prune debug file {f.name!r}:", repr(e))


# What a failure logcat keeps besides error/fatal lines: the crash-loop signatures this project has
# actually hit (CLAUDE.md; scripts/diagnose.sh greps for the same ones), plus kills and ANRs.
_LOGCAT_SIGNATURES = re.compile(
    r"WATCHDOG KILLING|FATAL EXCEPTION|ANR in |Version mismatch in Idmap|Can't downgrade database|"
    r"Bad operation #|There must be exactly one installer|lowmemorykiller|am_kill|am_crash|am_anr"
)
_LOGCAT_PRIORITY = re.compile(r"^\S+\s+\S+\s+\d+\s+\d+\s+([VDIWEF])\s")  # `-v threadtime` lines


def _filter_logcat(text: str) -> list[str]:
    """Error/fatal lines and known crash signatures from `logcat -v threadtime` output, last
    LOGCAT_TAIL_LINES of them."""
    kept = []
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


def _write_pair(directory: Path, stem: str, d: u2.Device, xml: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{stem}_hierarchy.xml").write_text(xml)
    cast(Image.Image, d.screenshot()).convert("RGB").save(directory / f"{stem}_screen.jpg", quality=70)


def dump_debug(d: u2.Device, name: str, xml: str | None = None) -> None:
    """Save a hierarchy + screenshot pair for later inspection. Pass `xml` when the caller
    already has a fresh dump, to avoid a redundant device round-trip. Best-effort: a debug dump is
    a diagnostic aid, not part of the scrape itself, so a write failure here (e.g. a stale file
    left owned by a different uid from a `docker exec -u root` session) must not crash the whole
    run — it just means this one dump is missing from $DEBUG_DIR. In profile capture mode the pair
    is also kept in PROFILE_CAPTURE_DIR, since these are exactly the screens a new profile breaks on."""
    try:
        xml = xml if xml is not None else d.dump_hierarchy()
        _write_pair(config.DEBUG_DIR, name, d, xml)
        prune_debug_dumps()
    except OSError as e:
        log(f"WARN: could not write debug dump {name!r}:", repr(e))
        return
    capture_screen(d, screen_of_dump(name) or name, xml, failure=True)


def capture_screen(d: u2.Device, screen: str, xml: str | None = None, failure: bool = False) -> None:
    """Profile capture mode (PROFILE_CAPTURE_DIR set, see scripts/new_profile.py): save this screen
    as `<seq>-<screen>[-fail]_hierarchy.xml` + `_screen.jpg`, up to CAPTURE_PER_SCREEN per screen
    (failure dumps always), so a baseline run under a new profile leaves one reviewable dump of
    every screen it reached. A no-op otherwise, and never raises: capturing must not change a run."""
    if not config.PROFILE_CAPTURE_DIR:
        return
    count = _captured.get(screen, 0)
    if not failure and count >= config.CAPTURE_PER_SCREEN:
        return
    _captured[screen] = count + 1
    seq = sum(_captured.values())
    stem = f"{seq:03d}-{screen}" + ("-fail" if failure else "")
    try:
        _write_pair(Path(config.PROFILE_CAPTURE_DIR), stem, d, xml if xml is not None else d.dump_hierarchy())
    except Exception as e:  # a device hiccup or a full disk; the run itself goes on
        log(f"WARN: could not capture screen {stem!r}:", repr(e))
