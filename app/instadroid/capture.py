"""Capturing a post card: expanded caption, permalink via the share sheet, media crops,
carousel slides, avatars."""

import time
from pathlib import Path
from typing import cast

import uiautomator2 as u2
from lxml import etree
from PIL import Image

from . import common, config, device, diagnostics, navigation, parsing
from .common import log
from .versioning import SELECTORS, versioned


@versioned
def expand_caption(d: u2.Device, p: parsing.Post) -> str:
    """Tap a truncated caption's "... more" to expand it in place, and return the full text.
    "more" is a clickable span at the end of the caption's last line, not a separate touch
    target of its own, so the tap aims at the widget's bottom-right corner rather than its
    center. Never raises and never drops the card: any tap/dump/match failure just falls back
    to the already-truncated caption, and a tap that lands on a different screen is recovered
    with navigation.back_to_feed() before returning."""
    if not p["caption_truncated"] or not p["caption_bounds"]:
        return p["caption"]
    prefix = p["caption"].rstrip("…").strip()
    if not prefix:  # nothing distinctive to match the re-read node against; not worth the risk
        return p["caption"]
    point = common.bounds_bottom_right(p["caption_bounds"])
    if not point:
        return p["caption"]
    for _ in range(config.CAPTION_EXPAND_TRIES):
        try:
            d.click(*point)
            device.human_pause(0.4, 0.9)
            xml = d.dump_hierarchy()
        except Exception as e:
            log(f"WARN: caption expand tap failed for {p['username']}:", repr(e))
            break
        root = etree.fromstring(xml.encode())
        for n in root.iter("node"):
            if n.get("class") != SELECTORS["caption_class"]:
                continue
            cleaned = parsing.clean_caption(n.get("text") or "", p["username"])
            if cleaned.startswith(prefix) and cleaned != p["caption"]:
                return cleaned
    if not navigation.on_feed(d):
        log(f"WARN: caption expand left the feed for {p['username']}; recovering")
        navigation.back_to_feed(d)
    return p["caption"]


_last_url = ""  # the last permalink handed out, to spot a clipboard that didn't change


def reset_last_url(d: u2.Device) -> None:
    """Start a run treating whatever is on the clipboard now as stale."""
    global _last_url
    try:
        _last_url = d.clipboard or ""
    except Exception:
        _last_url = ""


@versioned
def fetch_permalink(d: u2.Device, post_hash: str) -> tuple[str | None, str | None]:
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
    fresh = next(
        (p for p in parsing.parse_hierarchy(d.dump_hierarchy()) if parsing.post_id(p) == post_hash), None
    )
    point = common.bounds_center(fresh["share_bounds"]) if fresh else None
    if not point:
        log("WARN: card moved before the share tap; no permalink")
        return None, "sheet"
    link = d(description=SELECTORS["copy_link_desc"])
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
    # Note: clearing the clipboard first (d.set_clipboard) makes the next read come back empty.
    # Staleness is caught below by comparing with the last link we handed out.
    device.human_pause(0.8, 1.2)  # let the sheet finish animating
    try:
        b = link.info.get("bounds") or {}
        cx, cy = (b["left"] + b["right"]) // 2, (b["top"] + b["bottom"]) // 2
    except Exception as e:  # the sheet re-rendered and the node vanished
        log("WARN: Copy link vanished before click:", repr(e))
        navigation.close_sheets(d)
        navigation.back_to_feed(d)
        return None, "sheet"
    d.click(cx, cy)
    # Poll instead of a single fixed-delay read: the clipboard write can lag the tap by more
    # than a beat, and the old one-shot read missed it more often than not.
    global _last_url
    url = ""
    deadline = time.time() + config.CLIPBOARD_TIMEOUT
    while time.time() < deadline:
        time.sleep(0.4)
        try:
            candidate = d.clipboard or ""
        except Exception as e:
            log("WARN: clipboard read failed:", repr(e))
            continue
        if candidate and candidate != _last_url and SELECTORS["permalink"].match(candidate):
            url = _last_url = candidate
            break
    navigation.close_sheets(d)  # sheet usually closes itself after Copy link; make sure
    navigation.back_to_feed(d)
    m = SELECTORS["permalink"].match(url)
    if not m:
        log("WARN: clipboard did not contain a permalink:", repr(url[:80]))
        return None, "clipboard"
    return f"https://www.instagram.com/{m.group('type')}/{m.group('code')}/", None


def media_ext() -> str:
    return ".webp" if config.MEDIA_FORMAT == "webp" else ".jpg"


