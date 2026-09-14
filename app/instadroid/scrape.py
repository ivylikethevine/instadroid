"""One scrape run (scrape_once) and the long-running poll loop (main)."""

import sqlite3
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import NotRequired, TypedDict
from urllib.parse import urlsplit

import uiautomator2 as u2

from . import (
    capture,
    common,
    config,
    db,
    device,
    diagnostics,
    navigation,
    parsing,
    retention,
    stories,
    versioning,
)
from .common import log


def _startup_wait_seconds(con: sqlite3.Connection, now: datetime | None = None) -> float:
    """Seconds to wait before the first scrape after the process starts: whatever's left of the
    interval since the last recorded run finished. That interval is the first RETRY_DELAYS_MINUTES
    entry if the last run failed transiently (the retry it would have had), otherwise a normally
    sampled poll interval. 0 with no recorded run, SCRAPE_ON_STARTUP set, or an unreadable row.

    Without this, every `docker compose up`, recreate or crash-restart scraped immediately: three runs
    landed within 40 minutes on 2026-09-11, and with `restart: unless-stopped` a restart loop would
    become a scrape loop."""
    if config.SCRAPE_ON_STARTUP:
        return 0.0
    try:
        row = con.execute("SELECT finished_at, error FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    except sqlite3.Error:
        return 0.0
    if not row:
        return 0.0
    finished = common.parse_iso(row[0])
    if finished is None:
        return 0.0
    error = row[1] or ""
    if error and config.RETRY_DELAYS_MINUTES and error.split("(", 1)[0] in device.transient_error_names():
        interval = config.RETRY_DELAYS_MINUTES[0] * 60
    else:
        interval = device.sample_duration(config.POLL_MIN_H, config.POLL_MAX_H) * 3600
    elapsed = ((now or datetime.now(UTC)) - finished).total_seconds()
    return max(0.0, interval - elapsed)


def next_sleep_seconds(error: BaseException | None, attempt: int) -> tuple[float, int]:
    """(seconds to sleep before the next run, updated retry count). A transient failure retries
    after RETRY_DELAYS_MINUTES[attempt] (jittered up to +50%) until the list runs out; anything
    else — success, a non-transient error, retries exhausted — sleeps a normal poll interval."""
    if error is not None and device.is_transient(error) and attempt < len(config.RETRY_DELAYS_MINUTES):
        minutes = config.RETRY_DELAYS_MINUTES[attempt]
        return device.sample_duration(minutes, minutes * 1.5) * 60, attempt + 1
    return device.sample_duration(config.POLL_MIN_H, config.POLL_MAX_H) * 3600, 0


class RunStats(TypedDict):
    """What one run reports; every key but `new` is a runs table column (see db.record_run())."""

    new: int
    new_stories: int
    link_sheet_failures: int
    link_clipboard_failures: int
    warning: str | None
    filtered_posts: int  # dropped by the followed-accounts allowlist
    cards_per_screen: float  # the selector-drift canary's inputs, see db.check_selector_drift()
    share_captioned: float
    share_complete: float
    mem_peak_mb: NotRequired[int | None]  # added by scrape_once()
    oom_kills: NotRequired[int | None]


def _redact_url(url: str) -> str:
    """scheme://host/path only, no query string — FRESHRSS_REFRESH_URL carries an auth token and
    must never land in the shared container log."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


def _ping_freshrss(new_posts: int, new_stories: int) -> str | None:
    """GET FRESHRSS_REFRESH_URL after a run that stored something new, so FreshRSS (or any reader
    with an equivalent refresh webhook) fetches immediately instead of waiting out its own poll
    interval. No-op when disabled or nothing new was stored. Best-effort like device.force_stop():
    returns a short error string rather than raising — a reader being unreachable must not fail a
    scrape that already succeeded. Returns None on a no-op or success."""
    if not config.FRESHRSS_REFRESH_URL or (new_posts + new_stories) == 0:
        return None
    url = config.FRESHRSS_REFRESH_URL
    if "ajax=" not in url:
        url += ("&" if "?" in url else "?") + "ajax=1"
    try:
        with urllib.request.urlopen(url, timeout=config.FRESHRSS_REFRESH_TIMEOUT) as resp:
            resp.read()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        error = f"FreshRSS refresh ping to {_redact_url(config.FRESHRSS_REFRESH_URL)} failed: {e!r}"
        log("WARN:", error)
        return error
    log(f"pinged FreshRSS refresh ({_redact_url(config.FRESHRSS_REFRESH_URL)})")
    return None


def scrape_once(d: u2.Device, con: sqlite3.Connection) -> RunStats:
    """One scrape run. Starts and ends with Instagram and the cached system apps force-stopped
    (_free_device_memory), the end in a `finally` so a run that raises halfway doesn't leave
    Instagram's ~800MiB resident until the next poll. Adds the run's redroid memory peak and OOM
    kill count (MemoryGuard) to the stats."""
    device.free_device_memory(d)
    guard = device.MemoryGuard(d)
    try:
        stats = _scrape_feed(d, con, guard)
    finally:
        device.free_device_memory(d)
    stats["mem_peak_mb"] = guard.peak_mb()
    stats["oom_kills"] = guard.oom_kills()
    if stats["oom_kills"]:
        warnings = [
            w for w in (stats["warning"], f"redroid OOM-killed {stats['oom_kills']} Android process(es)") if w
        ]
        stats["warning"] = "; ".join(warnings)
    return stats


def _store_post(
    d: u2.Device,
    con: sqlite3.Connection,
    p: parsing.Post,
    h: str,
    pid: str,
    url: str | None,
    media: str | None,
    extra_media: list[str],
    ig_version: str | None,
) -> bool:
    """Save a captured card (media cropped, permalink fetched, `pid` its final id): merge it into a
    stored duplicate (returns False) or insert it as a new post (True)."""
    if p["caption_truncated"]:
        p["caption"] = capture.expand_caption(d, p)
    parsed = parsing.parse_posted_at(p["posted_date"], datetime.now(UTC))
    posted_at, precision = parsed if parsed else (None, None)
    now = datetime.now(UTC)
    row: db.PostRow = {
        "id": pid,
        "username": p["username"],
        "kind": p["kind"],
        "posted_date": p["posted_date"],
        "caption": p["caption"] or p["alt"],
        "media_file": media,
        "scraped_at": now.isoformat(),
        "hash": h,
        "url": url,
        "place": p["place"],
        "posted_at": posted_at.isoformat() if posted_at else None,
        "updated_at": now.isoformat(),
        "ig_version": ig_version,
    }
    if dup := db.find_duplicate(con, p["username"], posted_at, precision, row["caption"]):
        merged, media_to_drop = db.merged_fields(dup, row, now)
        db.write_merged(con, dup["id"], merged)
        con.commit()
        # The stored cover wins, so this capture's extra slides are redundant too.
        retention.discard_media(media_to_drop, *extra_media)
        log(f"merged duplicate: {p['username']} -> {merged['id']}")
        return False
    if media and pid != h:  # crops were saved under the hash; name them after the permalink
        media, extra_media = _rename_media(pid, media, extra_media)
    row["media_file"] = media
    db.insert_row(con, "posts", row)
    con.executemany(
        "INSERT OR REPLACE INTO media (post_id, idx, file) VALUES (?, ?, ?)",
        [(pid, i, fn) for i, fn in enumerate(extra_media, start=1)],
    )
    con.commit()
    log(f"new post: {p['username']} ({p['kind']}) {p['posted_date']} {url or '(no permalink)'}")
    return True


def _rename_media(pid: str, media: str, extra_media: list[str]) -> tuple[str, list[str]]:
    """Rename a cover and its extra slides to pid.ext, pid_1.ext, ... and return the new names."""
    names = [
        f"{pid}{Path(media).suffix}",
        *(f"{pid}_{i}{Path(fn).suffix}" for i, fn in enumerate(extra_media, 1)),
    ]
    for old, new_name in zip([media, *extra_media], names, strict=True):
        (config.MEDIA_DIR / old).rename(config.MEDIA_DIR / new_name)
    return names[0], names[1:]


def _scrape_feed(d: u2.Device, con: sqlite3.Connection, guard: device.MemoryGuard) -> RunStats:
    config.DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    capture.reset_last_url(d)
    navigation.open_target_feed(d)
    # Read after opening the feed, not from main()'s snapshot: opening it can install Instagram
    # (ensure_logged_in's auto-install), which would leave an earlier reading stale or empty.
    ig_version = device.instagram_version(d)
    if config.FOLLOWING_REFRESH_DAYS and db.needs_following_refresh(con):
        try:
            navigation.refresh_following_list(d, con)
        except Exception as e:  # a profile/list-navigation surprise must not sink the whole run
            log("WARN: following-list refresh failed, continuing with the existing list:", repr(e))
        navigation.open_target_feed(d)  # back onto the feed screen the post loop expects
    followed: set[str] | None = None
    if config.FOLLOWING_REFRESH_DAYS:
        rows = con.execute("SELECT username FROM following").fetchall()
        if rows:  # empty/never-refreshed means "not initialized yet" -> filter nothing
            followed = {r[0] for r in rows}
    warnings: list[str] = []
    new_stories = 0
    if reason := guard.exceeded():
        log(f"WARN: {reason}; skipping stories")
        warnings.append(f"skipped stories: {reason}")
    else:
        try:
            new_stories = stories.scrape_stories(d, con)
        except Exception as e:  # a stories-viewer surprise must not sink the whole run
            log("WARN: story capture failed, continuing with posts:", repr(e))
            navigation.open_target_feed(d)  # best-effort recovery back onto the screen the post loop expects
    if new_stories:
        log(f"stories: {new_stories} new")
    new, seen_streak, this_run = 0, 0, set()
    link_failures: dict[str, int] = {}  # hash -> failed share-sheet attempts
    link_sheet_failures = link_clipboard_failures = 0
    accounts_seen: set[str] = set()  # usernames already upserted this run
    avatars_checked: set[str] = set()  # usernames whose avatar has been considered this run
    filtered_posts = 0  # dropped by the followed-accounts allowlist, if enabled
    screens = empty_streak = 0
    feed_reopened = False
    # Selector-drift canary inputs (see db.check_selector_drift()): every hierarchy dump counts
    # toward cards/screen, and every parsed card toward the captioned/complete shares, regardless
    # of the followed-accounts filter — this measures parse yield, not post-filter output.
    stat_dumps = stat_cards = stat_captioned = stat_complete = 0
    while screens < config.MAX_SCROLLS:
        if reason := guard.exceeded():
            log(f"WARN: {reason}; stopping the run early")
            warnings.append(f"stopped early: {reason}")
            break
        xml = d.dump_hierarchy()
        raw_posts = parsing.parse_hierarchy(xml)
        stat_dumps += 1
        stat_cards += len(raw_posts)
        stat_captioned += sum(1 for p in raw_posts if not parsing.is_weak_caption(p["caption"] or p["alt"]))
        stat_complete += sum(1 for p in raw_posts if p["complete"])
        posts = raw_posts
        if followed is not None:
            posts = [p for p in raw_posts if not p["username"] or p["username"] in followed]
            filtered_posts += len(raw_posts) - len(posts)
        if screens == 0 and not raw_posts:
            diagnostics.dump_debug(d, "last", xml=xml)
            log("no posts parsed on first screen — selectors probably need updating; dump saved")
        empty_streak = 0 if posts else empty_streak + 1
        if config.EMPTY_SCREEN_LIMIT and empty_streak >= config.EMPTY_SCREEN_LIMIT:
            # Scrolling on regardless was seen live: 2 cards on screen 0, then 24 straight empty
            # screens, with nothing saved to show what was on screen. Whatever it is (a screen we
            # landed on by accident, the end of the feed, a UI change), more swipes won't fix it:
            # reopen the feed once, and stop the run if that doesn't help either.
            diagnostics.dump_debug(d, f"empty_feed{int(feed_reopened)}", xml=xml)
            if feed_reopened:
                log(f"{empty_streak} empty screens again after reopening the feed; stopping (dump saved)")
                warnings.append(f"stopped early: {empty_streak} empty screens in a row after reopening")
                break
            log(f"{empty_streak} empty screens in a row; reopening the feed (dump saved)")
            warnings.append(f"reopened the feed after {empty_streak} empty screens in a row")
            feed_reopened, empty_streak = True, 0
            navigation.open_target_feed(d)
            continue
        for p in posts:
            u = p["username"]
            if not u:
                continue
            if u not in accounts_seen:
                accounts_seen.add(u)
                db.upsert_account(con, u)
            # Only mark an avatar "checked" once a header is actually on screen for this account —
            # the header-less top card (its header already scrolled off) would otherwise block a
            # retry for the rest of the run even though its avatar was never actually captured.
            if u not in avatars_checked and p["header_bounds"]:
                avatars_checked.add(u)
                if db.needs_avatar_refresh(con, u):
                    avatar = capture.capture_avatar(d, p["header_bounds"], u)
                    if avatar:
                        db.upsert_account(con, u, avatar)
        touched = False
        for p in posts:
            h = parsing.post_id(p)
            if h in this_run:
                continue  # still on screen from the previous scroll
            if not p["complete"]:
                continue  # wait until the whole bottom of the card is on screen (stable identity)
            if con.execute("SELECT 1 FROM posts WHERE hash=? OR id=?", (h, h)).fetchone():
                this_run.add(h)
                seen_streak += 1
                continue
            settle = config.VIDEO_SETTLE_SECONDS if p["kind"] == "video" else 0
            media = capture.crop_media(
                d, p["bounds"], h, p.get("clip_top", 0), settle=settle
            )  # before any sheet opens
            if not media and not p["bounds"]:
                log("no crop: media node not found for card")
            extra_media = capture.capture_carousel(d, p, h) if media and p["kind"] == "carousel" else []
            url, fail_reason = capture.fetch_permalink(d, h)
            if fail_reason == "sheet":
                link_sheet_failures += 1
            elif fail_reason == "clipboard":
                link_clipboard_failures += 1
            touched = True
            if not url and link_failures.get(h, 0) < config.PERMALINK_RETRIES:
                # The sheet sometimes fails to open; try again on a later screen.
                link_failures[h] = link_failures.get(h, 0) + 1
                retention.discard_media(media, *extra_media)
                break
            this_run.add(h)
            if not navigation.on_target_feed(d):
                log(f"WARN: not on the {config.FEED_MODE} feed any more; reopening it")
                navigation.open_target_feed(d)
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
                retention.discard_media(media, *extra_media)
                break
            seen_streak = 0
            if _store_post(d, con, p, h, pid, url, media, extra_media, ig_version):
                new += 1
            else:
                seen_streak += 1  # merged into a stored duplicate
            break  # the screen may have shifted; re-dump before handling the next card
        if touched:
            continue
        log(f"screen {screens}: {len(posts)} cards, {new} new so far, seen-streak {seen_streak}")
        if seen_streak >= config.STOP_AFTER_SEEN:
            log("hit already-seen posts; stopping")
            break
        device.human_scroll(d)
        device.human_pause(config.SCROLL_PAUSE_MIN, config.SCROLL_PAUSE_MAX)
        screens += 1
    retention.prune_old_posts(con)
    retention.prune_expired_stories(con)
    diagnostics.prune_debug_dumps()  # age-based pruning shouldn't depend on a new dump happening to be taken
    # Leave the app in a natural state (scrape_once() force-stops it right after)
    d.press("home")
    if push_error := _ping_freshrss(new, new_stories):
        warnings.append(push_error)
    if versioning.PROFILE_WARNING:  # read at the end: an auto-install mid-run re-resolves the profile
        warnings.append(versioning.PROFILE_WARNING)
    cards_per_screen = stat_cards / stat_dumps if stat_dumps else 0.0
    share_captioned = stat_captioned / stat_cards if stat_cards else 0.0
    share_complete = stat_complete / stat_cards if stat_cards else 0.0
    if drift_warning := db.check_selector_drift(con, cards_per_screen, share_captioned, share_complete):
        log(f"WARN: {drift_warning}")
        warnings.append(drift_warning)
    return {
        "new": new,
        "new_stories": new_stories,
        "link_sheet_failures": link_sheet_failures,
        "link_clipboard_failures": link_clipboard_failures,
        "warning": "; ".join(warnings) or None,
        "filtered_posts": filtered_posts,
        "cards_per_screen": cards_per_screen,
        "share_captioned": share_captioned,
        "share_complete": share_complete,
    }


def main() -> None:
    con = db.db_init()
    attempt = 0  # consecutive transient-failure retries so far
    if wait := _startup_wait_seconds(con):
        log(
            f"last run was recent; waiting {wait / 60:.1f}m before the first scrape (SCRAPE_ON_STARTUP=1 skips)"
        )
        time.sleep(wait)
    while True:
        started_at = datetime.now(UTC).isoformat()
        snapshot: device.DeviceSnapshot = {}
        stats: dict = {}
        error, exc = None, None
        try:
            d = device.connect_device()
            snapshot = device.device_snapshot(d)
            stats = dict(scrape_once(d, con))
            log(f"run complete: {stats['new']} new posts, {stats['new_stories']} new stories")
        except Exception as e:  # keep the loop alive; log for debugging
            exc, error = e, repr(e)
            log("ERROR:", error)
            if device.is_transient(e):
                diagnostics.save_failure_logcat(error)
        if snapshot:  # connected, so a profile was activated (possibly re-activated by an install)
            snapshot["selector_profile"] = versioning.PROFILE.name
        new_posts = stats.pop("new", 0)
        db.record_run(con, started_at, datetime.now(UTC).isoformat(), new_posts, error, snapshot, **stats)
        seconds, attempt = next_sleep_seconds(exc, attempt)
        if attempt:
            log(
                f"transient device failure; retry {attempt}/{len(config.RETRY_DELAYS_MINUTES)} in {seconds / 60:.1f}m"
            )
        else:
            log(f"sleeping {seconds / 3600:.2f}h")
        time.sleep(seconds)
