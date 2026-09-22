"""Capturing a post card: expanded caption, permalink via the share sheet, media crops,
carousel slides, avatars."""

import re
import time
from collections.abc import Mapping
from pathlib import Path

from lxml import etree
from PIL import Image

from . import common, config, device, diagnostics, navigation, parsing, uidevice
from .common import log
from .versioning import SELECTORS, versioned


@versioned
def expand_caption(d: uidevice.Device, p: parsing.Post) -> str:
    """Tap a truncated caption's "... more" to expand it in place, and return the full text.
    "more" is a clickable span at the end of the caption's last line, not a separate touch
    target of its own, so the tap aims at the widget's bottom-right corner rather than its
    center. Never raises and never drops the card: any tap/dump/match failure just falls back
    to the already-truncated caption, and a tap that lands on a different screen is recovered
    with navigation.back_to_feed() before returning."""
    if not p["caption_truncated"] or not p["caption_bounds"]:
        return p["caption"]
    prefix: str = p["caption"].rstrip("…").strip()
    if not prefix:  # nothing distinctive to match the re-read node against; not worth the risk
        return p["caption"]
    point: tuple[int, int] | None = common.bounds_bottom_right(p["caption_bounds"])
    if not point:
        return p["caption"]
    for _ in range(config.CAPTION_EXPAND_TRIES):
        try:
            d.click(*point)
            device.human_pause(0.4, 0.9)
            xml: str = d.dump_hierarchy()
        except Exception as e:
            log(f"WARN: caption expand tap failed for {p['username']}:", repr(e))
            break
        root: etree._Element = etree.fromstring(xml.encode())
        n: etree._Element
        for n in root.iter("node"):
            if n.get("class") != SELECTORS["caption_class"]:
                continue
            cleaned: str = parsing.clean_caption(n.get("text") or "", p["username"])
            if cleaned.startswith(prefix) and cleaned != p["caption"]:
                return cleaned
    if not navigation.on_feed(d):
        log(f"WARN: caption expand left the feed for {p['username']}; recovering")
        navigation.back_to_feed(d)
    return p["caption"]


_last_code: str = ""  # the shortcode of the last permalink handed out, to spot a clipboard that didn't change
# The dumpsys notes ("a clip but no link", "failed") are logged once per run each.
_dumpsys_noted: bool = False
_dumpsys_failed: bool = False


def reset_last_url(d: uidevice.Device) -> None:
    """Start a run treating whatever is on the clipboard now as stale, whichever way it's read."""
    global _last_code, _dumpsys_noted, _dumpsys_failed
    _dumpsys_noted = _dumpsys_failed = False
    current: str = ""
    try:
        current = d.clipboard or ""
    except Exception:
        current = ""
    _last_code = _code_of(current) or _code_of(_clipboard_via_dumpsys(d, 5.0))


def _code_of(text: str) -> str:
    """The shortcode in a clipboard string, or "": what two reads of the same link have in common
    whether one came back with tracking parameters and a trailing slash and the other trimmed."""
    m: re.Match[str] | None = SELECTORS["permalink"].match(text)
    return m.group("code") if m else ""


def _clipboard_via_dumpsys(d: uidevice.Device, timeout: float) -> str:
    """The permalink in the system clipboard as `dumpsys clipboard` (root, over adb) prints it, or "".

    A second opinion for the uiautomator2 read, which comes from its own instrumentation process:
    Android 10+ only lets the focused app or the default IME read the clipboard, and that read came
    back empty on 6 of 8 Copy link taps in the 445 baseline (docs/RUNLOG.md). This image's adbd is
    root, and the service's dump prints the primary clip; whether this Android redacts the text in
    that dump is what the next real run tells us, so a clip without a link is logged once."""
    global _dumpsys_noted, _dumpsys_failed
    try:
        out: str = d.shell(["dumpsys", "clipboard"], timeout=max(0.5, timeout)).output or ""
    except Exception as e:
        if not _dumpsys_failed:
            _dumpsys_failed = True
            log("WARN: dumpsys clipboard failed (not retried this run):", repr(e))
        return ""
    m: re.Match[str] | None = SELECTORS["permalink"].search(out)
    if m:
        return m.group(0)
    if "ClipData" in out and not _dumpsys_noted:
        _dumpsys_noted = True
        first: str = next((line.strip() for line in out.splitlines() if "ClipData" in line), "")
        log(f"dumpsys clipboard shows a clip but no permalink (redacted?): {first[:120]!r}")
    return ""


