"""Capturing stories from the Home feed's tray, with perceptual-hash dedupe."""

import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol, TypedDict

from lxml import etree
from PIL import Image, ImageStat

from . import capture, common, config, device, diagnostics, navigation, parsing, uidevice
from .common import log
from .versioning import SELECTORS, versioned


class CapturedStory(TypedDict):
    username: str
    posted_date: str
    path: Path  # the saved crop, under a temporary name until it's stored


@versioned
def capture_story_media(
    img: Image.Image, media_bounds: str | None, clip_top: int, tmp_name: str
) -> Path | None:
    """Crop a story's current frame — from a screenshot already taken by the caller, not one taken
    here — into MEDIA_DIR/stories. clip_top skips the username/timestamp header overlay so the
    saved image doesn't bake in text that changes hour to hour (that text would otherwise make the
    same still-active story hash differently across runs — see scrape_stories())."""
    if not (b := common.parse_bounds(media_bounds)):
        return None
    x1, y1, x2, y2 = b
    y1 = max(y1, clip_top)
    if (y2 - y1) < 200:
        return None
    stories_dir = config.MEDIA_DIR / "stories"
    stories_dir.mkdir(parents=True, exist_ok=True)
    path = stories_dir / f"{tmp_name}{capture.media_ext()}"
    capture.save_media(img.crop((x1, y1, x2, y2)), path)
    return path


@versioned
def capture_story(d: uidevice.Device, item: parsing.StoryItem) -> CapturedStory | None:
    """Open one tray item's story, capture its current frame, and always exit back to the Home
    feed via Back. Never tap forward inside the viewer (past this one open tap): the message/like/
    reshare/profile-picture/menu targets are all real actions on someone else's story, and on this
    host, tapping to advance past a story's last frame has been observed to eject the app to the OS
    launcher entirely — unlike Back, which reliably returns to the tray.

    A single-frame story can also auto-advance (and eject the app the same way) on its own, within
    a few seconds of opening, whether or not we ever tap — an earlier version of this function took
    the screenshot after a hierarchy-walk following a multi-second polling loop, and was seen to
    capture the *launcher's* wallpaper (or a black transition frame) instead of the story, having
    fallen behind the story's own display timer. So every device round-trip here is on the clock:
    a single cheap exists() (not a repeated dump_hierarchy()) confirms the viewer opened, then the
    screenshot is taken and Back is pressed immediately — cropping and saving happen afterward,
    off-device, where they can't race anything.

    Returns {username, posted_date, path} or None if the story didn't open, closed before the
    screenshot, or nothing could be cropped."""
    point = common.bounds_center(item["bounds"])
    if not point:
        return None
    d.click(*point)
    if not d(resourceIdMatches=f".*:id/{SELECTORS['story_viewer_id']}").exists(timeout=5):
        log(f"WARN: story for {item['username']} didn't open; skipping")
        d.press("back")
        device.human_pause(1, 1.5)
        return None
    xml = d.dump_hierarchy()
    if SELECTORS["story_viewer_id"] not in xml:  # exists() can win a race against a fast auto-exit
        log(f"WARN: story for {item['username']} closed before it could be read; dump saved")
        diagnostics.dump_debug(d, f"story_{common.safe_filename(item['username']) or 'unknown'}", xml=xml)
        d.press("back")
        device.human_pause(1, 1.5)
        return None
    diagnostics.capture_screen(d, "story_viewer", xml)
    img = d.screenshot()
    d.press("back")  # off the device from here on; cropping/saving below never risks the timer
    device.human_pause(1, 1.5)
    root = etree.fromstring(xml.encode())
    media_bounds = clip_top = None
    posted_date = ""
    for n in root.iter("node"):
        rid = (n.get("resource-id") or "").split("/")[-1]
        if rid == SELECTORS["story_media_id"] and media_bounds is None:
            media_bounds = n.get("bounds")
        elif rid == SELECTORS["story_shadow_id"] and clip_top is None:
            if b := common.parse_bounds(n.get("bounds")):
                clip_top = b[3]
        elif rid == SELECTORS["story_timestamp_id"] and not posted_date:
            posted_date = n.get("text") or ""
    path = capture_story_media(
        img, media_bounds, clip_top or 0, f"tmp_{item['username']}_{int(time.time() * 1000)}"
    )
    return {"username": item["username"], "posted_date": posted_date, "path": path} if path else None


