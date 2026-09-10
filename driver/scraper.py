"""
Instagram -> SQLite scraper driving a real Instagram app inside an Android emulator via uiautomator2.

Strategy: open the chronological "Following" feed, scroll slowly, parse the accessibility
tree for post cards, store new ones, stop once we hit posts we've already seen.

Selectors live in the SELECTORS dict below. Instagram changes its UI a few times a year;
when a run fails, look at the hierarchy dump in $DEBUG_DIR and adjust them.
"""

import hashlib
import os
import random
import re
import sqlite3
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import adbutils
import uiautomator2 as u2
from lxml import etree
from PIL import Image

ADB_ADDR = os.environ.get("ADB_ADDR", "127.0.0.1:5557")  # run-emulator.sh's ADB port; redroid: 127.0.0.1:5555
DB_PATH = os.environ.get("DB_PATH", "/db/posts.sqlite")
MEDIA_DIR = Path(os.environ.get("MEDIA_DIR", "/media"))
DEBUG_DIR = Path(os.environ.get("DEBUG_DIR", "/debug"))
POLL_MIN_H = float(os.environ.get("POLL_MIN_HOURS", "2.5"))
POLL_MAX_H = float(os.environ.get("POLL_MAX_HOURS", "4.5"))
MAX_SCROLLS = int(os.environ.get("MAX_SCROLLS", "25"))
STOP_AFTER_SEEN = int(os.environ.get("STOP_AFTER_SEEN", "4"))
RETAIN_DAYS = int(os.environ.get("RETAIN_DAYS", "60"))  # 0 disables deletion
IG_USERNAME = os.environ.get("IG_USERNAME", "")
IG_PASSWORD = os.environ.get("IG_PASSWORD", "")
IG_PKG = "com.instagram.android"
DEBUG_KEEP = 12  # debug dump pairs to retain; older ones are pruned on every new dump

# --- Selectors (the fragile part) ---------------------------------------------------
SELECTORS = {
    # Every feed card has a header ViewGroup whose content-desc reads e.g.
    #   "nykkyhex posted a video in Rich's San Diego 21 hours ago"
    #   "clubsabbat posted a carousel in San Diego, California 3 days ago"
    #   "someone posted a photo August 29"
    "header_id": "row_feed_profile_header",
    "header_desc": re.compile(
        r"^(?P<user>[\w.]+) posted (?:an? )?(?P<kind>\w+)(?: in (?P<place>.+?))?"
        r" (?P<date>\d+ (?:second|minute|hour|day|week)s? ago|[A-Z][a-z]+ \d{1,2}(?:, \d{4})?|Yesterday)$"
    ),
    # Resource-id substrings marking the media area of a card (used for the screenshot crop).
    "media_ids": (
        "carousel_media_group",
        "media_group",
        "row_feed_photo_imageview",
        "zoomable_view_container",
    ),
    # Content-desc on the media itself ("Reel by Nykky Hex, Liked by ..., August 29" / "Photo 1 of 7 by ...").
    "media_alt": re.compile(r"^(Photo|Video|Reel|Image|Carousel)\b", re.I),
    "username_id": "row_feed_photo_profile_name",
    # Caption widget ("<user> text… more"), share button, and the share sheet's Copy link entry.
    "caption_class": "com.instagram.ui.widget.textview.IgTextLayoutView",
    "timestamp": re.compile(
        r"^(\d+ (?:second|minute|hour|day|week)s? ago|[A-Z][a-z]+ \d{1,2}(?:, \d{4})?|Yesterday)$"
    ),
    # Map the media description's leading word to the header's kind vocabulary.
    "alt_kind": {
        "reel": "video",
        "video": "video",
        "photo": "photo",
        "image": "photo",
        "carousel": "carousel",
    },
    "share_id": "row_feed_button_share",
    "copy_link_desc": "Copy link",
    # Anything that means a share/bottom sheet is open. We never interact inside one except to
    # tap "Copy link"; a stray tap there could message a contact.
    "sheet_markers_text": ["Write a message…"],
    "sheet_markers_desc": ["New group"],
    "permalink": re.compile(r"https://www\.instagram\.com/(?P<type>p|reel|reels|tv)/(?P<code>[\w-]+)"),
    # The "Home ⌄" title button at the top of the feed opens the Following/Favorites chooser.
    "feed_switcher_desc": "Instagram Home Feed",
    "following_text": "Following",
    # The Following feed is its own screen: Back button + action_bar_title "Following".
    "following_title_id": "action_bar_title",
    # Login screen. Instagram renames these occasionally; several candidates each.
    "login_username_ids": ["login_username", "username"],
    "login_username_hints": [
        "Mobile number or email",
        "Username, email or mobile number",
        "Phone number, username or email",
        "Username, email address or mobile number",
    ],
    "login_password_ids": ["password"],
    "login_password_hints": ["Password"],
    "login_button_texts": ["Log in", "Log In"],
    "login_page_markers": ["Log in", "Log In", "Forgot password?"],
    # Post-login interstitials and the buttons that dismiss them.
    "dismiss_texts": ["Not now", "Not Now", "Skip", "Save", "Continue", "Don’t allow", "Cancel", "OK"],
    # Anything matching these means a human has to intervene.
    "challenge_texts": [
        "confirmation code",
        "Confirm it's you",
        "Suspicious login",
        "security code",
        "Enter the code",
        "We Detected An Unusual Login",
        "Help us confirm it's you",
    ],
}
# ------------------------------------------------------------------------------------