@versioned
def fetch_permalink(d: uidevice.Device, post_hash: str) -> tuple[str | None, str | None]:
    """Open the share sheet for the post with this hash, pick 'Copy link', read the clipboard.

    Re-dumps the hierarchy right before tapping and clicks the share *element* (not stale
    coordinates) because the feed can shift a few hundred px between a dump and a tap.
    Returns (url, None) on success, or (None, reason) where reason is "sheet" (the share sheet
    never opened, or the card/button vanished before it could) or "clipboard" (the sheet opened
    and Copy link was tapped, but the clipboard never carried a fresh permalink) — the two need
    different recoveries, so the caller counts them separately."""
    if not navigation.close_sheets(d):
        log("WARN: a sheet is stuck open; skipping permalink")
        return None, "sheet"
    fresh: parsing.Post | None = next(
        (p for p in parsing.parse_hierarchy(d.dump_hierarchy()) if parsing.post_id(p) == post_hash), None
    )
    point: tuple[int, int] | None = common.bounds_center(fresh["share_bounds"]) if fresh else None
    if not point:
        log("WARN: card moved before the share tap; no permalink")
        return None, "sheet"
    link: uidevice.Selector = d(description=SELECTORS["copy_link_desc"])
    _tap: int
    for _tap in range(config.SHARE_TAP_TRIES):  # the first tap is occasionally swallowed by the video overlay
        # Coordinate tap from the fresh dump: element-based clicks on this (non-clickable)
        # ViewGroup are unreliable on video cards.
        d.click(*point)
        device.human_pause(2, 3)
        if link.exists(timeout=10):
            break
    if not link.exists(timeout=1):
        log("WARN: no share sheet with 'Copy link'; dump saved")
        diagnostics.dump_debug(d, "share_sheet")
        navigation.close_sheets(d)
        navigation.back_to_feed(d)
        return None, "sheet"
    diagnostics.capture_screen(d, "share_sheet")
    # Note: clearing the clipboard first (d.set_clipboard) makes the next read come back empty.
    # Staleness is caught below by comparing with the last link we handed out.
    device.human_pause(0.8, 1.2)  # let the sheet finish animating
    try:
        b: Mapping[str, int] = link.info.get("bounds") or {}
        cx: int
        cy: int
        cx, cy = (b["left"] + b["right"]) // 2, (b["top"] + b["bottom"]) // 2
    except Exception as e:  # the sheet re-rendered and the node vanished
        log("WARN: Copy link vanished before click:", repr(e))
        navigation.close_sheets(d)
        navigation.back_to_feed(d)
        return None, "sheet"
    d.click(cx, cy)
    # Poll instead of a single fixed-delay read: the clipboard write can lag the tap by more
    # than a beat, and the old one-shot read missed it more often than not.
    global _last_code
    url: str = ""
    deadline: float = time.time() + config.CLIPBOARD_TIMEOUT
    while time.time() < deadline:
        time.sleep(0.4)
        source: str = "uiautomator2"
        try:
            candidate: str = d.clipboard or ""
        except Exception as e:
            log("WARN: clipboard read failed:", repr(e))
            candidate = ""
        code: str = _code_of(candidate)
        if not code or code == _last_code:
            if _dumpsys_failed:
                continue
            candidate, source = _clipboard_via_dumpsys(d, deadline - time.time()), "dumpsys"
            code = _code_of(candidate)
        if code and code != _last_code:
            url, _last_code = candidate, code
            if source == "dumpsys":
                log("permalink read via dumpsys clipboard (uiautomator2's read was empty or stale)")
            break
    navigation.close_sheets(d)  # sheet usually closes itself after Copy link; make sure
    navigation.back_to_feed(d)
    m: re.Match[str] | None = SELECTORS["permalink"].match(url)
    if not m:
        log("WARN: clipboard did not contain a permalink:", repr(url[:80]))
        return None, "clipboard"
    return f"https://www.instagram.com/{m.group('type')}/{m.group('code')}/", None


def permalink_code(url: str) -> str:
    """The shortcode of a permalink fetch_permalink() returned: the id the post is stored under."""
    m: re.Match[str] | None = SELECTORS["permalink"].match(url)
    return m.group("code") if m else url


def media_ext() -> str:
    return config.MEDIA_FORMATS[config.MEDIA_FORMAT][0]


def save_media(img: Image.Image, path: Path) -> None:
    """Encode `img` to `path` in MEDIA_FORMAT at MEDIA_QUALITY (the caller picks the extension via
    media_ext())."""
    img.convert("RGB").save(path, config.MEDIA_FORMATS[config.MEDIA_FORMAT][1], quality=config.MEDIA_QUALITY)