def save_media(img: Image.Image, path: Path) -> None:
    """Encode `img` to `path` in MEDIA_FORMAT at MEDIA_QUALITY (the caller picks the extension via
    media_ext())."""
    img = img.convert("RGB")
    if config.MEDIA_FORMAT == "webp":
        img.save(path, "WEBP", quality=config.MEDIA_QUALITY)
    else:
        img.save(path, "JPEG", quality=config.MEDIA_QUALITY)


@versioned
def crop_media(
    d: u2.Device, bounds: str | None, pid: str, clip_top: int = 0, settle: float = 0
) -> str | None:
    """Screenshot the visible post image and save it as "{pid}.webp" (or .jpg, see MEDIA_FORMAT).
    Returns filename or None.
    `settle` delays the shot (e.g. for a video/Reel, so autoplay has started and the initial
    audio-label overlay has faded) before it's taken — still one image, just a better-timed one."""
    if not (b := common.parse_bounds(bounds)):
        return None
    x1, y1, x2, y2 = b
    w, h = d.window_size()
    full = y2 - y1
    y1, y2 = max(y1, clip_top), min(y2, h)  # trim the floating action bar / screen edge
    if full < 200 or (y2 - y1) < 0.4 * full:
        log(f"no crop: media bounds {bounds} mostly off-screen")
        return None
    if settle:
        device.human_pause(settle, settle * 1.4)
    img = cast(Image.Image, d.screenshot())
    config.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    fn = f"{pid}{media_ext()}"
    save_media(img.crop((x1, y1, x2, y2)), config.MEDIA_DIR / fn)
    return fn


@versioned
def capture_carousel(d: u2.Device, p: parsing.Post, pid: str) -> list[str]:
    """Swipe through a carousel's remaining slides in place and crop each one. `pid` already has
    slide 1 captured by the caller via crop_media(); this walks slides 2..total (capped at
    MAX_CAROUSEL_SLIDES), stopping as soon as a swipe doesn't land on the next slide (the swipe was
    read as a vertical scroll instead, or Instagram simply has nothing further) rather than risk
    storing the same slide twice. Returns the extra slides' filenames, in order."""
    total = min(parsing.carousel_count(p["alt"]), config.MAX_CAROUSEL_SLIDES)
    b = common.parse_bounds(p["bounds"])
    if total < 2 or not b:
        return []
    x1, y1, x2, y2 = b
    cy = (y1 + y2) // 2
    inset = max(int((x2 - x1) * 0.1), 1)
    key = parsing.post_id(p)
    files: list[str] = []
    for slide in range(2, total + 1):
        d.swipe(x2 - inset, cy, x1 + inset, cy, duration=device.swipe_duration())
        device.human_pause(0.8, 1.6)
        fresh = next(
            (c for c in parsing.parse_hierarchy(d.dump_hierarchy()) if parsing.post_id(c) == key), None
        )
        sm = SELECTORS["slide_index"].match(fresh["alt"]) if fresh else None
        if not fresh or not sm or int(sm.group(1)) != slide:
            log(f"carousel: swipe didn't land on slide {slide}; stopping with {len(files)} extra")
            break
        fn = crop_media(d, fresh["bounds"] or p["bounds"], f"{pid}_{slide - 1}", p.get("clip_top", 0))
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
    if not (b := common.parse_bounds(header_bounds)):
        return None
    x1, y1, x2, y2 = b
    size = y2 - y1
    pad = max(size // 8, 1)
    return x1 + pad, y1 + pad, x1 + pad + (size - 2 * pad), y2 - pad


@versioned
def capture_avatar(d: u2.Device, header_bounds: str, username: str) -> str | None:
    """Crop the account's avatar from its own feed header and save it once per account, in its own
    subdirectory so the retention orphan sweep (which only globs MEDIA_DIR's top level) never
    touches it. Returns the path relative to MEDIA_DIR, or None."""
    box = _avatar_bounds(header_bounds)
    safe_user = common.safe_filename(username)
    if not box or not safe_user:
        return None
    avatar_dir = config.MEDIA_DIR / "avatars"
    avatar_dir.mkdir(parents=True, exist_ok=True)
    img = cast(Image.Image, d.screenshot())
    fn = f"{safe_user}{media_ext()}"
    save_media(img.crop(box), avatar_dir / fn)
    # The orphan sweep never looks in avatars/, so drop this account's avatar in any other format
    # here (e.g. its old .jpg after switching MEDIA_FORMAT) rather than leaving it behind forever.
    for ext in config.MEDIA_EXTS:
        if ext != media_ext():
            (avatar_dir / f"{safe_user}{ext}").unlink(missing_ok=True)
    return f"avatars/{fn}"