# A caption is "weak" when it's really just the media description Instagram shows before the
# real caption has rendered ("Photo 1 of 2 by X, 113 likes, 10 comments"), or empty. Two cards
# with a weak caption on either side are treated as the same post if the time/author also match;
# real, differing captions never are. See same_post().
_WEAK_CAPTION = re.compile(r"^(Photo|Video|Reel|Image|Carousel)\b.*\bby\b", re.I)

_RELATIVE_AGO = re.compile(r"^(\d+) (second|minute|hour|day|week)s? ago$")
_ABSOLUTE_DATE = re.compile(r"^([A-Z][a-z]+) (\d{1,2})(?:, (\d{4}))?$")
_UNIT_SECONDS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 604800}


def log(*a):
    print(datetime.now().strftime("%H:%M:%S"), *a, flush=True)


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


def _is_weak_caption(caption: str) -> bool:
    return not caption or bool(_WEAK_CAPTION.match(caption))


def same_post(existing: dict, candidate: dict) -> bool:
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
    if _is_weak_caption(ecap) or _is_weak_caption(ccap):
        return True
    return ecap[:200] == ccap[:200]


def db_init():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute(
        """CREATE TABLE IF NOT EXISTS posts (
            id TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            kind TEXT,
            posted_date TEXT,
            caption TEXT,
            media_file TEXT,
            scraped_at TEXT NOT NULL
        )"""
    )
    cols = {r["name"] for r in con.execute("PRAGMA table_info(posts)")}
    for col in ("hash", "url", "place", "posted_at"):
        if col not in cols:
            con.execute(f"ALTER TABLE posts ADD COLUMN {col} TEXT")
    con.execute("CREATE INDEX IF NOT EXISTS posts_hash ON posts(hash)")
    con.execute("CREATE INDEX IF NOT EXISTS posts_username_posted_at ON posts(username, posted_at)")
    con.commit()
    _migrate_dedupe(con)
    return con


def _safe_parse_posted_at(posted_date, scraped_at_iso):
    """parse_posted_at(), tolerant of a malformed/legacy scraped_at that fromisoformat rejects.
    Returns (None, None) instead of raising, so one corrupt row can't abort the whole migration."""
    try:
        now = datetime.fromisoformat(scraped_at_iso)
    except TypeError, ValueError:
        return None, None
    return parse_posted_at(posted_date, now) or (None, None)


def _migrate_dedupe(con):
    """One-time cleanup, guarded by PRAGMA user_version so it runs exactly once: backfill
    posted_at for rows written before that column existed, then merge any rows same_post()
    considers duplicates — the bug that let a card get stored twice when its caption hadn't
    rendered on the first pass. Uses the same merge path as a live scrape (_find_duplicate /
    _merged_fields / _write_merged). Each row is handled defensively: a single corrupt row must
    not turn into a permanent boot loop (user_version is only bumped once every row is done)."""
    if con.execute("PRAGMA user_version").fetchone()[0] >= 1:
        return
    log("running one-time dedupe migration")
    for r in con.execute("SELECT id, posted_date, scraped_at FROM posts WHERE posted_at IS NULL"):
        posted_at, _ = _safe_parse_posted_at(r["posted_date"], r["scraped_at"])
        if posted_at:
            con.execute("UPDATE posts SET posted_at=? WHERE id=?", (posted_at.isoformat(), r["id"]))
    con.commit()
    merged = 0
    for (rid,) in con.execute("SELECT id FROM posts ORDER BY scraped_at").fetchall():
        r = con.execute("SELECT * FROM posts WHERE id=?", (rid,)).fetchone()
        if r is None:
            continue  # already merged away as another row's duplicate
        try:
            posted_at, precision = _safe_parse_posted_at(r["posted_date"], r["scraped_at"])
            dup = _find_duplicate(con, r["username"], posted_at, precision, r["caption"], exclude_id=r["id"])
            if not dup:
                continue
            final_id, fields, media_to_drop = _merged_fields(
                dup,
                r["id"],
                r["url"],
                r["hash"],
                r["kind"],
                r["posted_date"],
                r["place"],
                r["caption"],
                r["media_file"],
                posted_at,
            )
            if media_to_drop:
                (MEDIA_DIR / media_to_drop).unlink(missing_ok=True)
            if final_id != r["id"]:
                con.execute("DELETE FROM posts WHERE id=?", (r["id"],))
            _write_merged(con, dup["id"], final_id, fields)
            merged += 1
        except Exception as e:  # a single corrupt/unexpected row must not block every future start
            log(f"WARN: dedupe migration skipped row {rid!r}:", repr(e))
    con.execute("PRAGMA user_version = 1")
    con.commit()
    log(f"dedupe migration: merged {merged} duplicate row(s)")


