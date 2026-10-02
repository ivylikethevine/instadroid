"""Capturing stories from the Home feed's tray, with perceptual-hash dedupe."""

import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol, TypedDict

from igprofiles.screens import id_matches
from lxml import etree
from PIL import Image, ImageFile, ImageStat
from shared import sqlrows

from . import capture, common, config, device, diagnostics, navigation, parsing, uidevice
from .common import log
from .versioning import SELECTORS, versioned


class CapturedStory(TypedDict):
    username: str
    posted_date: str
    path: Path  # the saved crop, under a temporary name until it's stored
    body_top: int  # rows of the crop above its body, the part a story is identified by


@versioned
def capture_story_media(
    img: Image.Image, media_bounds: str | None, clip_top: int, tmp_name: str
) -> tuple[Path, int] | None:
    """Crop a story's whole current frame — from a screenshot already taken by the caller, not one
    taken here — into MEDIA_DIR/stories. Returns (path, the crop's rows above clip_top): the
    username/timestamp header overlays those rows with text that changes hour to hour, so a story is
    hashed on the body below them (see scrape_stories()), not on the whole crop."""
    b: tuple[int, int, int, int] | None
    if not (b := common.parse_bounds(media_bounds)):
        return None
    x1: int
    y1: int
    x2: int
    y2: int
    x1, y1, x2, y2 = b
    body_top: int = max(clip_top - y1, 0)
    if (y2 - y1 - body_top) < 200:
        return None
    stories_dir: Path = config.MEDIA_DIR / "stories"
    stories_dir.mkdir(parents=True, exist_ok=True)
    path: Path = stories_dir / f"{tmp_name}{capture.media_ext()}"
    capture.save_media(img.crop((x1, y1, x2, y2)), path)
    return path, body_top


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
    point: tuple[int, int] | None = common.bounds_center(item["bounds"])
    if not point:
        return None
    d.click(*point)
    if not d(resourceIdMatches=f".*:id/{SELECTORS['story_viewer_id']}").exists(timeout=5):
        log(f"WARN: story for {item['username']} didn't open; skipping")
        d.press("back")
        device.human_pause(1, 1.5)
        return None
    xml: str = d.dump_hierarchy()
    nodes: list[tuple[str, etree._Element]] = [
        (n.get("resource-id") or "", n) for n in etree.fromstring(xml.encode()).iter("node")
    ]
    # exists() can win a race against a fast auto-exit. Parsing here is milliseconds, off the device.
    if not any(id_matches(rid, SELECTORS["story_viewer_id"]) for rid, _ in nodes):
        log(f"WARN: story for {item['username']} closed before it could be read; dump saved")
        diagnostics.dump_debug(d, f"story_{common.safe_filename(item['username']) or 'unknown'}", xml=xml)
        d.press("back")
        device.human_pause(1, 1.5)
        return None
    img: Image.Image = d.screenshot()
    if _brightness(img) < DARK_FRAME_MEAN:  # possibly a video story's fade-in: look once more
        retake: Image.Image = d.screenshot()
        if _brightness(retake) > _brightness(img):
            img = retake
    diagnostics.capture_screen(d, "story_viewer", xml, image=img)
    d.press("back")  # off the device from here on; cropping/saving below never risks the timer
    device.human_pause(1, 1.5)
    media_bounds: str | None
    clip_top: int | None
    media_bounds = clip_top = None
    posted_date: str = ""
    rid: str
    n: etree._Element
    for rid, n in nodes:
        if id_matches(rid, SELECTORS["story_media_id"]) and media_bounds is None:
            media_bounds = n.get("bounds")
        elif id_matches(rid, SELECTORS["story_shadow_id"]) and clip_top is None:
            b: tuple[int, int, int, int] | None
            if b := common.parse_bounds(n.get("bounds")):
                clip_top = b[3]
        elif id_matches(rid, SELECTORS["story_timestamp_id"]) and not posted_date:
            posted_date = n.get("text") or ""
    saved: tuple[Path, int] | None = capture_story_media(
        img, media_bounds, clip_top or 0, f"tmp_{item['username']}_{int(time.time() * 1000)}"
    )
    if not saved:
        return None
    return {"username": item["username"], "posted_date": posted_date, "path": saved[0], "body_top": saved[1]}


