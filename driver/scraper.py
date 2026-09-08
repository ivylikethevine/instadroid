"""
Instagram -> SQLite scraper driving a real Instagram app inside redroid via uiautomator2.

Strategy: open the chronological "Following" feed, scroll slowly, parse the accessibility
tree for post cards, store new ones, stop once we hit posts we've already seen.

Selectors live in the SELECTORS dict below. Instagram changes its UI a few times a year;
when a run fails, look at the hierarchy dump in $DEBUG_DIR and adjust them.
"""
import hashlib
import io
import os
import random
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import adbutils
import uiautomator2 as u2
from lxml import etree
from PIL import Image

ADB_ADDR = os.environ.get("ADB_ADDR", "redroid:5555")
DB_PATH = os.environ.get("DB_PATH", "/db/posts.sqlite")
MEDIA_DIR = Path(os.environ.get("MEDIA_DIR", "/media"))
DEBUG_DIR = Path(os.environ.get("DEBUG_DIR", "/debug"))
POLL_MIN_H = float(os.environ.get("POLL_MIN_HOURS", "2.5"))
POLL_MAX_H = float(os.environ.get("POLL_MAX_HOURS", "4.5"))
MAX_SCROLLS = int(os.environ.get("MAX_SCROLLS", "25"))
STOP_AFTER_SEEN = int(os.environ.get("STOP_AFTER_SEEN", "4"))
IG_USERNAME = os.environ.get("IG_USERNAME", "")
IG_PASSWORD = os.environ.get("IG_PASSWORD", "")
IG_PKG = "com.instagram.android"

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
    "media_ids": ("carousel_media_group", "media_group", "row_feed_photo_imageview", "zoomable_view_container"),
    # Content-desc on the media itself ("Reel by Nykky Hex, Liked by ..., August 29" / "Photo 1 of 7 by ...").
    "media_alt": re.compile(r"^(Photo|Video|Reel|Image|Carousel)\b", re.I),
    "username_id": "row_feed_photo_profile_name",
    # The "Home ⌄" title button at the top of the feed opens the Following/Favorites chooser.
    "feed_switcher_desc": "Instagram Home Feed",
    "following_text": "Following",
    # Login screen. Instagram renames these occasionally; several candidates each.
    "login_username_ids": ["login_username", "username"],
    "login_username_hints": ["Mobile number or email", "Username, email or mobile number", "Phone number, username or email", "Username, email address or mobile number"],
    "login_password_ids": ["password"],
    "login_password_hints": ["Password"],
    "login_button_texts": ["Log in", "Log In"],
    "login_page_markers": ["Log in", "Log In", "Forgot password?"],
    # Post-login interstitials and the buttons that dismiss them.
    "dismiss_texts": ["Not now", "Not Now", "Skip", "Save", "Continue", "Don’t allow", "Cancel", "OK"],
    # Anything matching these means a human has to intervene.
    "challenge_texts": ["confirmation code", "Confirm it's you", "Suspicious login", "security code",
                        "Enter the code", "We Detected An Unusual Login", "Help us confirm it's you"],
}
# ------------------------------------------------------------------------------------


def log(*a):
    print(datetime.now().strftime("%H:%M:%S"), *a, flush=True)


def db_init():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
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
    con.commit()
    return con


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
    y2 = y1 - random.randint(int(h * 0.35), int(h * 0.55))
    d.swipe(x, y1, x, y2, duration=random.uniform(0.25, 0.6))


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


def _dump_debug(d, name):
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    (DEBUG_DIR / f"{name}_hierarchy.xml").write_text(d.dump_hierarchy())
    d.screenshot().save(DEBUG_DIR / f"{name}_screen.png")


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
    if not _first(d, text=SELECTORS["login_username_hints"]) and not _first(d, text=SELECTORS["login_password_hints"]):
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
        ok.click(); human_pause()
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
    user_field.click(); human_pause(0.5, 1.2)
    user_field.set_text(IG_USERNAME); human_pause(1, 2)
    pw_field.click(); human_pause(0.5, 1.2)
    pw_field.set_text(IG_PASSWORD); human_pause(1, 2)
    btn = _first(d, description=SELECTORS["login_button_texts"]) or _first(d, text=SELECTORS["login_button_texts"])
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