def _find_duplicate(con, username, posted_at, posted_at_prec, caption, exclude_id=None):
    """Look up a stored row that same_post() considers the same post as this freshly-parsed
    card, within a coarse SQL time window (same_post itself applies the exact tolerance)."""
    if posted_at is None:
        return None
    window = timedelta(seconds=max(posted_at_prec or 0, 3600) * 2)
    candidate = {
        "username": username,
        "caption": caption,
        "posted_at": posted_at,
        "posted_at_precision": posted_at_prec,
    }
    for r in con.execute(
        "SELECT * FROM posts WHERE username=? AND posted_at BETWEEN ? AND ?",
        (username, (posted_at - window).isoformat(), (posted_at + window).isoformat()),
    ):
        if exclude_id and r["id"] == exclude_id:
            continue
        existing = {
            "username": r["username"],
            "caption": r["caption"],
            "posted_at": datetime.fromisoformat(r["posted_at"]) if r["posted_at"] else None,
        }
        if same_post(existing, candidate):
            return r
    return None


def _merged_fields(existing, pid, url, h, kind, posted_date, place, caption, media, posted_at):
    """Compute the row that should replace `existing` (a sqlite3.Row from posts) once a
    duplicate for the same post is found: keep the permalink id/url over a hash id, a real
    caption over a weak/placeholder one, and whichever media crop already exists. Returns
    (final_id, fields_dict, media_to_drop) — the caller deletes media_to_drop and applies the
    write via _write_merged."""
    final_id = pid if (url and not existing["url"]) else existing["id"]
    final_caption = (
        caption
        if not _is_weak_caption(caption) and _is_weak_caption(existing["caption"] or "")
        else existing["caption"]
    )
    final_media, media_to_drop = existing["media_file"], None
    if media and existing["media_file"] and media != existing["media_file"]:
        media_to_drop = media  # existing crop wins; the new one is redundant
    elif media and not existing["media_file"]:
        final_media = media
    fields = {
        "username": existing["username"],
        "kind": existing["kind"] or kind,
        "posted_date": existing["posted_date"] or posted_date,
        "caption": final_caption,
        "media_file": final_media,
        "scraped_at": existing["scraped_at"],
        "hash": h,
        "url": existing["url"] or url,
        "place": existing["place"] or place,
        "posted_at": existing["posted_at"] or (posted_at.isoformat() if posted_at else None),
    }
    return final_id, fields, media_to_drop


def _write_merged(con, old_id, final_id, fields):
    """Apply a _merged_fields() result: replace `old_id`'s row with one at `final_id`."""
    if final_id != old_id:
        con.execute("DELETE FROM posts WHERE id=?", (old_id,))
    cols = ["id", *fields.keys()]
    con.execute(
        f"INSERT OR REPLACE INTO posts ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
        (final_id, *fields.values()),
    )


def connect_device():
    log("connecting to", ADB_ADDR)
    adbutils.adb.connect(ADB_ADDR, timeout=30)
    d = u2.connect(ADB_ADDR)
    d.implicitly_wait(10)
    log("device:", d.info.get("productName"), d.window_size())
    return d


def human_pause(lo=1.0, hi=3.0):
    time.sleep(random.uniform(lo, hi))


def human_scroll(d):
    """Scroll up by a random amount at a random speed, like a thumb would."""
    w, h = d.window_size()
    x = random.randint(int(w * 0.3), int(w * 0.7))
    y1 = random.randint(int(h * 0.65), int(h * 0.8))
    y2 = y1 - random.randint(int(h * 0.3), int(h * 0.45))
    # Slow enough not to fling: a fling scrolls several screens and skips whole posts.
    d.swipe(x, y1, x, y2, duration=random.uniform(0.6, 1.0))