@versioned
def scrape_stories(d: uidevice.Device, con: sqlite3.Connection) -> int:
    """Visit each not-yet-seen account's story from the Home feed's tray, capture its current
    frame, and return to the feed FEED_MODE scrapes (navigation.open_target_feed()) afterward. Stories
    have no stable public id the way posts do (no permalink/shortcode), so a capture is checked against
    the DB only afterwards: a blank frame, or one that looks like a story stored in the last day
    (_find_story_duplicate()), is discarded. That check, and the id, read the crop's body, below the
    header overlay."""
    for _ in range(3):
        if navigation.on_home_feed(d):
            break
        d.press("back")
        device.human_pause(1, 1.5)
    else:
        log("WARN: could not reach the Home feed for stories; skipping this run")
        diagnostics.capture_screen(d, "home_feed", failure=True)
        return 0
    tray_xml: str = d.dump_hierarchy()
    diagnostics.capture_screen(d, "home_feed", tray_xml)
    items: list[parsing.StoryItem] = [i for i in parsing.parse_story_tray(tray_xml) if not i["seen"]]
    new: int = 0
    item: parsing.StoryItem
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
        captured: CapturedStory | None = capture_story(d, item)
        if not captured:
            continue
        path: Path
        username: str
        path, username = captured["path"], captured["username"]
        img: ImageFile.ImageFile
        with Image.open(path) as img:
            body: Image.Image = img.crop((0, captured["body_top"], img.width, img.height)).convert("RGB")
        blank: bool = _is_blank_frame(body)
        phash: str = _dhash(body)
        if blank or _find_story_duplicate(con, username, phash):
            log(f"story for {username}: {'blank frame' if blank else 'already stored'}; discarding")
            path.unlink(missing_ok=True)
            continue
        digest: str = common.digest(body.tobytes())
        cur: sqlite3.Cursor = con.execute(
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
STORY_PHASH_DISTANCE: int = 10
# The same for another account's story, which has to match more closely: a frame two accounts both
# reshared is all but identical, and unrelated frames from two accounts can land within the above.
STORY_PHASH_DISTANCE_OTHER: int = 4
# Mean luminance (0-255) below which a screenshot is retaken once, in case it caught a fade-in.
DARK_FRAME_MEAN: float = 25.0


class _Resizable(Protocol):
    """Image.resize() as _dhash() calls it. Pillow annotates `size` as also accepting a numpy array,
    a type left unknown when numpy isn't installed, so the method is read through this instead."""

    def resize(self, size: tuple[int, int]) -> Image.Image: ...


def _resized(img: _Resizable, size: tuple[int, int]) -> Image.Image:
    return img.resize(size)


def _dhash(img: Image.Image) -> str:
    """64-bit difference hash: survives re-encoding and small overlays, unlike a byte hash."""
    px: bytes = _resized(img.convert("L"), (9, 8)).tobytes()
    bits: int = 0
    i: int
    for i in range(72):
        if i % 9 != 8:
            bits = bits << 1 | (px[i] > px[i + 1])
    return f"{bits:016x}"


def _is_blank_frame(img: Image.Image) -> bool:
    """A near-black crop: the viewer's loading/transition frame, not the story itself."""
    stat: ImageStat.Stat = ImageStat.Stat(img.convert("L"))
    return stat.mean[0] < 8 and stat.stddev[0] < 4


def _brightness(img: Image.Image) -> float:
    """Mean luminance of a frame, from a thumbnail: cheap enough for capture_story()'s timed path."""
    return ImageStat.Stat(_resized(img.convert("L"), (32, 32))).mean[0]


def _find_story_duplicate(con: sqlite3.Connection, username: str, phash: str) -> bool:
    """True if a story stored in the last day (a story's lifetime) looks the same: this account's
    own, or one another account posted too (STORY_PHASH_DISTANCE_OTHER). The byte hash alone missed
    these: every capture re-encodes a fresh screenshot."""
    cutoff: str = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    rows: list[sqlite3.Row] = sqlrows.fetch_all(
        con.execute(
            "SELECT username, phash FROM stories WHERE scraped_at > ? AND phash IS NOT NULL", (cutoff,)
        )
    )
    return any(
        (int(sqlrows.must_str(r, 1), 16) ^ int(phash, 16)).bit_count()
        <= (STORY_PHASH_DISTANCE if sqlrows.cell(r, 0) == username else STORY_PHASH_DISTANCE_OTHER)
        for r in rows
    )
