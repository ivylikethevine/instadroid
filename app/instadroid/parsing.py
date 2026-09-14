"""Pure parsing of accessibility-tree dumps and UI strings: post cards, story tray, Following
list, timestamps, and post identity. No device access, so it's what the replay tests exercise."""

import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, TypedDict

from lxml import etree

from . import common
from .versioning import SELECTORS, versioned

# A caption is "weak" when it's really just the media description Instagram shows before the
# real caption has rendered ("Photo 1 of 2 by X, 113 likes, 10 comments"), or empty. Two cards
# with a weak caption on either side are treated as the same post if the time/author also match;
# real, differing captions never are. See same_post().
_WEAK_CAPTION = re.compile(r"^(Photo|Video|Reel|Image|Carousel)\b.*\bby\b", re.I)

_RELATIVE_AGO = re.compile(r"^(\d+) (second|minute|hour|day|week)s? ago$")
_ABSOLUTE_DATE = re.compile(r"^([A-Z][a-z]+) (\d{1,2})(?:, (\d{4}))?$")
_UNIT_SECONDS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 604800}


def parse_posted_at(text: str, now: datetime) -> tuple[datetime, int] | None:
    """Convert a header/timestamp string ("3 days ago", "August 29", "Yesterday") into an
    absolute UTC instant plus the granularity of that instant in seconds (e.g. 3600 for an
    hours-ago value, 86400 for a bare date). Returns None if the text isn't a format we know."""
    if not text:
        return None
    text = text.strip()
    if text == "Yesterday":
        return now - timedelta(days=1), 86400
    if m := _RELATIVE_AGO.match(text):
        unit = m.group(2)
        seconds = _UNIT_SECONDS[unit]
        return now - timedelta(seconds=int(m.group(1)) * seconds), seconds
    if m := _ABSOLUTE_DATE.match(text):
        try:
            month = datetime.strptime(m.group(1), "%B").month
        except ValueError:
            return None
        year = int(m.group(3)) if m.group(3) else now.year
        try:
            dt = datetime(year, month, int(m.group(2)), tzinfo=UTC)
        except ValueError:
            return None
        if not m.group(3) and dt > now:  # bare "Month Day" with no year: assume the past
            try:
                dt = dt.replace(year=year - 1)
            except ValueError:  # "February 29" rolled back into a non-leap year
                return None
        return dt, 86400
    return None


def is_weak_caption(caption: str) -> bool:
    return not caption or bool(_WEAK_CAPTION.match(caption))


def same_post(existing: Mapping[str, Any], candidate: Mapping[str, Any]) -> bool:
    """True if `existing` (a stored post: username, caption, posted_at) and `candidate` (a
    freshly parsed card: username, caption, posted_at, posted_at_precision) are the same
    Instagram post seen twice — typically because a card was captured before its caption widget
    had rendered, so it got identified by the media description instead. Requires the same
    author and posted times within the candidate's own granularity (floor 1h); captions must
    then agree, or one side must be a weak/placeholder caption."""
    if existing["username"] != candidate["username"]:
        return False
    ea, ca = existing.get("posted_at"), candidate.get("posted_at")
    if ea is None or ca is None:
        return False
    tolerance = max(candidate.get("posted_at_precision") or 0, 3600)
    if abs((ea - ca).total_seconds()) > tolerance:
        return False
    ecap, ccap = existing.get("caption") or "", candidate.get("caption") or ""
    if is_weak_caption(ecap) or is_weak_caption(ccap):
        return True
    return ecap[:200] == ccap[:200]


@versioned
def parse_following_list(xml: str) -> list[str]:
    """Every username row currently on screen on the Following-list screen, in the order they
    appear. Deliberately narrow: only the row's own username TextView (follow_list_username) is
    matched, so the "Categories" suggestion cards above the list (own resource-ids: title/subtitle)
    and the "Sorted by ..." header can never be mistaken for a followed account."""
    root = etree.fromstring(xml.encode())
    suffix = f"id/{SELECTORS['follow_list_username_id']}"
    rows = (n for n in root.iter("node") if (n.get("resource-id") or "").endswith(suffix))
    return [text for n in rows if (text := n.get("text"))]


class Post(TypedDict):
    """One post card as parse_hierarchy() found it on screen. Bounds are uiautomator strings."""

    kind: str  # photo | video | carousel | post (header-less card with no media description)
    username: str
    posted_date: str  # as shown: "3 days ago", "August 29"
    place: str
    caption: str
    caption_bounds: str | None
    caption_truncated: bool  # ends in "… more"; see capture.expand_caption()
    bounds: str | None  # the media
    share_bounds: str | None
    header_bounds: str | None
    clip_top: int  # bottom of the floating action bar; crops start below it
    alt: str  # the media's content-desc ("Photo 1 of 7 by ...")
    headless: bool  # the top card, whose header had already scrolled off
    complete: bool  # the whole bottom of the card was on screen, so its identity is stable
    merged_reel: bool  # a collaborator Reel whose header came after its media


