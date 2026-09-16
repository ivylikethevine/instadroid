"""Getting around Instagram: login, the Following/Home feeds, sheets, and the Following list."""

import sqlite3
import time
from datetime import UTC, datetime

from . import config, device, diagnostics, install, parsing, uidevice
from .common import log
from .device import DeviceNotReady
from .versioning import SELECTORS, versioned


def _prepare_app(d: uidevice.Device) -> None:
    """The common start of every navigation: logged in, in front, prompts dismissed, no sheet open."""
    ensure_logged_in(d)
    device.ensure_foreground(d)
    _dismiss_interstitials(d)  # notification / location / "set up on new device" prompts
    close_sheets(d)


@versioned
def _challenge_present(d: uidevice.Device) -> str | None:
    for t in SELECTORS["challenge_texts"]:
        if d(textContains=t).exists(timeout=0.5):
            return t
    return None


@versioned
def _dismiss_interstitials(d: uidevice.Device, rounds: int = 4) -> None:
    for _ in range(rounds):
        btn = device.first(d, text=SELECTORS["dismiss_texts"])
        if not btn:
            return
        btn.click()
        device.human_pause(1.5, 3)


@versioned
def _login_form(d: uidevice.Device) -> tuple[uidevice.Selector, uidevice.Selector] | None:
    """Return (username_field, password_field) or None. Instagram's login screen is Jetpack
    Compose: the labels are plain Views and the two EditTexts carry no id, so we go by order."""
    if not device.first(d, text=SELECTORS["login_username_hints"]) and not device.first(
        d, text=SELECTORS["login_password_hints"]
    ):
        return None
    edits = d(className="android.widget.EditText")
    if edits.count < 2:
        return None
    return edits[0], edits[1]


def _package_present(d: uidevice.Device) -> bool:
    """A second opinion before anything is downloaded and installed over what may be a live login:
    `pm path` lists the installed APKs. `app_list()` goes through uiautomator2's own service, which
    has answered wrongly while the device was still settling; only when both say it's missing is it."""
    try:
        present = "package:" in (d.shell(["pm", "path", config.IG_PKG]).output or "")
    except Exception:
        return False
    if present:
        log(f"WARN: app_list() didn't list {config.IG_PKG} but `pm path` does; not reinstalling")
    return present


@versioned
def ensure_logged_in(d: uidevice.Device) -> None:
    """If the login screen is showing, fill credentials from the environment and log in.

    Returns once we are (or became) logged in. Raises RuntimeError on a 2FA/challenge
    screen so the caller can abort and a human can finish it.
    """
    if config.IG_PKG not in d.app_list() and not _package_present(d):
        if not config.IG_AUTO_INSTALL:
            raise RuntimeError(f"{config.IG_PKG} is not installed on the device; adb install it first")
        log(f"{config.IG_PKG} not installed; fetching and installing")
        install.install_instagram(d)
        if config.IG_PKG not in d.app_list():
            raise DeviceNotReady(f"{config.IG_PKG} still not present after install")
    device.launch_app(d)
    device.human_pause(4, 6)
    for attempt in range(3):
        if device.in_foreground(d):
            break
        log(f"WARN: {config.IG_PKG} not foregrounded yet (attempt {attempt}); retrying launch")
        device.launch_app(d)
        device.human_pause(3, 5)
    else:
        # Without this check, every screen-detection call below trivially finds nothing (we're
        # still on the home screen) and the function falls through to "no login screen; assume
        # session is live" — a false positive that leaves the caller thinking it's logged in.
        diagnostics.dump_debug(d, "login")
        raise DeviceNotReady(f"could not bring {config.IG_PKG} to the foreground; see {config.DEBUG_DIR}")
    # Stray "Enter your password" style alert from a previous attempt.
    ok = d(text=SELECTORS["stray_alert_ok_text"])
    if ok.exists(timeout=1):
        ok.click()
        device.human_pause()
    if c := _challenge_present(d):
        diagnostics.dump_debug(d, "login")
        raise RuntimeError(f"Instagram wants a human: '{c}' screen; see {config.DEBUG_DIR}")
    existing = d(text=SELECTORS["welcome_existing_profile_text"])
    if existing.exists(timeout=1):
        # Logged-out "Join Instagram" welcome screen (fresh install, or an invalidated session) —
        # neither a login form nor a login_page_marker, so it must be tapped through first or the
        # check below mistakes it for an already-live session.
        log("logged-out welcome screen detected; tapping through to the login form")
        existing.click()
        device.human_pause(1.5, 2.5)
    form = _login_form(d)
    if not form:
        if device.first(d, text=SELECTORS["login_page_markers"]):
            diagnostics.dump_debug(d, "login")
            raise RuntimeError(f"login page shown but form not recognised; see {config.DEBUG_DIR}")
        # No login screen, so the session is live, provided Instagram is still up: a build that
        # crashes at startup is in front just long enough for the check above, then gone (seen live
        # with 400.0.0.49.68), and every check since found nothing because nothing was there.
        if not device.in_foreground(d):
            diagnostics.dump_debug(d, "login")
            raise DeviceNotReady(
                f"{config.IG_PKG} left the foreground right after launch (crashing at startup?)"
            )
        return
    if not (config.IG_USERNAME and config.IG_PASSWORD):
        diagnostics.dump_debug(d, "login")
        raise RuntimeError("login screen shown but IG_USERNAME/IG_PASSWORD not set")
    diagnostics.capture_screen(d, "login")  # before typing, so no credentials end up in it
    user_field, pw_field = form
    log("login screen detected; entering credentials as", config.IG_USERNAME)
    user_field.click()
    device.human_pause(0.5, 1.2)
    user_field.set_text(config.IG_USERNAME)
    device.human_pause(1, 2)
    pw_field.click()
    device.human_pause(0.5, 1.2)
    pw_field.set_text(config.IG_PASSWORD)
    device.human_pause(1, 2)
    btn = device.first(d, description=SELECTORS["login_button_texts"]) or device.first(
        d, text=SELECTORS["login_button_texts"]
    )
    if btn:
        btn.click()
    else:
        d.press("enter")
    log("submitted login; waiting")
    device.human_pause(10, 14)
    if c := _challenge_present(d):
        diagnostics.dump_debug(d, "login")
        raise RuntimeError(f"Instagram wants a human: '{c}' screen; see {config.DEBUG_DIR}")
    _dismiss_interstitials(d)
    if _login_form(d):
        diagnostics.dump_debug(d, "login")
        raise RuntimeError(f"still on login screen after submit (wrong password?); see {config.DEBUG_DIR}")
    log("logged in")