def open_following_feed(d):
    ensure_logged_in(d)
    if d.app_current().get("package") != IG_PKG:
        d.app_start(IG_PKG, stop=False)
        human_pause(3, 5)
    _dismiss_interstitials(d)  # notification / location / "set up on new device" prompts
    # The title bar hides while scrolled; pull back to the top until the switcher is visible.
    w, h = d.window_size()
    sw = d(description=SELECTORS["feed_switcher_desc"])
    for _ in range(12):
        if sw.exists(timeout=1):
            break
        d.swipe(w // 2, int(h * 0.3), w // 2, int(h * 0.8), duration=0.3)
        human_pause(0.8, 1.5)
    if sw.exists(timeout=3):
        sw.click()
        human_pause(2, 3)
        f = d(text=SELECTORS["following_text"])
        if f.exists(timeout=5):
            f.click()
            human_pause(3, 5)
            return True
        d.press("back")
    log("WARN: could not open Following feed; scraping whatever feed is showing")
    return False


def _card_of(node):
    """Walk up to the feed RecyclerView's direct child that contains this node."""
    n = node
    while n is not None:
        parent = n.getparent()
        if parent is not None and parent.get("resource-id") == "android:id/list":
            return n
        n = parent
    return node.getparent()


def parse_hierarchy(xml: str):
    """Return a list of post dicts found in the current screen's accessibility tree."""
    root = etree.fromstring(xml.encode())
    posts = []
    for hdr in root.iter("node"):
        if not (hdr.get("resource-id") or "").endswith(SELECTORS["header_id"]):
            continue
        m = SELECTORS["header_desc"].match(hdr.get("content-desc") or "")
        if not m:
            continue  # sponsored / suggested cards have a different header
        card = _card_of(hdr)
        media_bounds, alt, caption = None, "", ""
        for n in card.iter("node"):
            rid = (n.get("resource-id") or "").split("/")[-1]
            desc = n.get("content-desc") or ""
            text = n.get("text") or ""
            if media_bounds is None and rid in SELECTORS["media_ids"]:
                media_bounds = n.get("bounds")
            if not alt and SELECTORS["media_alt"].match(desc):
                alt = desc
            if not caption and text and n.get("class") == "android.view.ViewGroup" and rid == "":
                caption = text
        posts.append({
            "kind": m.group("kind").lower(),
            "username": m.group("user"),
            "posted_date": m.group("date"),
            "place": m.group("place") or "",
            "caption": caption,
            "bounds": media_bounds,
            "alt": alt,
        })
    return posts


def post_id(p):
    alt = re.sub(r"\d+", "", p["alt"])  # strip like/comment counts
    raw = f'{p["username"]}|{p["kind"]}|{p["caption"][:200]}|{alt[:120]}'
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def crop_media(d, bounds: str, pid: str):
    """Screenshot the visible post image. Returns filename or None."""
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds or "")
    if not m:
        return None
    x1, y1, x2, y2 = map(int, m.groups())
    w, h = d.window_size()
    if y1 < 0 or y2 > h or (y2 - y1) < 200:
        return None  # partially off-screen; skip rather than save a sliver
    img: Image.Image = d.screenshot()
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    fn = f"{pid}.jpg"
    img.crop((x1, y1, x2, y2)).convert("RGB").save(MEDIA_DIR / fn, quality=85)
    return fn


def scrape_once(d, con):
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    open_following_feed(d)
    new, seen_streak, this_run = 0, 0, set()
    for i in range(MAX_SCROLLS):
        xml = d.dump_hierarchy()
        posts = parse_hierarchy(xml)
        if i == 0 and not posts:
            (DEBUG_DIR / "last_hierarchy.xml").write_text(xml)
            d.screenshot().save(DEBUG_DIR / "last_screen.png")
            log("no posts parsed on first screen — selectors probably need updating; dump saved")
        for p in posts:
            pid = post_id(p)
            if pid in this_run:
                continue  # still on screen from the previous scroll
            this_run.add(pid)
            if con.execute("SELECT 1 FROM posts WHERE id=?", (pid,)).fetchone():
                seen_streak += 1
                continue
            seen_streak = 0
            media = crop_media(d, p["bounds"], pid)
            con.execute(
                "INSERT INTO posts VALUES (?,?,?,?,?,?,?)",
                (pid, p["username"], p["kind"], p["posted_date"], p["caption"] or p["alt"],
                 media, datetime.now(timezone.utc).isoformat()),
            )
            con.commit()
            new += 1
            log(f"new post: {p['username']} ({p['kind']}) {p['posted_date']}")
        if seen_streak >= STOP_AFTER_SEEN:
            log("hit already-seen posts; stopping")
            break
        human_scroll(d)
        human_pause(1.5, 4.0)
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
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        (DEBUG_DIR / "manual_hierarchy.xml").write_text(d.dump_hierarchy())
        d.screenshot().save(DEBUG_DIR / "manual_screen.png")
        print("wrote", DEBUG_DIR)
    else:
        main()