def _first(d, **kinds):
    """Return the first existing selector among the given candidate lists."""
    for kind, values in kinds.items():
        for v in values:
            sel = d(**{kind: v})
            if sel.exists(timeout=1):
                return sel
    return None


def _challenge_present(d):
    for t in SELECTORS["challenge_texts"]:
        if d(textContains=t).exists(timeout=0.5):
            return t
    return None


def _prune_debug_dumps():
    hierarchies = sorted(DEBUG_DIR.glob("*_hierarchy.xml"), key=lambda p: p.stat().st_mtime)
    for old in hierarchies[:-DEBUG_KEEP]:
        stem = old.name.removesuffix("_hierarchy.xml")
        old.unlink(missing_ok=True)
        for ext in ("_screen.jpg", "_screen.png"):
            (DEBUG_DIR / f"{stem}{ext}").unlink(missing_ok=True)


def _dump_debug(d, name, xml=None):
    """Save a hierarchy + screenshot pair for later inspection. Pass `xml` when the caller
    already has a fresh dump, to avoid a redundant device round-trip."""
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    (DEBUG_DIR / f"{name}_hierarchy.xml").write_text(xml if xml is not None else d.dump_hierarchy())
    d.screenshot().convert("RGB").save(DEBUG_DIR / f"{name}_screen.jpg", quality=70)
    _prune_debug_dumps()


def _dismiss_interstitials(d, rounds=4):
    for _ in range(rounds):
        btn = _first(d, text=SELECTORS["dismiss_texts"])
        if not btn:
            return
        btn.click()
        human_pause(1.5, 3)


def _login_form(d):
    """Return (username_field, password_field) or None. Instagram's login screen is Jetpack
    Compose: the labels are plain Views and the two EditTexts carry no id, so we go by order."""
    if not _first(d, text=SELECTORS["login_username_hints"]) and not _first(
        d, text=SELECTORS["login_password_hints"]
    ):
        return None
    edits = d(className="android.widget.EditText")
    if edits.count < 2:
        return None
    return edits[0], edits[1]


def ensure_logged_in(d):
    """If the login screen is showing, fill credentials from the environment and log in.

    Returns True if we are (or became) logged in. Raises RuntimeError on a 2FA/challenge
    screen so the caller can abort and a human can finish it.
    """
    if IG_PKG not in d.app_list():
        raise RuntimeError(f"{IG_PKG} is not installed on the device; adb install it first")
    d.app_start(IG_PKG, stop=False)
    human_pause(4, 6)
    ok = d(text="OK")  # stray "Enter your password" style alert from a previous attempt
    if ok.exists(timeout=1):
        ok.click()
        human_pause()
    if c := _challenge_present(d):
        _dump_debug(d, "login")
        raise RuntimeError(f"Instagram wants a human: '{c}' screen; see {DEBUG_DIR}")
    form = _login_form(d)
    if not form:
        if _first(d, text=SELECTORS["login_page_markers"]):
            _dump_debug(d, "login")
            raise RuntimeError(f"login page shown but form not recognised; see {DEBUG_DIR}")
        return True  # no login screen; assume session is live
    if not (IG_USERNAME and IG_PASSWORD):
        _dump_debug(d, "login")
        raise RuntimeError("login screen shown but IG_USERNAME/IG_PASSWORD not set")
    user_field, pw_field = form
    log("login screen detected; entering credentials as", IG_USERNAME)
    user_field.click()
    human_pause(0.5, 1.2)
    user_field.set_text(IG_USERNAME)
    human_pause(1, 2)
    pw_field.click()
    human_pause(0.5, 1.2)
    pw_field.set_text(IG_PASSWORD)
    human_pause(1, 2)
    btn = _first(d, description=SELECTORS["login_button_texts"]) or _first(
        d, text=SELECTORS["login_button_texts"]
    )
    if btn:
        btn.click()
    else:
        d.press("enter")
    log("submitted login; waiting")
    human_pause(10, 14)
    if c := _challenge_present(d):
        _dump_debug(d, "login")
        raise RuntimeError(f"Instagram wants a human: '{c}' screen; see {DEBUG_DIR}")
    _dismiss_interstitials(d)
    if _login_form(d):
        _dump_debug(d, "login")
        raise RuntimeError(f"still on login screen after submit (wrong password?); see {DEBUG_DIR}")
    log("logged in")
    return True


def _on_following_feed(d):
    t = d(resourceIdMatches=f".*:id/{SELECTORS['following_title_id']}$", text=SELECTORS["following_text"])
    return t.exists(timeout=1)