@versioned
def crop_media(
    d: uidevice.Device, bounds: str | None, pid: str, clip_top: int = 0, settle: float = 0
) -> str | None:
    """Screenshot the visible post image and save it as "{pid}.webp" (or .jpg, see MEDIA_FORMAT).
    Returns filename or None.
    `settle` delays the shot (e.g. for a video/Reel, so autoplay has started and the initial
    audio-label overlay has faded) before it's taken — still one image, just a better-timed one."""
    b: tuple[int, int, int, int] | None
    if not (b := common.parse_bounds(bounds)):
        return None
    x1: int
    y1: int
    x2: int
    y2: int
    x1, y1, x2, y2 = b
    _: int
    h: int
    _, h = device.window_size(d)
    full: int = y2 - y1
    y1, y2 = max(y1, clip_top), min(y2, h)  # trim the floating action bar / screen edge
    if full < 200 or (y2 - y1) < 0.4 * full:
        log(f"no crop: media bounds {bounds} mostly off-screen")
        return None
    if settle:
        device.human_pause(settle, settle * 1.4)
    img: Image.Image = d.screenshot()
    config.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    fn: str = f"{pid}{media_ext()}"
    save_media(img.crop((x1, y1, x2, y2)), config.MEDIA_DIR / fn)
    return fn


@versioned
def capture_carousel(d: uidevice.Device, p: parsing.Post, pid: str) -> list[str]:
    """Swipe through a carousel's remaining slides in place and crop each one. `pid` already has
    slide 1 captured by the caller via crop_media(); this walks slides 2..total (capped at
    MAX_CAROUSEL_SLIDES), stopping as soon as a swipe doesn't land on the next slide (the swipe was
    read as a vertical scroll instead, or Instagram simply has nothing further) rather than risk
    storing the same slide twice. Returns the extra slides' filenames, in order."""
    total: int = min(parsing.carousel_count(p["alt"]), config.MAX_CAROUSEL_SLIDES)
    b: tuple[int, int, int, int] | None = common.parse_bounds(p["bounds"])
    if total < 2 or not b:
        return []
    x1: int
    y1: int
    x2: int
    y2: int
    x1, y1, x2, y2 = b
    cy: int = (y1 + y2) // 2
    inset: int = max(int((x2 - x1) * 0.1), 1)
    key: str = parsing.post_id(p)
    files: list[str] = []
    slide: int
    for slide in range(2, total + 1):
        d.swipe(x2 - inset, cy, x1 + inset, cy, duration=device.swipe_duration())
        device.human_pause(0.8, 1.6)
        fresh: parsing.Post | None = next(
            (c for c in parsing.parse_hierarchy(d.dump_hierarchy()) if parsing.post_id(c) == key), None
        )
        sm: re.Match[str] | None = SELECTORS["slide_index"].match(fresh["alt"]) if fresh else None
        if not fresh or not sm or int(sm.group(1)) != slide:
            log(f"carousel: swipe didn't land on slide {slide}; stopping with {len(files)} extra")
            break
        fn: str | None = crop_media(
            d, fresh["bounds"] or p["bounds"], f"{pid}_{slide - 1}", p.get("clip_top", 0)
        )
        if not fn:
            break
        files.append(fn)
    return files


@versioned
def _avatar_bounds(header_bounds: str) -> tuple[int, int, int, int] | None:
    """The header's own bounds crop to a leading square avatar with a small inset — the avatar
    ImageView has no addressable node (row_feed_profile_header is a collapsed leaf in the
    accessibility tree), so this crops positionally rather than by resource-id. Needs a live check
    against a real device to confirm the inset actually lands on the avatar."""
    b: tuple[int, int, int, int] | None
    if not (b := common.parse_bounds(header_bounds)):
        return None
    x1: int
    y1: int
    x2: int
    y2: int
    x1, y1, x2, y2 = b
    size: int = y2 - y1
    pad: int = max(size // 8, 1)
    return x1 + pad, y1 + pad, x1 + pad + (size - 2 * pad), y2 - pad


@versioned
def capture_avatar(d: uidevice.Device, header_bounds: str, username: str) -> str | None:
    """Crop the account's avatar from its own feed header and save it once per account, in its own
    subdirectory so the retention orphan sweep (which only globs MEDIA_DIR's top level) never
    touches it. Returns the path relative to MEDIA_DIR, or None."""
    box: tuple[int, int, int, int] | None = _avatar_bounds(header_bounds)
    safe_user: str | None = common.safe_filename(username)
    if not box or not safe_user:
        return None
    avatar_dir: Path = config.MEDIA_DIR / "avatars"
    avatar_dir.mkdir(parents=True, exist_ok=True)
    img: Image.Image = d.screenshot()
    fn: str = f"{safe_user}{media_ext()}"
    save_media(img.crop(box), avatar_dir / fn)
    # The orphan sweep never looks in avatars/, so drop this account's avatar in any other format
    # here (e.g. its old .jpg after switching MEDIA_FORMAT) rather than leaving it behind forever.
    ext: str
    for ext in config.MEDIA_EXTS:
        if ext != media_ext():
            (avatar_dir / f"{safe_user}{ext}").unlink(missing_ok=True)
    return f"avatars/{fn}"