@versioned
def _on_following_feed(d: uidevice.Device) -> bool:
    t = d(resourceIdMatches=f".*:id/{SELECTORS['following_title_id']}$", text=SELECTORS["following_text"])
    return t.exists(timeout=1)


@versioned
def open_following_feed(d: uidevice.Device) -> bool:
    _prepare_app(d)
    w, h = device.window_size(d)
    sw = d(description=SELECTORS["feed_switcher_desc"])
    for attempt in range(4):
        # The action bar hides while scrolled; pull back to the top so we can see where we are.
        for _ in range(12):
            if sw.exists(timeout=1) or _on_following_feed(d):
                break
            d.swipe(w // 2, int(h * 0.3), w // 2, int(h * 0.8), duration=0.3)
            device.human_pause(0.8, 1.5)
        if _on_following_feed(d):
            if attempt > 0:
                diagnostics.capture_screen(d, "following_feed")
                return True  # we navigated here a moment ago; the screen just took a while
            # Left over from the last run: leave and re-enter so the feed is fresh.
            d.press("back")
            device.human_pause(2, 3)
            continue
        if sw.exists(timeout=3):
            try:
                f = d(text=SELECTORS["following_text"])
                for tap in range(4):  # taps get swallowed while the app is still warming up
                    sw.click()
                    if f.exists(timeout=5):
                        diagnostics.capture_screen(d, "feed_switch_menu")
                        break
                    log(f"feed switch attempt {attempt}: switcher tap {tap} opened nothing")
                if f.exists(timeout=1):
                    f.click()
                    device.human_pause(3, 5)
                    for _ in range(6):  # cold starts can take a while to build the screen
                        if _on_following_feed(d):
                            diagnostics.capture_screen(d, "following_feed")
                            return True
                        time.sleep(2)
                    log(f"feed switch attempt {attempt}: clicked Following but title not found")
                else:
                    diagnostics.dump_debug(d, f"feed_switch_menu{attempt}")
                    d.press("back")
            except Exception as e:  # the header can scroll away between exists() and click()
                log("WARN: feed switcher click failed, retrying:", repr(e))
        else:
            d.press("back")  # some other screen; step out and retry
            device.human_pause(1, 2)
    log("WARN: could not open Following feed; scraping whatever feed is showing (dump saved)")
    diagnostics.dump_debug(d, "feed_switch")
    return False


@versioned
def open_home_feed(d: uidevice.Device) -> bool:
    """The FEED_MODE=home alternative to open_following_feed(): navigate to (and stay on) the
    algorithmic Home feed. No switcher involved — just the bottom tab bar's own Home tab, tapped
    directly.

    Deliberately does NOT mirror open_following_feed()'s "leave and re-enter if already there" —
    confirmed live (2026-09-11): Home is the root of the app's back stack, so pressing back from
    it doesn't refresh anything, it triggers Android's "tap again to exit" and risks actually
    exiting the app on a second back press soon after. Already being on Home just means done."""
    _prepare_app(d)
    tab = d(resourceIdMatches=f".*:id/{SELECTORS['home_tab_id']}$")
    for attempt in range(4):
        if on_home_feed(d):
            return True
        if tab.exists(timeout=3):
            try:
                tab.click()
                device.human_pause(1.5, 2.5)
                for _ in range(6):  # cold starts can take a while to build the screen
                    if on_home_feed(d):
                        return True
                    time.sleep(2)
                log(f"open home feed attempt {attempt}: tapped Home tab but feed not found")
            except Exception as e:  # the tab bar can be mid-transition between exists() and click()
                log("WARN: home tab click failed, retrying:", repr(e))
        else:
            d.press("back")  # some other screen; step out and retry
            device.human_pause(1, 2)
    log("WARN: could not open Home feed (dump saved)")
    diagnostics.dump_debug(d, "home_feed_open")
    return False


def on_target_feed(d: uidevice.Device) -> bool:
    """True when the screen currently showing is the specific feed FEED_MODE selects, not just
    any feed at all — the Following and Home feeds are the only two the scraper ever intends to be
    on, and on_home_feed()'s bottom-tab-bar check tells them apart."""
    if not on_feed(d):
        return False
    return on_home_feed(d) if config.FEED_MODE == "home" else not on_home_feed(d)


def open_target_feed(d: uidevice.Device) -> None:
    """Navigate to whichever feed FEED_MODE selects — what the scrape run and stories call to get
    onto (or back onto) the feed, so neither has to know which mode it's in beyond this dispatch. A
    failure to get there is logged (with a dump) by the feed's own opener; the run carries on."""
    if config.FEED_MODE == "home":
        open_home_feed(d)
    else:
        open_following_feed(d)


@versioned
def _on_following_list(d: uidevice.Device) -> bool:
    """The Following-list screen (reached via own profile -> "N following"), independent of
    whether the list itself has any rows on screen yet — this is the screen's own view pager,
    present as soon as the screen loads."""
    return d(resourceIdMatches=f".*:id/{SELECTORS['following_list_screen_id']}$").exists(timeout=2)


@versioned
def open_own_following_list(d: uidevice.Device) -> bool:
    """Navigate from wherever the app is to the logged-in account's own Following list. Returns
    True once the list screen is confirmed on screen, False if navigation failed after a few
    attempts (dump saved) — mirrors open_following_feed()'s shape.

    Confirmed live against a real device (2026-09-11): if the app is already sitting on the list
    screen — left over from an earlier run, or from Android simply not having killed the activity
    — that scroll position is wherever the list was last left, not the top. Silently accepting it
    as "already there" made a second refresh collect only 9 of 30 followed accounts instead of a
    fresh scroll's 27+. So being on the screen already is never treated as done: leave (two backs,
    same as open_following_feed()'s own "leave and re-enter so the feed is fresh") and navigate
    back in via the normal tab -> link path, which always starts the list at row 0."""
    _prepare_app(d)
    tab = d(resourceIdMatches=f".*:id/{SELECTORS['profile_tab_id']}$")
    for attempt in range(4):
        if _on_following_list(d):
            d.press("back")
            d.press("back")
            device.human_pause(2, 3)
            continue
        if not tab.exists(timeout=3):
            d.press("back")  # some other screen (e.g. left inside the list from a prior attempt)
            device.human_pause(1, 2)
            continue
        try:
            tab.click()
            device.human_pause(1.5, 2.5)
            diagnostics.capture_screen(d, "profile")
            link = d(resourceIdMatches=f".*:id/{SELECTORS['following_link_id']}$")
            if not link.exists(timeout=5):
                log(f"open following list attempt {attempt}: following link not found on profile")
                diagnostics.dump_debug(d, f"following_list_profile{attempt}")
                d.press("back")
                continue
            link.click()
            device.human_pause(1.5, 2.5)
            if _on_following_list(d):
                return True
            log(f"open following list attempt {attempt}: list screen not detected after tap")
        except Exception as e:  # a node can scroll/disappear between exists() and click()
            log("WARN: following-list navigation click failed, retrying:", repr(e))
    log("WARN: could not open own Following list (dump saved)")
    diagnostics.dump_debug(d, "following_list_open")
    return False


@versioned
def scrape_following_list(d: uidevice.Device) -> list[str] | None:
    """Scroll the already-open Following list from wherever it starts, collecting every distinct
    username, and return them in first-seen order. Stops after FOLLOWING_LIST_EMPTY_LIMIT
    consecutive screens with no new username (list exhausted), same shape as the main feed's
    empty-streak detection. Returns None rather than [] when nothing was collected at all — almost
    certainly a navigation/selector failure, not "this account follows nobody" — so the caller can
    tell the two apart and refuse to replace a possibly-good existing list with an empty one."""
    collected: dict[str, None] = {}  # dict for its insertion-order + O(1) membership
    screens = empty_streak = 0
    while screens < config.MAX_FOLLOWING_SCROLLS:
        xml = d.dump_hierarchy()
        diagnostics.capture_screen(d, "following_list", xml)
        names = parsing.parse_following_list(xml)
        if screens == 0 and not names:
            diagnostics.dump_debug(d, "following_list_first", xml=xml)
            log("no usernames parsed on first Following-list screen — selectors probably need updating")
        new = 0
        for n in names:
            if n not in collected:
                collected[n] = None
                new += 1
        empty_streak = 0 if new else empty_streak + 1
        if empty_streak >= config.FOLLOWING_LIST_EMPTY_LIMIT:
            break
        device.human_scroll_list(d)
        device.human_pause(config.SCROLL_PAUSE_MIN, config.SCROLL_PAUSE_MAX)
        screens += 1
    return list(collected) if collected else None


def refresh_following_list(d: uidevice.Device, con: sqlite3.Connection) -> None:
    """Navigate to the own Following list, scrape it in full, and replace the stored allowlist
    with exactly what was found — so an unfollow is reflected simply by that username's row no
    longer existing after this runs. If the refresh fails (nothing collected, or navigation never
    reached the list), the existing stored list (if any) is left untouched rather than wiped, and
    db.needs_following_refresh() will keep returning True so the next run tries again."""
    if not open_own_following_list(d):
        return
    usernames = scrape_following_list(d)
    if not usernames:
        log("WARN: following-list refresh collected nothing; keeping the existing list")
        return
    now_iso = datetime.now(UTC).isoformat()
    con.execute("DELETE FROM following")
    con.executemany(
        "INSERT INTO following (username, updated_at) VALUES (?, ?)",
        [(u, now_iso) for u in usernames],
    )
    con.commit()
    log(f"following list refreshed: {len(usernames)} accounts")


@versioned
def _sheet_open(d: uidevice.Device) -> bool:
    if d(description=SELECTORS["copy_link_desc"]).exists(timeout=0.3):
        return True
    if any(d(text=t).exists(timeout=0.3) for t in SELECTORS["sheet_markers_text"]):
        return True
    return any(d(description=t).exists(timeout=0.3) for t in SELECTORS["sheet_markers_desc"])


@versioned
def close_sheets(d: uidevice.Device, max_back: int = 2) -> bool:
    """Back out of any open share/bottom sheet without touching its contents."""
    for i in range(max_back):
        if not _sheet_open(d) or not device.in_foreground(d):
            break
        if i > 0 and on_feed(d):
            break  # feed rows visible: the marker is a false positive, another Back would exit
        d.press("back")
        device.human_pause(1.5, 2)
    if device.ensure_foreground(d):
        log("WARN: left Instagram while closing a sheet; relaunched")
    return not _sheet_open(d)


@versioned
def on_feed(d: uidevice.Device) -> bool:
    """True when a feed list with post rows is showing (title bars hide while scrolled, so
    they are not a reliable signal)."""
    return d(resourceIdMatches=f".*:id/{SELECTORS['share_id']}").exists(timeout=0.5) or d(
        resourceIdMatches=f".*:id/{SELECTORS['header_id']}"
    ).exists(timeout=0.5)


@versioned
def on_home_feed(d: uidevice.Device) -> bool:
    """The Home feed keeps the bottom tab bar; the Following screen does not."""
    return d(resourceIdMatches=f".*:id/{SELECTORS['home_tab_id']}").exists(timeout=0.5)


@versioned
def back_to_feed(d: uidevice.Device, tries: int = 2) -> bool:
    """If a tap opened a profile/hashtag/etc., back out until a feed is showing again. Never
    backs out of the app: if we somehow left it, relaunch instead."""
    for _ in range(tries):
        if device.ensure_foreground(d):
            return on_feed(d)
        if on_feed(d) and not _sheet_open(d):
            return True
        d.press("back")
        device.human_pause(1, 1.5)
    return on_feed(d)