@versioned
def scrape_stories(d: uidevice.Device, con: sqlite3.Connection) -> int:
    """Visit each not-yet-seen account's story from the Home feed's tray, capture its current
    frame, and return to the Following feed afterward. Stories have no stable public id the way
    posts do (no permalink/shortcode), so a capture is checked against the DB only afterwards: a
    blank frame, or one that looks like a story the account posted in the last day
    (_find_story_duplicate()), is discarded."""
    for _ in range(3):
        if navigation.on_home_feed(d):
            break
        d.press("back")
        device.human_pause(1, 1.5)
    else:
        log("WARN: could not reach the Home feed for stories; skipping this run")
        diagnostics.capture_screen(d, "home_feed", failure=True)
        return 0
    tray_xml = d.dump_hierarchy()
    diagnostics.capture_screen(d, "home_feed", tray_xml)
    items = [i for i in parsing.parse_story_tray(tray_xml) if not i["seen"]]
    new = 0
    for item in items[: config.MAX_STORIES_PER_RUN]:
        if not navigation.on_home_feed(d):
            # A prior story's own auto-exit can eject the app entirely (see capture_story()); tapping
            # this item's now-stale tray coordinates against whatever's currently on screen would be
            # a shot in the dark, so recover onto Home first.
            log("WARN: not on the Home feed any more; recovering before the next story")
            for _ in range(3):
                if navigation.on_home_feed(d):
                    break
                device.launch_app(d)
                device.human_pause(2, 3)
            else:
                log("WARN: could not recover the Home feed; stopping story capture for this run")
                break
        captured = capture_story(d, item)
        if not captured:
            continue
        path, username = captured["path"], captured["username"]
        with Image.open(path) as img:
            blank = _is_blank_frame(img)
            phash = _dhash(img)
        if blank or _find_story_duplicate(con, username, phash):
            log(f"story for {username}: {'blank frame' if blank else 'already stored'}; discarding")
            path.unlink(missing_ok=True)
            continue
        digest = common.digest(path.read_bytes())
        cur = con.execute(
            "INSERT OR IGNORE INTO stories (id, username, media_file, kind, posted_date, scraped_at, phash)"
            " VALUES (?,?,?,?,?,?,?)",
            (
                digest,
                username,
                f"stories/{digest}{path.suffix}",
                "story",
                captured["posted_date"],
                datetime.now(UTC).isoformat(),
                phash,
            ),
        )
        con.commit()
        if cur.rowcount:
            path.rename(config.MEDIA_DIR / "stories" / f"{digest}{path.suffix}")
            new += 1
            log(f"new story: {username}")
        else:
            path.unlink(missing_ok=True)  # byte-identical to one already stored
    navigation.open_target_feed(d)
    return new


# Max differing bits (of 64) for two story crops to count as the same frame. Re-captures of one
# frame differ only by screenshot noise and overlays; distinct stories stored on 2026-09-14 were
# 16-45 bits apart.
STORY_PHASH_DISTANCE = 10


class _Resizable(Protocol):
    """Image.resize() as _dhash() calls it. Pillow annotates `size` as also accepting a numpy array,
    a type left unknown when numpy isn't installed, so the method is read through this instead."""

    def resize(self, size: tuple[int, int]) -> Image.Image: ...


def _resized(img: _Resizable, size: tuple[int, int]) -> Image.Image:
    return img.resize(size)


def _dhash(img: Image.Image) -> str:
    """64-bit difference hash: survives re-encoding and small overlays, unlike a byte hash."""
    px = _resized(img.convert("L"), (9, 8)).tobytes()
    bits = 0
    for i in range(72):
        if i % 9 != 8:
            bits = bits << 1 | (px[i] > px[i + 1])
    return f"{bits:016x}"


def _is_blank_frame(img: Image.Image) -> bool:
    """A near-black crop: the viewer's loading/transition frame, not the story itself."""
    stat = ImageStat.Stat(img.convert("L"))
    return stat.mean[0] < 8 and stat.stddev[0] < 4


def _find_story_duplicate(con: sqlite3.Connection, username: str, phash: str) -> bool:
    """True if this account has a story stored in the last day (a story's lifetime) that looks the
    same. The byte hash alone missed these: every capture re-encodes a fresh screenshot."""
    cutoff = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    rows = common.fetch_all(
        con.execute(
            "SELECT phash FROM stories WHERE username=? AND scraped_at > ? AND phash IS NOT NULL",
            (username, cutoff),
        )
    )
    return any(
        (int(common.must_str(r, 0), 16) ^ int(phash, 16)).bit_count() <= STORY_PHASH_DISTANCE for r in rows
    )