def open_following_feed(d):
    ensure_logged_in(d)
    if d.app_current().get("package") != IG_PKG:
        d.app_start(IG_PKG, stop=False)
        human_pause(3, 5)
    _dismiss_interstitials(d)  # notification / location / "set up on new device" prompts
    close_sheets(d)
    w, h = d.window_size()
    sw = d(description=SELECTORS["feed_switcher_desc"])
    for attempt in range(4):
        # The action bar hides while scrolled; pull back to the top so we can see where we are.
        for _ in range(12):
            if sw.exists(timeout=1) or _on_following_feed(d):
                break
            d.swipe(w // 2, int(h * 0.3), w // 2, int(h * 0.8), duration=0.3)
            human_pause(0.8, 1.5)
        if _on_following_feed(d):
            if attempt > 0:
                return True  # we navigated here a moment ago; the screen just took a while
            # Left over from the last run: leave and re-enter so the feed is fresh.
            d.press("back")
            human_pause(2, 3)
            continue
        if sw.exists(timeout=3):
            try:
                f = d(text=SELECTORS["following_text"])
                for tap in range(4):  # taps get swallowed while the app is still warming up
                    sw.click()
                    if f.exists(timeout=5):
                        break
                    log(f"feed switch attempt {attempt}: switcher tap {tap} opened nothing")
                if f.exists(timeout=1):
                    f.click()
                    human_pause(3, 5)
                    for _ in range(6):  # cold starts can take a while to build the screen
                        if _on_following_feed(d):
                            return True
                        time.sleep(2)
                    log(f"feed switch attempt {attempt}: clicked Following but title not found")
                else:
                    _dump_debug(d, f"feed_switch_menu{attempt}")
                    d.press("back")
            except Exception as e:  # the header can scroll away between exists() and click()
                log("WARN: feed switcher click failed, retrying:", repr(e))
        else:
            d.press("back")  # some other screen; step out and retry
            human_pause(1, 2)
    log("WARN: could not open Following feed; scraping whatever feed is showing (dump saved)")
    _dump_debug(d, "feed_switch")
    return False


def _new_post(user, kind, date, place, clip_top):
    return {
        "kind": kind,
        "username": user,
        "posted_date": date,
        "place": place,
        "caption": "",
        "bounds": None,
        "share_bounds": None,
        "clip_top": clip_top,
        "alt": "",
        "headless": False,
        "complete": False,
    }


def parse_hierarchy(xml: str):
    """Return a list of post dicts found in the current screen's accessibility tree.

    A post is several sibling rows in the feed RecyclerView (header+media, buttons, caption...),
    so we walk the tree in document order: a header starts a post and everything up to the next
    header belongs to it. Rows that appear before the first header belong to a post whose header
    has already scrolled off the top; we identify that one from its caption ("<user> text") and
    media description instead."""
    root = etree.fromstring(xml.encode())
    posts, cur = [], None
    # The action bar floats over the list; remember where it ends so crops can skip it.
    clip_top = 0
    for n in root.iter("node"):
        if (n.get("resource-id") or "").endswith("action_bar_container"):
            m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", n.get("bounds") or "")
            if m:
                clip_top = int(m.group(4))
            break
    in_list = False
    for n in root.iter("node"):
        rid = (n.get("resource-id") or "").split("/")[-1]
        desc = n.get("content-desc") or ""
        text = n.get("text") or ""
        if n.get("resource-id") == "android:id/list":
            in_list = True
            cur = _new_post("", "", "", "", clip_top)  # provisional: the header-less top card
            cur["headless"] = True
            posts.append(cur)
            continue
        if not in_list:
            continue
        if rid == SELECTORS["header_id"]:
            if cur is not None and cur["share_bounds"]:
                cur["complete"] = True  # we saw the whole bottom of the previous card
            m = SELECTORS["header_desc"].match(desc)
            cur = None
            if m:  # sponsored / suggested cards have a different header and are skipped
                cur = _new_post(
                    m.group("user"),
                    m.group("kind").lower(),
                    m.group("date"),
                    m.group("place") or "",
                    clip_top,
                )
                posts.append(cur)
            continue
        if cur is None:
            continue
        if cur["bounds"] is None and rid in SELECTORS["media_ids"]:
            cur["bounds"] = n.get("bounds")
        elif cur["share_bounds"] is None and rid == SELECTORS["share_id"]:
            cur["share_bounds"] = n.get("bounds")
        elif cur["headless"] and cur["kind"] in ("", "post") and desc.startswith("Turn sound"):
            cur["kind"] = "video"  # reels have a mute toggle and no media description
        elif not cur["alt"] and SELECTORS["media_alt"].match(desc):
            cur["alt"] = desc
            if cur["headless"] and not cur["kind"]:
                cur["kind"] = SELECTORS["alt_kind"].get(desc.split()[0].lower(), "")
        elif not cur["caption"] and text and n.get("class") == SELECTORS["caption_class"]:
            if cur["headless"] and not cur["username"]:
                cur["username"] = text.split(" ", 1)[0]
            cur["caption"] = clean_caption(text, cur["username"])
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


def clean_caption(text: str, user: str) -> str:
    """'user Caption text… more' -> 'Caption text…'. The app truncates long captions itself."""
    text = text.replace(" ", " ").strip()
    if text.startswith(user + " "):
        text = text[len(user) + 1 :]
    return re.sub(r"\s*(?:…|\.\.\.)?\s*more$", "…", text).strip()


def bounds_center(bounds: str):
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds or "")
    if not m:
        return None
    x1, y1, x2, y2 = map(int, m.groups())
    return (x1 + x2) // 2, (y1 + y2) // 2


