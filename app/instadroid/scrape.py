"""One scrape run (scrape_once) and the long-running poll loop (main)."""

import sqlite3
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TypedDict

from shared import sqlrows
from shared.timestamps import parse_iso

from . import (
    alerts,
    backup,
    capture,
    common,
    config,
    control,
    db,
    device,
    diagnostics,
    navigation,
    parsing,
    retention,
    stories,
    uidevice,
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
        row: sqlite3.Row | None = sqlrows.fetch_one(
            con.execute("SELECT finished_at, error FROM runs ORDER BY id DESC LIMIT 1")
        )
    except sqlite3.Error:
        return 0.0
    if not row:
        return 0.0
    finished: datetime | None = parse_iso(sqlrows.cell(row, 0))
    if finished is None:
        return 0.0
    error: str = sqlrows.cell_str(row, 1) or ""
    if error and config.RETRY_DELAYS_MINUTES and error.split("(", 1)[0] in device.transient_error_names():
        interval: float = config.RETRY_DELAYS_MINUTES[0] * 60
    else:
        interval = poll_interval_seconds(db.consecutive_failures(con))
    elapsed: float = ((now or datetime.now(UTC)) - finished).total_seconds()
    return max(0.0, interval - elapsed)


def budget_wait_seconds(con: sqlite3.Connection, now: datetime | None = None) -> float:
    """Seconds until another run is allowed under MAX_RUNS_PER_DAY: 0 when fewer runs than that
    started in the last 24h (or the budget is off), else the time until the oldest of them is a day
    old. The last line of defence against every loop at once: nothing that starts a run gets past it."""
    if config.MAX_RUNS_PER_DAY <= 0:
        return 0.0
    at: datetime = now or datetime.now(UTC)
    cutoff: datetime = at - timedelta(days=1)
    parsed: list[datetime | None] = [
        parse_iso(sqlrows.cell(r, 0))
        for r in sqlrows.fetch_all(
            con.execute(
                "SELECT started_at FROM runs WHERE started_at >= ? ORDER BY id", (cutoff.isoformat(),)
            )
        )
    ]
    starts: list[datetime] = [t for t in parsed if t is not None and t >= cutoff]
    if len(starts) < config.MAX_RUNS_PER_DAY:
        return 0.0
    return max(0.0, (starts[0] + timedelta(days=1) - at).total_seconds()) + 1


def poll_interval_seconds(failures: int = 0) -> float:
    """A sampled poll interval, widened by the failure backoff: doubled for every consecutive failed
    run after the first (1x, 2x, 4x, ...) and capped at FAILURE_BACKOFF_MAX_HOURS, so a scraper that
    fails every run — broken selectors, a build that crashes on launch, a challenge nobody has
    answered — launches Instagram a few times a day at most instead of every few hours, forever.
    0 disables the backoff."""
    seconds: float = device.sample_duration(config.POLL_MIN_H, config.POLL_MAX_H) * 3600
    if failures > 1 and config.FAILURE_BACKOFF_MAX_HOURS > 0:
        cap: float = max(config.FAILURE_BACKOFF_MAX_HOURS * 3600, seconds)
        factor: float = float(1 << min(failures - 1, 16))  # 2, 4, 8, ...; `**` on floats types as Any
        seconds = min(seconds * factor, cap)
    return seconds


def next_sleep_seconds(error: BaseException | None, attempt: int, failures: int = 0) -> tuple[float, int]:
    """(seconds to sleep before the next run, updated retry count). A transient failure retries
    after RETRY_DELAYS_MINUTES[attempt] (jittered up to +50%) until the list runs out; anything
    else — success, a non-transient error, retries exhausted — sleeps a poll interval, widened by
    the backoff for `failures` consecutive failed runs (poll_interval_seconds())."""
    if error is not None and device.is_transient(error) and attempt < len(config.RETRY_DELAYS_MINUTES):
        minutes: float = config.RETRY_DELAYS_MINUTES[attempt]
        return device.sample_duration(minutes, minutes * 1.5) * 60, attempt + 1
    return poll_interval_seconds(failures), 0


class RunStats(TypedDict):
    """What one run reports: its new post count, and `metrics`, stored as runs table columns as is
    (see db.record_run())."""

    new: int
    metrics: db.RunMetrics


def _ping_freshrss(new_posts: int, new_stories: int) -> str | None:
    """GET FRESHRSS_REFRESH_URL after a run that stored something new, so FreshRSS (or any reader
    with an equivalent refresh webhook) fetches immediately instead of waiting out its own poll
    interval. No-op when disabled or nothing new was stored. Best-effort like device.force_stop():
    returns a short error string rather than raising — a reader being unreachable must not fail a
    scrape that already succeeded. Returns None on a no-op or success."""
    if not config.FRESHRSS_REFRESH_URL or (new_posts + new_stories) == 0:
        return None
    url: str = config.FRESHRSS_REFRESH_URL
    if "ajax=" not in url:
        url += ("&" if "?" in url else "?") + "ajax=1"
    error: str | None
    if error := common.send_best_effort("FreshRSS refresh ping", url, config.FRESHRSS_REFRESH_TIMEOUT):
        return error
    # FRESHRSS_REFRESH_URL carries an auth token
    log(f"pinged FreshRSS refresh ({common.redact_url(config.FRESHRSS_REFRESH_URL)})")
    return None


def scrape_once(d: uidevice.Device, con: sqlite3.Connection) -> RunStats:
    """One scrape run. Starts and ends with Instagram and the cached system apps force-stopped
    (_free_device_memory), the end in a `finally` so a run that raises halfway doesn't leave
    Instagram's ~800MiB resident until the next poll. Adds the run's redroid memory peak and OOM
    kill count (MemoryGuard) to the stats."""
    device.free_device_memory(d)
    guard: device.MemoryGuard = device.MemoryGuard(d)
    clock: device.RunClock = device.RunClock()
    try:
        stats: RunStats = _scrape_feed(d, con, guard, clock)
    finally:
        device.free_device_memory(d)
    metrics: db.RunMetrics = stats["metrics"]
    metrics["mem_peak_mb"] = guard.peak_mb()
    oom_kills: int | None
    metrics["oom_kills"] = oom_kills = guard.oom_kills()
    if oom_kills:
        warnings: list[str] = [
            w for w in (metrics.get("warning"), f"redroid OOM-killed {oom_kills} Android process(es)") if w
        ]
        metrics["warning"] = "; ".join(warnings)
    return stats


def _store_post(
    d: uidevice.Device,
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
    parsed: tuple[datetime, int] | None = parsing.parse_posted_at(p["posted_date"], datetime.now(UTC))
    posted_at: datetime | None
    precision: int | None
    posted_at, precision = parsed if parsed else (None, None)
    now: datetime = datetime.now(UTC)
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
    dup: sqlite3.Row | None
    if dup := db.find_duplicate(con, p["username"], posted_at, precision, row["caption"]):
        merged: db.StoredPost
        media_to_drop: str | None
        merged, media_to_drop = db.merged_fields(dup, row, now)
        db.write_merged(con, sqlrows.must_str(dup, "id"), merged)
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


def _needs_permalink(stored: sqlite3.Row) -> bool:
    """A stored post with no permalink yet, and backfill tries left."""
    attempts: int = sqlrows.cell_int(stored, "permalink_attempts") or 0
    return sqlrows.cell_str(stored, "url") is None and attempts < config.PERMALINK_BACKFILL_TRIES


def _fetch_permalink(d: uidevice.Device, h: str, fail_reasons: Counter[str]) -> str | None:
    """capture.fetch_permalink(), counting a failure under its reason ("sheet"/"clipboard") in
    `fail_reasons`."""
    url: str | None
    fail_reason: str | None
    url, fail_reason = capture.fetch_permalink(d, h)
    if fail_reason:
        fail_reasons[fail_reason] += 1
    return url


def _reopen_feed_if_left(d: uidevice.Device) -> bool:
    """Reopen the target feed if a share sheet left it. True if it had to."""
    if navigation.on_target_feed(d):
        return False
    log(f"WARN: not on the {config.FEED_MODE} feed any more; reopening it")
    navigation.open_target_feed(d)
    return True


def _backfill_permalink(
    d: uidevice.Device, con: sqlite3.Connection, stored: sqlite3.Row, h: str, fail_reasons: Counter[str]
) -> None:
    """Try Copy link once for a post stored under a hash id, and record the link on its row. The id stays:
    the Atom entry id is derived from it, so changing it would make a reader show the post twice.
    updated_at moves, so the feed's ETag does and readers pick the link up. A link already stored for
    another row (the same post captured twice) is left alone. A failure is counted in `fail_reasons`."""
    post_id: str = sqlrows.must_str(stored, "id")
    url: str | None = _fetch_permalink(d, h, fail_reasons)
    attempts: int = (sqlrows.cell_int(stored, "permalink_attempts") or 0) + 1
    con.execute("UPDATE posts SET permalink_attempts=? WHERE id=?", (attempts, post_id))
    if url:
        code: str = capture.permalink_code(url)
        taken: sqlite3.Row | None = sqlrows.fetch_one(
            con.execute("SELECT id FROM posts WHERE (id=? OR url=?) AND id != ?", (code, url, post_id))
        )
        if taken:
            log(
                f"WARN: permalink {code} is already stored for {sqlrows.must_str(taken, 'id')}; not backfilling {post_id}"
            )
        else:
            now: str = datetime.now(UTC).isoformat()
            con.execute("UPDATE posts SET url=?, updated_at=? WHERE id=?", (url, now, post_id))
            log(f"backfilled permalink for {post_id}: {url}")
    con.commit()
    _reopen_feed_if_left(d)


def _rename_media(pid: str, media: str, extra_media: list[str]) -> tuple[str, list[str]]:
    """Rename a cover and its extra slides to pid.ext, pid_1.ext, ... and return the new names."""
    names: list[str] = [
        f"{pid}{Path(media).suffix}",
        *(f"{pid}_{i}{Path(fn).suffix}" for i, fn in enumerate(extra_media, 1)),
    ]
    for old, new_name in zip([media, *extra_media], names, strict=True):
        (config.MEDIA_DIR / old).rename(config.MEDIA_DIR / new_name)
    return names[0], names[1:]


def _stop_reason(guard: device.MemoryGuard, clock: device.RunClock) -> str | None:
    """Why the run should stop here, if it should: over the memory guard, or over its time budget."""
    return guard.exceeded() or clock.exceeded()


def _scrape_feed(
    d: uidevice.Device, con: sqlite3.Connection, guard: device.MemoryGuard, clock: device.RunClock
) -> RunStats:
    config.DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    capture.reset_last_url(d)
    navigation.open_target_feed(d)
    # Read after opening the feed, not from run_recorded()'s snapshot: opening it can install Instagram
    # (ensure_logged_in's auto-install), which re-reads the version and would leave the snapshot's
    # stale or empty.
    ig_version: str | None = device.instagram_version(d)
    if config.FOLLOWING_REFRESH_DAYS and db.needs_following_refresh(con):
        try:
            navigation.refresh_following_list(d, con)
        except Exception as e:  # a profile/list-navigation surprise must not sink the whole run
            log("WARN: following-list refresh failed, continuing with the existing list:", repr(e))
        navigation.open_target_feed(d)  # back onto the feed screen the post loop expects
    followed: set[str] | None = None
    if config.FOLLOWING_REFRESH_DAYS:
        rows: list[sqlite3.Row] = sqlrows.fetch_all(con.execute("SELECT username FROM following"))
        if rows:  # empty/never-refreshed means "not initialized yet" -> filter nothing
            followed = {sqlrows.must_str(r, 0) for r in rows}
    warnings: list[str] = []
    new_stories: int = 0
    reason: str | None
    if reason := _stop_reason(guard, clock):
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
    new: int
    seen_streak: int
    new = seen_streak = 0
    this_run: set[str] = set()  # hashes of the cards already handled
    link_failures: dict[str, int] = {}  # hash -> failed share-sheet attempts
    fail_reasons: Counter[str] = Counter()  # failed permalink fetches by reason ("sheet"/"clipboard")
    backfills_left: int = config.PERMALINK_BACKFILL_PER_RUN
    accounts_seen: set[str] = set()  # usernames already upserted this run
    avatars_checked: set[str] = set()  # usernames whose avatar has been considered this run
    filtered_posts: int = 0  # dropped by the followed-accounts allowlist, if enabled
    screens: int
    empty_streak: int
    screens = empty_streak = 0
    feed_reopened: bool = False
    # Selector-drift canary inputs (see db.check_selector_drift()): every hierarchy dump counts
    # toward cards/screen, and every parsed card toward the captioned/complete shares, regardless
    # of the followed-accounts filter — this measures parse yield, not post-filter output.
    stat_dumps: int
    stat_cards: int
    stat_captioned: int
    stat_complete: int
    stat_dumps = stat_cards = stat_captioned = stat_complete = 0
    while screens < config.MAX_SCROLLS:
        if reason := _stop_reason(guard, clock):
            log(f"WARN: {reason}; stopping the run early")
            warnings.append(f"stopped early: {reason}")
            break
        xml: str = d.dump_hierarchy()
        diagnostics.capture_screen(d, "feed", xml)
        raw_posts: list[parsing.Post] = parsing.parse_hierarchy(xml)
        stat_dumps += 1
        stat_cards += len(raw_posts)
        stat_captioned += sum(1 for p in raw_posts if not parsing.is_weak_caption(p["caption"] or p["alt"]))
        stat_complete += sum(1 for p in raw_posts if p["complete"])
        posts: list[parsing.Post] = raw_posts
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
            u: str = p["username"]
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
                    avatar: str | None = capture.capture_avatar(d, p["header_bounds"], u)
                    if avatar:
                        db.upsert_account(con, u, avatar)
        for p in posts:  # at most one card per dump: handling a card breaks out to re-dump
            h: str = parsing.post_id(p)
            if h in this_run:
                continue  # still on screen from the previous scroll
            if not p["complete"]:
                continue  # wait until the whole bottom of the card is on screen (stable identity)
            stored: sqlite3.Row | None = sqlrows.fetch_one(
                con.execute("SELECT id, url, permalink_attempts FROM posts WHERE hash=? OR id=?", (h, h))
            )
            if stored:
                this_run.add(h)
                seen_streak += 1
                if backfills_left > 0 and _needs_permalink(stored):
                    backfills_left -= 1
                    _backfill_permalink(d, con, stored, h, fail_reasons)
                    break  # the share sheet came and went; re-dump before the next card
                continue
            settle: float = config.VIDEO_SETTLE_SECONDS if p["kind"] == "video" else 0
            media: str | None = capture.crop_media(
                d, p["bounds"], h, p.get("clip_top", 0), settle=settle
            )  # before any sheet opens
            if not media and not p["bounds"]:
                log("no crop: media node not found for card")
            extra_media: list[str] = (
                capture.capture_carousel(d, p, h) if media and p["kind"] == "carousel" else []
            )
            url: str | None = _fetch_permalink(d, h, fail_reasons)
            if not url and link_failures.get(h, 0) < config.PERMALINK_RETRIES:
                # The sheet sometimes fails to open; try again on a later screen.
                link_failures[h] = link_failures.get(h, 0) + 1
                retention.discard_media(media, *extra_media)
                break
            this_run.add(h)
            if _reopen_feed_if_left(d):
                this_run.discard(h)  # let the card be handled again where it appears
            pid: str = capture.permalink_code(url) if url else h
            row: sqlite3.Row | None = (
                sqlrows.fetch_one(con.execute("SELECT username FROM posts WHERE id=?", (pid,)))
                if url
                else None
            )
            owner: sqlrows.SqlValue = sqlrows.cell(row, 0) if row else None
            if row and owner != p["username"]:
                log(
                    f"WARN: permalink {pid} belongs to {owner}, not {p['username']}; stale clipboard, dropping it"
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
        else:  # nothing left to handle on this screen: scroll on
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
    try:
        backup.backup_database(con)
    except (OSError, sqlite3.Error) as e:  # a full or read-only backup disk mustn't fail the scrape
        log("WARN: database backup failed:", repr(e))
        warnings.append(f"database backup failed: {e}")
    # Leave the app in a natural state (scrape_once() force-stops it right after)
    d.press("home")
    push_error: str | None
    if push_error := _ping_freshrss(new, new_stories):
        warnings.append(push_error)
    if versioning.PROFILE_WARNING:  # read at the end: an auto-install mid-run re-resolves the profile
        warnings.append(versioning.PROFILE_WARNING)
    cards_per_screen: float = stat_cards / stat_dumps if stat_dumps else 0.0
    share_captioned: float = stat_captioned / stat_cards if stat_cards else 0.0
    share_complete: float = stat_complete / stat_cards if stat_cards else 0.0
    drift_warning: str | None
    if drift_warning := db.check_selector_drift(con, cards_per_screen, share_captioned, share_complete):
        log(f"WARN: {drift_warning}")
        warnings.append(drift_warning)
    return {
        "new": new,
        "metrics": {
            "new_stories": new_stories,
            "link_sheet_failures": fail_reasons["sheet"],
            "link_clipboard_failures": fail_reasons["clipboard"],
            "warning": "; ".join(warnings) or None,
            "filtered_posts": filtered_posts,
            "cards_per_screen": cards_per_screen,
            "share_captioned": share_captioned,
            "share_complete": share_complete,
        },
    }


def run_recorded(con: sqlite3.Connection) -> tuple[RunStats | None, Exception | None]:
    """Connect, scrape once, and record the run in the runs table whatever happens: one iteration
    of main()'s loop, and `scraper.py once`. Returns (the run's stats, or None when it failed before
    finishing; the exception that ended it, or None); a failure is logged, never raised."""
    started_at: str = datetime.now(UTC).isoformat()
    snapshot: device.DeviceSnapshot = {}
    stats: RunStats | None = None
    error: str | None
    exc: Exception | None
    error, exc = None, None
    try:
        d: uidevice.Device = device.connect_device()
        snapshot = device.device_snapshot(d)
        stats = scrape_once(d, con)
        log(f"run complete: {stats['new']} new posts, {stats['metrics'].get('new_stories', 0)} new stories")
    except Exception as e:  # keep the loop alive; log for debugging
        exc, error = e, repr(e)
        log("ERROR:", error)
        if device.is_transient(e):
            diagnostics.save_failure_logcat(error)
    if snapshot:  # connected, so a profile was activated (possibly re-activated by an install)
        snapshot["selector_profile"] = versioning.PROFILE.name
    new_posts: int
    metrics: db.RunMetrics
    new_posts, metrics = (stats["new"], stats["metrics"]) if stats is not None else (0, db.RunMetrics())
    db.record_run(con, started_at, datetime.now(UTC).isoformat(), new_posts, error, snapshot, **metrics)
    if alerts.needs_human(error):
        control.set_hold(error or "")  # no retry until a person has dealt with it
    try:
        alerts.update(con)
    except (OSError, sqlite3.Error) as e:  # alerting must never take the loop down with it
        log("WARN: could not update alerts:", repr(e))
    return stats, exc


def main() -> None:
    con: sqlite3.Connection = db.db_init()
    attempt: int = 0  # consecutive transient-failure retries so far
    failures: int = db.consecutive_failures(con)  # failed runs in a row, restarts included
    wait: float
    if wait := _startup_wait_seconds(con):
        log(
            f"last run was recent; waiting {wait / 60:.1f}m before the first scrape (SCRAPE_ON_STARTUP=1 skips)"
        )
        control.wait(con, wait)
    while True:
        control.wait_while_locked()
        if wait := budget_wait_seconds(con):
            log(
                f"{config.MAX_RUNS_PER_DAY} runs started in the last 24h (MAX_RUNS_PER_DAY);"
                f" waiting {wait / 3600:.2f}h before the next"
            )
            control.wait(con, wait)
            continue  # re-check the lock and the budget: a scrape-now may have cut the wait short
        exc: Exception | None = run_recorded(con)[1]
        failures = failures + 1 if exc else 0
        seconds: float
        seconds, attempt = next_sleep_seconds(exc, attempt, failures)
        if attempt:
            log(
                f"transient device failure; retry {attempt}/{len(config.RETRY_DELAYS_MINUTES)} in {seconds / 60:.1f}m"
            )
        elif failures > 1:
            log(f"{failures} failed runs in a row; backing off, sleeping {seconds / 3600:.2f}h")
        else:
            log(f"sleeping {seconds / 3600:.2f}h")
        control.wait(con, seconds)