class StoryItem(TypedDict):
    username: str
    seen: bool
    bounds: str | None


def _new_post(user: str, kind: str, date: str, place: str, clip_top: int) -> Post:
    return {
        "kind": kind,
        "username": user,
        "posted_date": date,
        "place": place,
        "caption": "",
        "caption_bounds": None,
        "caption_truncated": False,
        "bounds": None,
        "share_bounds": None,
        "header_bounds": None,
        "clip_top": clip_top,
        "alt": "",
        "headless": False,
        "complete": False,
        "merged_reel": False,
    }


@versioned
def parse_hierarchy(xml: str) -> list[Post]:
    """Return a list of post dicts found in the current screen's accessibility tree.

    A post is several sibling rows in the feed RecyclerView (header+media, buttons, caption...),
    so we walk the tree in document order: a header starts a post and everything up to the next
    header belongs to it. Rows that appear before the first header belong to a post whose header
    has already scrolled off the top; we identify that one from its caption ("<user> text") and
    media description instead."""
    root = etree.fromstring(xml.encode())
    posts: list[Post] = []
    cur: Post | None = None
    # The action bar floats over the list; remember where it ends so crops can skip it.
    clip_top = 0
    for n in root.iter("node"):
        if (n.get("resource-id") or "").endswith(SELECTORS["action_bar_id"]):
            if b := common.parse_bounds(n.get("bounds")):
                clip_top = b[3]
            break
    in_list = False
    for n in root.iter("node"):
        rid = (n.get("resource-id") or "").split("/")[-1]
        desc = n.get("content-desc") or ""
        text = n.get("text") or ""
        if n.get("resource-id") == SELECTORS["feed_list_id"]:
            in_list = True
            cur = _new_post("", "", "", "", clip_top)  # provisional: the header-less top card
            cur["headless"] = True
            posts.append(cur)
            continue
        if not in_list:
            continue
        if rid == SELECTORS["header_id"]:
            m = SELECTORS["header_desc"].match(desc)
            # A Reel/video card tagged with collaborators ("<user> and N others" — confirmed live
            # 2026-09-11) renders its media node, and the "Reel by ..." alt description that comes
            # with it, *before* its own header — unlike every other card layout, where the header
            # always comes first. When that happens for the very top-of-screen provisional
            # "headless" card, this header is that same card's header discovered late, not a
            # different, already-scrolled-off card's: a genuinely different off-screen card would
            # already have picked up its own username (from a caption) or share_bounds before any
            # later header could appear, so headless+no-username+no-caption+no-share_bounds can
            # only mean "still nothing but this card's own leading media." Fold the identity in
            # rather than starting a second, disconnected entry that would never pass the final
            # username+(caption or alt) filter below.
            if (
                m
                and cur is not None
                and cur["headless"]
                and not cur["username"]
                and not cur["caption"]
                and not cur["share_bounds"]
                and cur["alt"]
                and cur["alt"].split()[0].lower() in ("reel", "video")
            ):
                cur["username"] = m.group("user")
                cur["posted_date"] = m.group("date")
                cur["place"] = m.group("place") or ""
                cur["header_bounds"] = n.get("bounds")
                cur["headless"] = False
                cur["merged_reel"] = True
                continue
            if cur is not None and cur["share_bounds"]:
                cur["complete"] = True  # we saw the whole bottom of the previous card
            cur = None
            if m:  # sponsored / suggested cards have a different header and are skipped
                cur = _new_post(
                    m.group("user"),
                    m.group("kind").lower(),
                    m.group("date"),
                    m.group("place") or "",
                    clip_top,
                )
                cur["header_bounds"] = n.get("bounds")  # this node *is* the header
                posts.append(cur)
            continue
        if cur is None:
            continue
        if cur["bounds"] is None and rid in SELECTORS["media_ids"]:
            cur["bounds"] = n.get("bounds")
        elif cur["share_bounds"] is None and rid == SELECTORS["share_id"]:
            cur["share_bounds"] = n.get("bounds")
            if cur["merged_reel"]:
                # This layout has no separate caption or timestamp node to wait for (confirmed
                # live: zero caption-class nodes anywhere in a real dump of one) — the share
                # button itself is the bottom of the card.
                cur["complete"] = True
        elif (
            cur["headless"]
            and cur["kind"] in ("", "post")
            and desc.startswith(SELECTORS["mute_toggle_desc_prefix"])
        ):
            cur["kind"] = "video"  # reels have a mute toggle and no media description
        elif not cur["alt"] and SELECTORS["media_alt"].match(desc):
            cur["alt"] = desc
            if cur["headless"] and not cur["kind"]:
                cur["kind"] = SELECTORS["alt_kind"].get(desc.split()[0].lower(), "")
        elif not cur["caption"] and text and n.get("class") == SELECTORS["caption_class"]:
            if cur["headless"] and not cur["username"]:
                cur["username"] = text.split(" ", 1)[0]
            cur["caption_truncated"] = bool(SELECTORS["caption_more_suffix"].search(text))
            cur["caption"] = clean_caption(text, cur["username"])
            cur["caption_bounds"] = n.get("bounds")
            if cur["share_bounds"]:
                cur["complete"] = True
        elif SELECTORS["timestamp"].match(text) and cur["share_bounds"]:
            if cur["headless"] and not cur["posted_date"]:
                cur["posted_date"] = text
            cur["complete"] = True  # the timestamp row sits below the caption
    # The provisional top card only counts if we could identify it.
    for p in posts:
        if p["headless"] and not p["kind"]:
            p["kind"] = "post"
    # A card with neither a caption nor a media description can't be identified (post_id() would
    # hash nothing but the username); leave it out and it will be picked up on a later dump once
    # more of it has rendered, instead of being stored as an empty placeholder.
    return [p for p in posts if p["username"] and (p["caption"] or p["alt"])]