def _sheet_open(d):
    if d(description=SELECTORS["copy_link_desc"]).exists(timeout=0.3):
        return True
    if any(d(text=t).exists(timeout=0.3) for t in SELECTORS["sheet_markers_text"]):
        return True
    return any(d(description=t).exists(timeout=0.3) for t in SELECTORS["sheet_markers_desc"])


def close_sheets(d, max_back=2):
    """Back out of any open share/bottom sheet without touching its contents."""
    for i in range(max_back):
        if not _sheet_open(d) or d.app_current().get("package") != IG_PKG:
            break
        if i > 0 and _on_feed(d):
            break  # feed rows visible: the marker is a false positive, another Back would exit
        d.press("back")
        human_pause(1.5, 2)
    if d.app_current().get("package") != IG_PKG:
        log("WARN: left Instagram while closing a sheet; relaunching")
        d.app_start(IG_PKG, stop=False)
        human_pause(3, 5)
    return not _sheet_open(d)


def _on_feed(d):
    """True when a feed list with post rows is showing (title bars hide while scrolled, so
    they are not a reliable signal)."""
    return d(resourceIdMatches=f".*:id/{SELECTORS['share_id']}").exists(timeout=0.5) or d(
        resourceIdMatches=f".*:id/{SELECTORS['header_id']}"
    ).exists(timeout=0.5)


def _on_home_feed(d):
    """The Home feed keeps the bottom tab bar; the Following screen does not."""
    return d(resourceIdMatches=".*:id/feed_tab").exists(timeout=0.5)


def _back_to_feed(d, tries=2):
    """If a tap opened a profile/hashtag/etc., back out until a feed is showing again. Never
    backs out of the app: if we somehow left it, relaunch instead."""
    for _ in range(tries):
        if d.app_current().get("package") != IG_PKG:
            d.app_start(IG_PKG, stop=False)
            human_pause(3, 5)
            return _on_feed(d)
        if _on_feed(d) and not _sheet_open(d):
            return True
        d.press("back")
        human_pause(1, 1.5)
    return _on_feed(d)


_last_url = ""


def fetch_permalink(d, post_hash: str):
    """Open the share sheet for the post with this hash, pick 'Copy link', read the clipboard.

    Re-dumps the hierarchy right before tapping and clicks the share *element* (not stale
    coordinates) because the feed can shift a few hundred px between a dump and a tap.
    Returns the canonical URL or None."""
    if not close_sheets(d):
        log("WARN: a sheet is stuck open; skipping permalink")
        return None
    fresh = next((p for p in parse_hierarchy(d.dump_hierarchy()) if post_id(p) == post_hash), None)
    if not fresh or not fresh["share_bounds"]:
        log("WARN: card moved before the share tap; no permalink")
        return None
    link = d(description=SELECTORS["copy_link_desc"])
    for _tap in range(2):  # the first tap is occasionally swallowed by the video overlay
        # Coordinate tap from the fresh dump: element-based clicks on this (non-clickable)
        # ViewGroup are unreliable on video cards.
        d.click(*bounds_center(fresh["share_bounds"]))
        human_pause(2, 3)
        if link.exists(timeout=10):
            break
    if not link.exists(timeout=1):
        log("WARN: no share sheet with 'Copy link'; dump saved")
        _dump_debug(d, "share_sheet")
        close_sheets(d)
        _back_to_feed(d)
        return None
    # Note: clearing the clipboard first (d.set_clipboard) makes the next read come back empty.
    # Staleness is caught below by comparing with the last link we handed out.
    human_pause(0.8, 1.2)  # let the sheet finish animating
    try:
        b = link.info.get("bounds") or {}
        cx, cy = (b["left"] + b["right"]) // 2, (b["top"] + b["bottom"]) // 2
    except Exception as e:  # the sheet re-rendered and the node vanished
        log("WARN: Copy link vanished before click:", repr(e))
        close_sheets(d)
        _back_to_feed(d)
        return None
    d.click(cx, cy)
    # Poll instead of a single fixed-delay read: the clipboard write can lag the tap by more
    # than a beat, and the old one-shot read missed it more often than not.
    global _last_url
    url = ""
    deadline = time.time() + 4
    while time.time() < deadline:
        time.sleep(0.4)
        try:
            candidate = d.clipboard or ""
        except Exception as e:
            log("WARN: clipboard read failed:", repr(e))
            continue
        if candidate and candidate != _last_url and SELECTORS["permalink"].match(candidate):
            url = candidate
            break
    if url and url == _last_url:
        log("WARN: clipboard still holds the previous post's link; copy failed")
        url = ""
    elif url:
        _last_url = url
    close_sheets(d)  # sheet usually closes itself after Copy link; make sure
    _back_to_feed(d)
    m = SELECTORS["permalink"].match(url)
    if not m:
        log("WARN: clipboard did not contain a permalink:", repr(url[:80]))
    return f"https://www.instagram.com/{m.group('type')}/{m.group('code')}/" if m else None


