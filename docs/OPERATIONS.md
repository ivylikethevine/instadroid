# Operations

Running the stack day to day: health, restarts, manual control, alerts, memory, and what's stored
and for how long. Setup is in the [README](../README.md#first-time-setup); the
redroid incidents referred to below are written up in [INCIDENTS.md](INCIDENTS.md).

## Contents

- [Health and restarts](#health-and-restarts)
- [Storage and retention](#storage-and-retention)

## Health and restarts

Every long-running service uses `restart: unless-stopped`, so they come back after a host reboot or a crash;
`docker compose stop` (or `down`) keeps them down. `/status` is a plain-HTML page of recent runs.
`/health` returns 503 — which the compose healthcheck turns into `unhealthy` in `docker ps` — when
no run has finished within `POLL_MAX_HOURS` + 30min of the last one (the loop looks stuck), or no
run has _succeeded_ for 2 × `POLL_MAX_HOURS` + 1h (e.g. a login challenge is waiting for you).
Docker doesn't restart an unhealthy container by itself. After a long downtime the stack reports
unhealthy until its first run finishes. redroid has a healthcheck too, on `sys.boot_completed`, so an
Android that never finishes booting (or crash-looped back into booting) shows as `unhealthy` in
`docker ps`; on a clean start it turns healthy about 20 seconds in. It exists for visibility only,
and nothing acts on it.

**One report of the whole state**: `docker compose exec app python scraper.py doctor` prints the
control state, the last five runs with their result and peak memory, the failure backoff and daily
budget if either is holding runs back, the device over plain adb (boot state, Instagram version and
whether it's running, redroid's memory) and the logcat crash signatures with their fixes. It never
launches Instagram or uiautomator2. [SUPPORT.md](SUPPORT.md) asks for its output with a bug report.

**Manual lock and scrape-now**: before driving the device yourself in scrcpy, lock the scraper so a
scheduled run can't start underneath you, and unlock when done:

```bash
docker compose exec app python scraper.py lock      # or: touch local/data/db/manual.lock
docker compose exec app python scraper.py unlock    # or: rm local/data/db/manual.lock
docker compose exec app python scraper.py scrape-now
```

A run already in progress finishes. The lock holds until you remove it; `LOCK_MAX_HOURS` (default 0) can instead have a lock older than that many hours ignored as forgotten, with a warning. `scrape-now` starts a run within about 30 seconds, but only
once `RUN_NOW_MIN_MINUTES` (default 30) have passed since the last one. The feed server offers the
same as `GET /control`, `POST`/`DELETE /control/lock` and `POST /control/scrape-now` (behind
`FEED_TOKEN` when that's set), and `/status` shows both, with Lock/Unlock and Scrape now buttons
that call those routes.

**The scraper holds itself when Instagram needs a person.** A run that ends on a challenge
("confirm it's you", a code), a login form it couldn't recognise or get past, or missing
credentials writes `local/data/db/needs-human.hold` (the error is its text) and no further run
starts, not on the schedule, not from `scrape-now`, not after a restart, until you clear it:

```bash
docker compose exec app python scraper.py unlock    # or: DELETE /control/lock, or rm the file
```

Unlike the manual lock it never expires. Without it the loop would relaunch Instagram, and retype a
wrong password, every poll interval: the kind of repetition that gets an account flagged. `/status`, `/control` and the alert below all show the hold and its reason.
Finish the challenge in scrcpy (or fix the credentials) first, then unlock.

**Failure alerts**: after each run the scraper raises an alert for a login challenge, for
`ALERT_FAILED_RUNS` (default 3) failed runs in a row, and optionally for no new post in
`ALERT_NO_POSTS_HOURS`. Each one is announced once when it's raised and once when it clears, as a
POST to `ALERT_URL` (an [ntfy](https://ntfy.sh) topic URL works as is). With or without that, open
alerts appear as the first entry of `/instagram.xml` and on `/status`, so the feed reader you already
check shows them.

A run that fails for a device reason — adb offline, redroid still booting so the uiautomator server
can't start, Instagram refusing to come to the foreground — is retried after `RETRY_DELAYS_MINUTES`
(default 2, 5, then 15 minutes, with jitter) instead of waiting out a full poll interval. Login
challenges and every other error never retry early. After a device failure, the scraper also saves
a filtered `adb logcat -d` (error and fatal lines plus the known crash-loop signatures from
[INCIDENTS.md](INCIDENTS.md), last `LOGCAT_TAIL_LINES`) to `local/data/debug/logcat_<time>.txt`, so
the log is already on disk before anyone restarts anything.

After consecutive failed runs of any kind, the poll interval doubles per failure (1x, 2x, 4x, ...)
up to `FAILURE_BACKOFF_MAX_HOURS` (default 24), and a success resets it. So broken selectors or a
build that crashes on launch cost one Instagram launch a day, not eight. The count comes from the
runs table, so a restart doesn't reset it; `0` disables the backoff.

On top of that, `MAX_RUNS_PER_DAY` (default 12) caps the runs started in any 24h window, whatever
starts them: the schedule, retries, `scrape-now`, `scraper.py once` or a restart. At the ceiling the
loop waits for the oldest run in the window to age out (and `once` refuses, saying when the next is
allowed). It's the backstop under every other limit; `0` disables it.

Restarting the container doesn't trigger an extra scrape. On startup the scraper looks at the last
recorded run and waits out whatever's left of a poll interval since it finished (or of the first
retry delay, if that run failed for a device reason) before its first scrape. With no recorded run
it starts right away. `SCRAPE_ON_STARTUP=1` restores scraping immediately on every start.

Each run also records the installed Instagram `versionName` and the redroid image (`runs.ig_version`
/ `runs.redroid_image`, both on `/status`), so when the selectors break it's a lookup whether an
Instagram update landed. Each post also stores the version that scraped it (`posts.ig_version`,
first-seen wins on a duplicate merge; `NULL` for posts from before this was added).

**Selector-drift canary**: every run also records its parse yield — cards parsed per screen dump,
the share of cards with a real (non-placeholder) caption, and the share flagged "complete" (see
`parsing.is_weak_caption()`) — as `runs.cards_per_screen`/`share_captioned`/`share_complete`. If any of the
three falls below `SELECTOR_DRIFT_THRESHOLD` (default 0.5, i.e. half) of the rolling average over
the last `SELECTOR_DRIFT_BASELINE_RUNS` successful runs (default 10; needs at least
`SELECTOR_DRIFT_MIN_RUNS`, default 3, prior runs with a nonzero baseline before it judges anything),
a run warning like `selector drift? cards/screen 0.40 vs 3.80 baseline (10 runs)` is raised — this
is meant to catch an Instagram UI change (a moved resource-id, a changed card layout) well before a
run goes fully blank, rather than only noticing once new posts stop arriving. `SELECTOR_DRIFT_BASELINE_RUNS=0` disables it.

Memory: redroid's Android never reclaims memory on its own here, so the scraper manages it. On its
first connect after each start it disables the unused apps in `app/instadroid/tune_packages.txt` (the
same list `scripts/tune-android.sh` applies by hand), so they never launch at all. Every
run starts and ends by force-stopping Instagram and a few cached system apps (the end even when the
run fails), and `scraper.py login` stops Instagram when it's done. During a run the scraper reads
redroid's container memory through adb before stories and before each screen, and stops early with
a warning once it reaches `MEMORY_GUARD_PERCENT` (default 85) of the container's limit. Each run
records its peak (`runs.mem_peak_mb`) and any kernel OOM kills inside redroid (`runs.oom_kills`,
also a run warning), shown in `/status`'s "Peak mem" column. A run also stops early, with the same
kind of warning, once it has been going `RUN_MAX_MINUTES` (default 30; a normal run takes 5-15), so a
degraded device can't stretch one run across hours of Instagram use.

Docker keeps at most 3 × 10MB of log per container (the `x-logging` block
in `docker-compose.yml`), and the healthcheck's own `GET /health` every 30s is left out of the
access log.

## Storage and retention

Every post also gets a `posted_at` column (parsed from its relative/absolute timestamp), which is
what the feed and DB are ordered by — not `scraped_at`, since the newest post is always scraped
_first_ within a run. Before storing a new card, the scraper checks for an existing post by the same
author within a close time window; if either side's caption hasn't rendered yet (empty, or a bare
media description like "Photo 1 of 2 by X, 113 likes"), the two are treated as one post and merged
rather than stored twice.
The first run after upgrading applies this merge once to the existing database. A merge like this
bumps the row's `updated_at` (even though `scraped_at`, when it was first seen, doesn't change) —
the feed's ETag keys off `updated_at`, so a caption correction like this actually reaches FreshRSS
instead of getting cached away as a 304.

Posts (and their media) older than `RETAIN_DAYS` (default 60, `0` keeps everything) are deleted at
the end of every scrape, along with any media file no row references any more, so disk use stays
flat. A carousel's extra slides (beyond the cover, which stays in the same row as before) live in a
small `media` table and are deleted alongside their post; avatars live in their own `media/avatars`
subdirectory, one file per account, untouched by this cleanup. If `MEDIA_MAX_MB` is set, the oldest
posts are removed after that (even if still within `RETAIN_DAYS`) until total size is back under the
cap — worth setting once carousels are captured in full, since a single heavily-posting account can
otherwise grow disk use with no bound but time. Debug dumps in `local/data/debug` are saved as JPEG
and only the newest 12 hierarchy/screenshot pairs (and 12 failure logcats) are kept; any
`.xml`/`.jpg`/`.png`/`.txt` there older
than `DEBUG_RETAIN_DAYS` (default 7) is deleted at the end of every run. Other files in that
directory (e.g. scratch `test*.sqlite` databases) are never touched.

Saved images are WebP by default (`MEDIA_FORMAT=jpeg` switches back). Re-encoding real crops,
WebP came out ~36% smaller than JPEG at the default `MEDIA_QUALITY=95` and ~56% smaller at 90.
Switching formats needs no migration: the database stores each file's name, so earlier files keep
serving, and the orphan sweep and retention handle both extensions.
In the feeds, every image carries its `width`/`height`, each entry gets a `<media:thumbnail>` (the
cover image) for readers that show pictures in list view, and video/Reel titles start with ▶.

**Backups**: at the end of a run, once the newest backup is `BACKUP_EVERY_HOURS` old (default 24), the
scraper writes a consistent copy of the database to `BACKUP_DIR` (default `local/data/db/backups`)
and keeps the newest `BACKUP_KEEP` (default 7); `docker compose exec app python scraper.py backup`
takes one now. The expensive thing to lose is redroid's `/data`, with the logged-in session: stop
redroid and run `scripts/snapshot-android-data.sh [--keep N]` before changing the redroid image or
Instagram build, or a risky recovery step. It writes `local/data/backups/android-<time>.tar.gz`.

Stories live in their own `stories` table and `media/stories` subdirectory, but share `RETAIN_DAYS`
with posts (`MEDIA_MAX_MB`'s size cap only ever considers posts, not stories) — see the README's
"Stories".