@versioned
def clean_caption(text: str, user: str) -> str:
    """'user Caption text… more' -> 'Caption text…'. The app truncates long captions itself
    (see capture.expand_caption() for recovering the untruncated text)."""
    text = text.replace(" ", " ").strip()
    if text.startswith(user + " "):
        text = text[len(user) + 1 :]
    return SELECTORS["caption_more_suffix"].sub("…", text).strip()


@versioned
def parse_story_tray(xml: str) -> list[StoryItem]:
    """Return the Home feed's story tray items (empty if the tray isn't on screen — it only
    appears on Home, not on Following), skipping index 0 (always the logged-in account's own
    story). Each item's content-desc doubles as a compact seen-state signal:
    "<user>'s story, <index> of <total>, Unseen." — used only to prioritize which accounts to
    open, since the stories table (keyed by a content hash, not this label) is the actual record
    of what's already been captured."""
    root = etree.fromstring(xml.encode())
    tray = next(
        (n for n in root.iter("node") if (n.get("resource-id") or "").endswith(SELECTORS["story_tray_id"])),
        None,
    )
    if tray is None:
        return []
    items: list[StoryItem] = []
    for n in tray.iter("node"):
        # The avatar image inside shares the same content-desc as its parent Button; restrict to
        # the Button itself so each tray item is matched exactly once.
        if not (n.get("class") or "").endswith("Button"):
            continue
        m = SELECTORS["story_item_desc"].match(n.get("content-desc") or "")
        if not m or int(m.group("index")) == 0:
            continue
        items.append(
            {
                "username": m.group("user"),
                "seen": m.group("seen") != "Unseen",
                "bounds": n.get("bounds"),
            }
        )
    return items


def _post_key(p: Post) -> str:
    """What post_id() hashes. Must not depend on anything that changes while the post sits in the
    feed: like counts, relative dates, carousel index."""
    # Caption if there is one, else the media description up to the first comma ("Photo  of  by X").
    key = p["caption"][:200] if p["caption"] else re.sub(r"\d+", "", p["alt"].split(",")[0])
    # No kind here: a header-less video card has no media description to infer it from.
    return f"{p['username']}|{key}"


def post_id(p: Post) -> str:
    """Cheap identity for the first-pass 'have we stored this' check."""
    return common.digest(_post_key(p))


@versioned
def carousel_count(alt: str) -> int:
    """Parse the slide total from a carousel card's media description ("Photo 1 of 7 by X, 317
    likes, 10 comments"). Returns 1 (not a carousel, or the format drifted) if it can't be parsed."""
    m = SELECTORS["slide_index"].match(alt or "")
    return int(m.group(2)) if m else 1


class ScreenParse(TypedDict):
    """Everything the parsers find on one hierarchy dump, as the replay fixtures record it."""

    posts: list[dict]  # each Post plus its "id" (post_id), so identity drift shows up too
    story_tray: list[StoryItem]
    following_list: list[str]


def parse_screen(xml: str) -> ScreenParse:
    """Run every parser over one dump with the active profile (see tests/test_replay.py)."""
    return {
        "posts": [{**p, "id": post_id(p)} for p in parse_hierarchy(xml)],
        "story_tray": parse_story_tray(xml),
        "following_list": parse_following_list(xml),
    }