def post_id(p):
    """Cheap identity for the first-pass 'have we stored this' check. Must not depend on anything
    that changes while the post sits in the feed: like counts, relative dates, carousel index."""
    # Caption if there is one, else the media description up to the first comma ("Photo  of  by X").
    key = p["caption"][:200] if p["caption"] else re.sub(r"\d+", "", p["alt"].split(",")[0])
    # No kind here: a header-less video card has no media description to infer it from.
    raw = f"{p['username']}|{key}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def crop_media(d, bounds: str, pid: str, clip_top: int = 0):
    """Screenshot the visible post image. Returns filename or None."""
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds or "")
    if not m:
        return None
    x1, y1, x2, y2 = map(int, m.groups())
    w, h = d.window_size()
    full = y2 - y1
    y1, y2 = max(y1, clip_top), min(y2, h)  # trim the floating action bar / screen edge
    if full < 200 or (y2 - y1) < 0.4 * full:
        log(f"no crop: media bounds {bounds} mostly off-screen")
        return None
    img: Image.Image = d.screenshot()
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    fn = f"{pid}.jpg"
    img.crop((x1, y1, x2, y2)).convert("RGB").save(MEDIA_DIR / fn, quality=85)
    return fn


def _prune_old_posts(con):
    """Delete posts older than RETAIN_DAYS (0 disables) and any media file no row references any
    more, so disk use stays flat instead of growing forever."""
    if RETAIN_DAYS > 0:
        cutoff = (datetime.now(UTC) - timedelta(days=RETAIN_DAYS)).isoformat()
        gone = con.execute(
            "SELECT media_file FROM posts"
            " WHERE COALESCE(posted_at, scraped_at) < ? AND media_file IS NOT NULL",
            (cutoff,),
        ).fetchall()
        cur = con.execute("DELETE FROM posts WHERE COALESCE(posted_at, scraped_at) < ?", (cutoff,))
        con.commit()
        for (fn,) in gone:
            (MEDIA_DIR / fn).unlink(missing_ok=True)
        if cur.rowcount:
            log(f"retention: removed {cur.rowcount} post(s) older than {RETAIN_DAYS}d")
    if not MEDIA_DIR.exists():
        return
    kept = {r[0] for r in con.execute("SELECT media_file FROM posts WHERE media_file IS NOT NULL")}
    # Only ever written media_file names are *.jpg (crop_media()); restrict the sweep to those so
    # pointing MEDIA_DIR at the wrong directory can't delete unrelated files.
    orphans = [f for f in MEDIA_DIR.glob("*.jpg") if f.name not in kept]
    for f in orphans:
        f.unlink(missing_ok=True)
    if orphans:
        log(f"retention: removed {len(orphans)} orphaned media file(s)")


def scrape_once(d, con):
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    global _last_url
    try:
        _last_url = d.clipboard or ""  # whatever is there now is stale by definition
    except Exception:
        _last_url = ""
    open_following_feed(d)
    new, seen_streak, this_run = 0, 0, set()
    link_failures: dict[str, int] = {}  # hash -> failed share-sheet attempts
    screens = 0
    while screens < MAX_SCROLLS:
        xml = d.dump_hierarchy()
        posts = parse_hierarchy(xml)
        if screens == 0 and not posts:
            _dump_debug(d, "last", xml=xml)
            log("no posts parsed on first screen — selectors probably need updating; dump saved")
        touched = False
        for p in posts:
            h = post_id(p)
            if h in this_run:
                continue  # still on screen from the previous scroll
            if not p["complete"]:
                continue  # wait until the whole bottom of the card is on screen (stable identity)
            if con.execute("SELECT 1 FROM posts WHERE hash=? OR id=?", (h, h)).fetchone():
                this_run.add(h)
                seen_streak += 1
                continue
            media = crop_media(d, p["bounds"], h, p.get("clip_top", 0))  # before any sheet opens
            if not media and not p["bounds"]:
                log("no crop: media node not found for card")
            url = fetch_permalink(d, h)
            touched = True
            if not url and link_failures.get(h, 0) < 1:
                # The sheet sometimes fails to open; try once more on the next screen.
                link_failures[h] = link_failures.get(h, 0) + 1
                if media:
                    (MEDIA_DIR / media).unlink(missing_ok=True)
                break
            this_run.add(h)
            if _on_home_feed(d) or not _on_feed(d):
                log("WARN: not on the Following feed any more; reopening it")
                open_following_feed(d)
                this_run.discard(h)  # let the card be handled again where it appears
            pid = url.rstrip("/").rsplit("/", 1)[-1] if url else h
            row = con.execute("SELECT username FROM posts WHERE id=?", (pid,)).fetchone() if url else None
            if row and row[0] != p["username"]:
                log(
                    f"WARN: permalink {pid} belongs to {row[0]}, not {p['username']}; stale clipboard, dropping it"
                )
                url, pid = None, h
            elif row:
                seen_streak += 1  # same post, caption edited since we stored it
                con.execute("UPDATE posts SET hash=? WHERE id=?", (h, pid))
                con.commit()
                if media:
                    (MEDIA_DIR / media).unlink(missing_ok=True)
                break
            seen_streak = 0
            store_caption = p["caption"] or p["alt"]
            parsed = parse_posted_at(p["posted_date"], datetime.now(UTC))
            posted_at, posted_at_prec = parsed if parsed else (None, None)
            dup = _find_duplicate(con, p["username"], posted_at, posted_at_prec, store_caption)
            if dup:
                final_id, fields, media_to_drop = _merged_fields(
                    dup, pid, url, h, p["kind"], p["posted_date"], p["place"], store_caption, media, posted_at
                )
                if media_to_drop:
                    (MEDIA_DIR / media_to_drop).unlink(missing_ok=True)
                _write_merged(con, dup["id"], final_id, fields)
                con.commit()
                seen_streak += 1
                log(f"merged duplicate: {p['username']} -> {final_id}")
                break
            if media and pid != h:
                (MEDIA_DIR / media).rename(MEDIA_DIR / f"{pid}.jpg")
                media = f"{pid}.jpg"
            con.execute(
                "INSERT INTO posts (id, username, kind, posted_date, caption, media_file, scraped_at,"
                " hash, url, place, posted_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    pid,
                    p["username"],
                    p["kind"],
                    p["posted_date"],
                    store_caption,
                    media,
                    datetime.now(UTC).isoformat(),
                    h,
                    url,
                    p["place"],
                    posted_at.isoformat() if posted_at else None,
                ),
            )
            con.commit()
            new += 1
            log(f"new post: {p['username']} ({p['kind']}) {p['posted_date']} {url or '(no permalink)'}")
            break  # the screen may have shifted; re-dump before handling the next card
        if touched:
            continue
        log(f"screen {screens}: {len(posts)} cards, {new} new so far, seen-streak {seen_streak}")
        if seen_streak >= STOP_AFTER_SEEN:
            log("hit already-seen posts; stopping")
            break
        human_scroll(d)
        human_pause(1.5, 4.0)
        screens += 1
    _prune_old_posts(con)
    # Leave the app in a natural state
    d.press("home")
    return new


def main():
    con = db_init()
    while True:
        try:
            d = connect_device()
            n = scrape_once(d, con)
            log(f"run complete: {n} new posts")
        except Exception as e:  # keep the loop alive; log for debugging
            log("ERROR:", repr(e))
        hours = random.uniform(POLL_MIN_H, POLL_MAX_H)
        log(f"sleeping {hours:.2f}h")
        time.sleep(hours * 3600)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "once":
        con = db_init()
        print(scrape_once(connect_device(), con), "new posts")
    elif len(sys.argv) > 1 and sys.argv[1] == "login":
        print("logged in:", ensure_logged_in(connect_device()))
    elif len(sys.argv) > 1 and sys.argv[1] == "dump":
        d = connect_device()
        _dump_debug(d, "manual")
        print("wrote", DEBUG_DIR)
    else:
        main()
