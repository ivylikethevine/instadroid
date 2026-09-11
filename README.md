# instadroid (instagram via redroid to rss)

> EXPERIMENTAL UNTIL v1.0.0

A real, logged-in Instagram Android app running in redroid (a containerised Android device),
driven by `uiautomator2`, publishing the chronological _Following_ feed as Atom for FreshRSS.

The non-Android half ships as one image (`app/`): a scraper process and a feed server running
side by side in the same container (see `app/entrypoint.sh`).

```bash
redroid (Instagram APK)  <--ADB-->  app: scraper (uiautomator2, every 2.5-4.5h)
                                            |
                                        SQLite + cropped images
                                            |
                                     app: feed (FastAPI -> /instagram.xml)  <-- FreshRSS
```

## Which Android?

Instagram ships arm64 native code only, so an x86_64 host needs a redroid image with working ARM
translation. `abing7k/redroid:a11_ndk_amd` (Android 11) **crashed Instagram at native startup**
across 3 tested APK versions. `erstt/redroid:13.0.0_ndk_ChromeOS` (Android 13, Google's NDK
translation as shipped in ChromeOS's ARC++) **works**: Instagram installs, logs in, and scrapes
successfully — tested end-to-end (login, a full scrape run, permalink capture, cropped media) with
zero issues. This is the image in `docker-compose.yml`.

One known rendering quirk: the Following-feed switcher's bottom sheet doesn't open under this
image's default `androidboot.redroid_gpu_mode=guest`, so the scraper falls back to scraping the
Home feed. `androidboot.redroid_gpu_mode=host` was tried (with `/dev/dri` passed through) and does
engage the real host GLES renderer, but boot became much slower and the user decided against
pursuing it further — other clients of this GPU mode may not support it. `guest` remains the mode
in `docker-compose.yml`; see `CLAUDE.md` for the full writeup.

Also tried, and **not** working: `erstt/redroid:15.0.0_ndk_AVD` (Android 15, a different NDK
translation build — the `AVD` in that tag is upstream's own naming, unrelated to this project).
`hwservicemanager` and `servicemanager` — Android's core binder-registration daemons — crashed
fatally within ~3 seconds of boot, identically across two attempts, the second with
`mem_limit`/`shm_size` raised well past redroid's usual recommendations — ruling out memory as the
cause. This is a binder ABI mismatch between this specific image and this host, not an Instagram
compatibility issue or a resource one; the container exiting cleanly caused no host impact either
time. No Android 14 NDK build exists upstream (`erstt/redroid` only publishes 11/12/13/15).

Also tried: `aureliolo/redroid:14.0.0_amd64_with_gapps` (Android 14, the only Android-14 redroid
image found). It boots cleanly and Instagram installs, but the image ships **no ARM translation at
all** — `ro.product.cpu.abilist` claims `arm64-v8a` support but no `libndk_translation.so`,
`libhoudini.so`, or native-bridge property exists on the device. Confirmed empirically: launching
Instagram crashes the dynamic linker outright — `dlopen failed: "libsuperpack-jni.so" is for
EM_AARCH64 (183) instead of EM_X86_64 (62)` — a clean, host-safe app crash, not something a
config change fixes. `erstt/redroid` is the only source found with confirmed, working ARM
translation, and it doesn't publish an Android 14 build. Android 13/ChromeOS remains the image in
`docker-compose.yml`.

That same first attempt at running redroid **also caused a full kernel panic** on this specific
host, unrelated to Instagram compatibility — see `CLAUDE.md` for the root cause and the exact,
now-validated safe procedure before running redroid here (or on any host you haven't personally
tested it on).

## Host requirements

- Docker + compose, privileged containers allowed, for redroid and the `app` container. `app` uses
  host networking to reach redroid's ADB port.
- `adb` on the host. `scrcpy` is optional; `adb exec-out screencap -p > shot.png` is enough for checks.
- [`apkeep`](https://github.com/EFForg/apkeep) to fetch the Instagram APK from APKPure (apkmirror
  blocks scripted downloads).

## First-time setup

```bash
cp .env.example .env                 # fill in IG_USERNAME / IG_PASSWORD
docker compose pull redroid
docker compose up -d redroid
adb connect 127.0.0.1:5555
adb -s 127.0.0.1:5555 wait-for-device shell 'while ! pm list packages >/dev/null 2>&1; do sleep 2; done'
./scripts/tune-android.sh 127.0.0.1:5555     # animations off, sync/location off, unused Google/AOSP
                                              # apps disabled (cuts idle memory), + DEVICE_TIMEZONE
                                              # from .env if set (see "Staying under the radar")

apkeep -a com.instagram.android -d apk-pure local
unzip -o local/com.instagram.android.xapk -d local/xapk
adb -s 127.0.0.1:5555 install-multiple local/xapk/com.instagram.android.apk local/xapk/config.*.apk

docker compose up -d --build
docker compose exec app python scraper.py login      # types the .env credentials into the login form
docker compose exec app python scraper.py once        # first scrape, watch the output
```

`tune-android.sh` disables a curated list of unused system apps to cut idle memory (see CLAUDE.md's
"Reducing idle memory" for the measurement). One package must never be added to that list:
`com.android.packageinstaller`. Disabling it crashes `system_server` on every subsequent cold boot
(`RuntimeException: There must be exactly one installer; found []` in `PackageManagerService`) —
Android requires exactly one enabled package-installer app system-wide. Recovery, if this ever
happens again: `adb root`, then move `/data/system/users/0/package-restrictions.xml` aside and
restart the container — same pattern as the `/data` corruption incidents in CLAUDE.md.

The scraper runs the login step at the start of every scrape, so once the session is saved on the
device it is a no-op. If Instagram asks for a code or "confirm it's you", the run aborts with a
`login_screen.jpg` / `login_hierarchy.xml` in `local/data/debug`; finish that step by hand and re-run.
First-run interstitials (notifications, location, "set up on new device") are dismissed automatically.
Login and feed selectors live in `SELECTORS` in `app/scraper.py`.

If a run reports `no posts parsed on first screen`, look at `local/data/debug/last_hierarchy.xml`
and `last_screen.jpg`, then adjust `SELECTORS`.
`docker compose exec app python scraper.py dump` grabs a fresh dump any time.

## How a scrape works

1. Log in if needed, open the Following feed (the switcher is retried; cold starts are slow).
2. Switch back to the Home feed — stories don't appear on the Following screen — and capture up to
   `MAX_STORIES_PER_RUN` not-yet-seen accounts' current story frame from the tray, then return to
   Following. See "Stories" below for what this does and doesn't cover.
3. Walk the accessibility tree screen by screen. A post is registered only once the bottom of its
   card (share button + caption/timestamp) is on screen, so it has a stable identity. The first time
   an account's own header is on screen each run, its avatar is cropped and saved (once per account,
   refreshed after `AVATAR_REFRESH_DAYS`).
4. For each new post: crop the media from a screenshot (a video/Reel gets `VIDEO_SETTLE_SECONDS` to
   let autoplay start and the audio-label overlay fade first; a carousel is swiped through in place,
   capturing up to `MAX_CAROUSEL_SLIDES`), then tap Share → "Copy link" and read the clipboard. The
   shortcode becomes the post id and the feed links straight to the post. If the sheet fails to open
   or the clipboard never updates, it's retried on a later screen (`PERMALINK_RETRIES`), then the
   post falls back to a content hash. If the caption was truncated at "… more", its "more" span is
   tapped (expanding it in place, no navigation) and the fully-rendered caption is stored instead
   (`CAPTION_EXPAND_TRIES` taps before giving up and keeping the truncated text).
5. Stop after `STOP_AFTER_SEEN` consecutive already-stored posts or `MAX_SCROLLS` screens. If
   `EMPTY_SCREEN_LIMIT` screens in a row show no recognisable post, a debug dump (`empty_feed0`) is
   saved and the feed is reopened once; if it happens again the run stops early (`empty_feed1`)
   and `/status` shows it as a warning, rather than swiping through the rest of `MAX_SCROLLS` blind.
6. Force-stop Instagram and a short list of cached system apps (Settings, permission controller,
   etc.). This container's Android never reclaims memory on its own between runs (see CLAUDE.md),
   and Instagram alone measured ~820MiB resident once opened — without this, that memory just sits
   there for the full `POLL_MIN_HOURS`-`POLL_MAX_HOURS` gap until the next run. The saved login
   session lives on disk, not in the running process, so the next run's normal login-check handles
   the resulting cold start the same way it always does.

Taps are always made from a hierarchy dump taken immediately beforehand, and nothing is ever tapped
inside an open sheet except "Copy link" (a stray tap there could message a contact).

If a followed account renames itself, `docker compose exec app python scraper.py rename <old>
<new>` repoints its stored history to the new username (there's no automatic detection — Instagram's
numeric user id never appears in the feed's accessibility tree). It doesn't fix an existing
`?user=<old>` FreshRSS subscription; re-subscribe under the new username after renaming.

## Stories

Opening a story is a real, visible view — the scraping account shows up in that account's story
viewer list, same as a human opening it would. That's accepted as the cost of this feature, not a
bug; keep `MAX_STORIES_PER_RUN` in mind alongside "Staying under the radar" below if that's a
concern for a given account.

Only the story's current frame is captured — never actually tapped, since only the initial open tap
is made and every visit exits via Back. Advancing a story via tap was tried during development and,
on this host, reliably ejects Instagram to the OS launcher once a single-frame story's queue is
exhausted (Back, by contrast, always returns cleanly to the tray). Given that, multi-frame stories
only ever contribute their currently-shown frame, not the whole reel — a deliberate scope decision,
not a "not yet implemented" gap. Stories have no permalink/shortcode the way posts do, so each is
identified by a content hash of its own cropped image (the crop skips the header overlay so
identical stories don't hash differently as their relative timestamp ticks over between runs).
Captured stories are served at `/stories.xml` and always deleted after `STORY_RETAIN_HOURS`
(default 24), independent of `RETAIN_DAYS`.

## Development

```bash
python -m venv local/.venv && . local/.venv/bin/activate
pip install -r scripts/requirements-dev.txt -r app/requirements.txt
ruff check . && ruff format --check .
PYTHONPATH=app pytest app/tests -q     # parser, feed, and device-flow tests; temp SQLite db
PYTHONPATH=app pytest app/tests -q --cov=app --cov-report=term-missing   # with coverage
```

The device-driving code (login, feed navigation, share sheet, carousels, stories, the scrape loop)
is tested against `app/tests/fakedevice.py`: a scripted stand-in for a uiautomator2 device whose
screens are synthetic hierarchy XML, with `goto`/`clip` attributes on nodes scripting what a tap
does. No real account data is used in any fixture.

CI (`.github/workflows/ci.yml`) runs ruff, the test suite, `pip-audit` on the requirements file
(also weekly), shellcheck on the scripts, hadolint plus a build and smoke test of the image,
`docker compose config`, and gitleaks. Dependabot watches pip, Docker base images and GitHub
Actions.

## FreshRSS

Subscribe to `http://<host>:8000/instagram.xml` (set `PUBLIC_URL` in compose to whatever
FreshRSS can reach so image links resolve). Per-account feeds: `/instagram.xml?user=somebody`.
`/users` lists everyone seen so far. `/stories.xml` is a separate feed of currently-unexpired
stories (see "Stories" above) — subscribe to it separately if you want it.

## Health and restarts

Both services use `restart: unless-stopped`, so they come back after a host reboot or a crash;
`docker compose stop` (or `down`) keeps them down. `/status` is a plain-HTML page of recent runs.
`/health` returns 503 — which the compose healthcheck turns into `unhealthy` in `docker ps` — when
no run has finished within `POLL_MAX_HOURS` + 30min of the last one (the loop looks stuck), or no
run has *succeeded* for 2 × `POLL_MAX_HOURS` + 1h (e.g. a login challenge is waiting for you).
Docker doesn't restart an unhealthy container by itself. After a long downtime the stack reports
unhealthy until its first run finishes.

A run that fails for a device reason — adb offline, redroid still booting so the uiautomator server
can't start, Instagram refusing to come to the foreground — is retried after `RETRY_DELAYS_MINUTES`
(default 2, 5, then 15 minutes, with jitter) instead of waiting out a full poll interval. Login
challenges and every other error never retry early.

Each run also records the installed Instagram `versionName` and the redroid image (`runs.ig_version`
/ `runs.redroid_image`, both on `/status`), so when the selectors break it's a lookup whether an
Instagram update landed. Docker keeps at most 3 × 10MB of log per container (the `x-logging` block
in `docker-compose.yml`), and the healthcheck's own `GET /health` every 30s is left out of the
access log.

## Staying under the radar

- Keep `POLL_MIN_HOURS` ≥ 2. Instagram tolerates a phone that checks in a few times a day; it does not
  tolerate one that scrolls every 15 minutes with metronome timing.
- `TIME_DISTRIBUTION` shapes every in-session pause and the poll interval, not just their bounds:
  `uniform` (default) draws flat across the range; `lognormal` clusters near the midpoint with the
  occasional longer outlier; `daynight` is lognormal but widens the top of the range during
  `DAYNIGHT_QUIET_START`..`DAYNIGHT_QUIET_END` local hours (default 0–6). `daynight` is a cheap
  extra layer: a metronome that's merely slow is still a metronome, whereas real usage thins out
  overnight.
- `DEVICE_TIMEZONE` (e.g. `America/Los_Angeles`) is applied to the device by `tune-android.sh`
  (takes effect immediately, no reboot) and defines "local" for `daynight`. It's empty by default
  on purpose — see "Fingerprint consistency" in the Roadmap: a timezone that doesn't match the
  network egress may be a worse signal than the device's default GMT.
- `MAX_SCROLLS` 25 is roughly 10–15 posts per run on this feed layout (each new post costs a
  share-sheet round trip). If you follow enough accounts to post more than that in a ~3-hour
  window, raise the poll frequency slowly (lower `POLL_MIN_HOURS`/`POLL_MAX_HOURS`) rather than
  scroll depth.
- `MAX_STORIES_PER_RUN` is a real, visible view of each story it opens — unlike scrolling the feed,
  which is invisible to the accounts posting it. Keep it modest if the followed accounts would find
  a stranger's account watching every one of their stories every few hours notable.
- Occasionally open `scrcpy` and poke around yourself; it helps, and you'll need it anyway for
  the "confirm it's you" challenges that appear a few times a year.

## Storage and retention

Every post also gets a `posted_at` column (parsed from its relative/absolute timestamp), which is
what the feed and DB are ordered by — not `scraped_at`, since the newest post is always scraped
*first* within a run. Before storing a new card, the driver checks for an existing post by the same
author within a close time window; if either side's caption hasn't rendered yet (empty, or a bare
media description like "Photo 1 of 2 by X, 113 likes"), the two are treated as one post and merged
rather than stored twice — this is what previously caused ~30% of stored posts to be duplicates.
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
and only the newest 12 hierarchy/screenshot pairs are kept; any `.xml`/`.jpg`/`.png` there older
than `DEBUG_RETAIN_DAYS` (default 7) is deleted at the end of every run. Other files in that
directory (e.g. scratch `test*.sqlite` databases) are never touched.

Stories are unrelated to all of the above: they live in their own `stories` table and
`media/stories` subdirectory, and are always deleted `STORY_RETAIN_HOURS` after capture regardless
of `RETAIN_DAYS`/`MEDIA_MAX_MB` — see "Stories" above.

## Known limitations of v1

- When "Copy link" fails on every retry (`PERMALINK_RETRIES`) for a post, its id is a hash of
  author + caption (or media description) instead of the permalink shortcode.
- Avatar and video-still crops are positional, not selector-based — Instagram's accessibility tree
  has no addressable node for either (the header is a collapsed leaf; the video frame is whatever's
  on screen after `VIDEO_SETTLE_SECONDS`) — so their exact framing hasn't been verified against a
  live device yet.
- Videos/Reels get a still only, never the actual video.

## AI Usage

Heavily inspired by
[Dictionarry/Profilarr's AI Transparency Statement](https://v2.dictionarry.dev/ai-transparency).

I have used generative AI to write large parts of this code base. All of the
code here is my _responsibility_ regardless: AI is a tool, not an owner of a
project. I have personally understood, reviewed
and approved all of the AI-generated code in this repository, and **mainline
releases** carry the same accountability to me as anything I write and publish
myself.

## Roadmap

Grouped by how much of the current architecture each would touch, roughly smallest to largest.

### Reliability

- **Don't scrape on every container start**: the scraper starts a run the moment its container
  starts, so every `docker compose up`, recreate, or crash-restart is an extra, off-schedule scrape
  — three runs landed between 05:45 and 06:24 UTC on 2026-09-11, at least two of them from container
  recreates, all well inside `POLL_MIN_HOURS`. With `restart: unless-stopped`, a restart loop would
  become a scrape loop. On startup, wait out whatever's left of the poll interval since the last
  recorded run instead.

### Operations and observability

- **Failure alerts**: push a notification (ntfy, Apprise, or a plain webhook) on a login challenge,
  N consecutive failed runs, or no new posts for X hours. A zero-dependency variant: a synthetic
  "scraper needs attention" entry in `/instagram.xml`, since the feed is already being read.
- **Selector-drift canary**: record per-run parse stats (cards per screen, share with a real
  caption, share `complete`) and flag a drop against a rolling baseline — catches an Instagram UI
  change before runs go fully blank.
- **`scripts/diagnose.sh`**: codify `CLAUDE.md`'s logcat triage (grep for `WATCHDOG KILLING`,
  `FATAL EXCEPTION`, `Version mismatch`, `Can't downgrade database`) and print the matching fix;
  optionally have the scraper save a filtered `logcat -d` into `DEBUG_DIR` on device failures.
- **Guard `/data` against Android version mixing**: record the image tag in `local/data/android` on
  first boot and refuse to start a different Android major version against it — the appops.xml,
  idmap and telephony.db corruption in `CLAUDE.md` all came from exactly that.
- **Backups**: `sqlite3 .backup` of the posts database, plus a snapshot of redroid's `/data` before
  image or APK upgrades — the saved login session is the expensive thing to lose.
- **Instagram update path**: detect the forced "update Instagram" screen (as a challenge-style
  stop) and add a `scripts/update-instagram.sh` (apkeep → `install-multiple` → a `scraper.py dump`
  smoke check), keeping the previous xapk for rollback.
- **Manual-use lock and run-now**: a lock (file or endpoint) that keeps the scraper from starting
  while you're driving the device in scrcpy, and a rate-limited "scrape now" trigger.
- **Credentials from a file**: `IG_PASSWORD_FILE` / Docker secrets instead of a plain environment
  variable.

### Capture and data fidelity

These extend the existing scrape/store/serve flow without changing its shape.

- **Backfill missing permalinks**: 17 of 35 stored posts have no permalink (16 of them from before
  `PERMALINK_RETRIES` existed). When an already-stored hash-id post is back on screen, try Copy
  link once and fill in its `url` — keeping its existing `id`, since the Atom entry id is derived
  from it and changing it would make FreshRSS show the post twice.
- **Link hashtags and mentions in captions**: now that full captions are stored (see "How a scrape
  works" above), hashtags and @mentions in them could be turned into links in the feed HTML.
- **Detect username changes automatically**: today a rename has to be noticed and reconciled by
  hand (`scraper.py rename <old> <new>`). Instagram's numeric user id never appears in the feed's
  accessibility tree, so detecting a rename would mean visiting each account's profile — extra
  in-app navigation and detection surface per run, which is why it wasn't done automatically here.
- **Real video capture**: still a poster-frame still — Reels/videos never get the actual video. Likely needs screen recording rather than a screenshot,
  plus somewhere to store and serve a video file per post, and meaningfully longer dwell time per
  video post (see "Staying under the radar" above) — a real cost/benefit call, not just effort.
- **Full story-reel capture**: only a story's current frame is captured (see "Stories" above) — a
  deliberate scope decision, not a gap left for later, given that tapping to advance a story has
  been observed to eject the app to the OS launcher on this host once its queue is exhausted. Worth
  revisiting only with a materially different navigation approach (e.g. reading the tray's own
  `total` count to know exactly how many frames to expect, so the loop never has to discover
  exhaustion by tapping past the end).

### Feed serving

- **OPML export**: `/opml` listing one per-account feed each, for a one-step bulk subscribe in
  FreshRSS (today `/users` is a bare JSON list).
- **Push new posts to FreshRSS**: after a run with new posts, ping FreshRSS (WebSub, or its
  feed-refresh URL) so they show up immediately instead of on FreshRSS's own poll interval.
- **Smaller images, richer entries**: media averages ~260KB per post as JPEG; WebP would roughly
  halve disk and bandwidth. Add `width`/`height` to `<img>`, an Atom thumbnail for list views, and
  a ▶ marker on video/Reel titles.
- **Optional feed auth**: a token or basic auth, needed before `FEED_HOST=0.0.0.0` is safe —
  otherwise media from private accounts you follow is served to anyone on the LAN.

### New scrape surfaces

- **Reach the real Following feed without the switcher**: under `gpu_mode=guest` the switcher's
  bottom sheet may not open, and the scraper then falls back to Home — algorithmic, with suggested
  posts mixed in. Investigate a deep link or activity intent that opens Following directly;
  unconfirmed whether one exists.
- **Followed-accounts allowlist**: periodically read the logged-in account's own Following list
  and drop posts from anyone not on it, filtering suggested posts that leak in through the Home
  fallback. Costs extra in-app navigation per refresh.

### Anti-detection: timing and device tuning

No infrastructure change needed — these would extend the existing pause/scroll randomization and
`tune-android.sh` (timing distributions and device timezone already exist; see "Staying under the
radar"). Investigated but **not** implemented — display density is already covered by
`REDROID_WIDTH`/`HEIGHT`/`DPI`, so the remaining gaps are locale and GPS:

- **Locale**: `adb shell settings put system system_locales <locale>` writes the setting but a
  running system doesn't pick it up without a broadcast of `android.intent.action.LOCALE_CHANGED` —
  and this device's `adb shell` gets a `SecurityException` sending that broadcast (`not allowed to
  send broadcast ... from ... uid=2000`), confirmed live. The usual fallback, a reboot to force the
  property to be re-read at boot, is too heavy for routine per-account tuning (redroid's own ~35s
  boot budget, doubled or worse if this became a per-run thing). Needs a materially different
  approach, not just wiring up the setting.
- **Mock GPS location**: `adb emu geo fix <lon> <lat>` — the standard way to fake a location on the
  Android Emulator — is a no-op on redroid; confirmed live (no response, no error, nothing changes).
  That command talks to the official AVD's QEMU console, which redroid's non-QEMU virtualization
  doesn't expose. A real implementation needs a mock-location provider app installed and driven
  through Developer Options (`ACCESS_MOCK_LOCATION` + "select mock location app"), a materially
  bigger lift than the other items here — and moot without the proxy/VPN item below anyway, since a
  GPS fix with no matching network egress is its own mismatch.

**Fingerprint consistency**: locale, timezone, and GPS all need to agree with each other *and* with
wherever the network traffic egresses (see the proxy/VPN items below) — a mismatch between IP
geolocation, GPS, and device timezone is an easy signal for Instagram to notice. That's why
`DEVICE_TIMEZONE` defaults to empty rather than some plausible-looking value: setting it alone, with
no matching IP/GPS, may be a worse signal than leaving the device on its default GMT.

### Anti-detection: networking

Needs changes to `docker-compose.yml`'s network setup, not just app code — redroid currently has no
network config beyond the default bridge and a published ADB port.

- **IP proxy support**: route redroid's network traffic through a per-account HTTP/SOCKS proxy.
- **VPN support**: route through a VPN client (e.g. WireGuard) instead of/alongside a proxy.

### Documentation

- **Document compatible Android image / Instagram version pairs**: partially done already —
  `CLAUDE.md`'s "What's validated" section already tracks which `erstt/redroid` tags work
  (`13.0.0_ndk_ChromeOS`) versus don't (`15.0.0_ndk_AVD`: binder ABI mismatch;
  `aureliolo/redroid:14.0.0_amd64_with_gapps`: no ARM translation at all; `abing7k`'s Android 11:
  Instagram crashes at native startup). What's still missing is a structured table cross-referencing
  specific Instagram APK versions against each image, kept current as Instagram updates — right now
  that history is narrative, not a lookup. The raw data now accumulates on its own: every run
  records the Instagram `versionName` and redroid image (`runs.ig_version` / `runs.redroid_image`).

### Architecture and scaling

The most invasive items — each changes the container/process topology, not just code inside it.

- **Multiple Android VMs**: `docker-compose.yml` runs exactly one `redroid` + one `app`, and
  `redroid` mounts one `/data` volume. Running several in parallel (one per account) means
  per-instance compose services *and* per-instance `/data` volumes — the appops.xml/idmap/
  telephony.db corruption incidents in `CLAUDE.md` are a direct warning against ever pointing two
  instances at the same volume.
- **Pure ADB backend for a real phone**: an alternative to redroid that drives a physical Android
  device over USB/network ADB, for accounts where an emulator's fingerprint is too great a risk.
  The driver already talks to its device purely over an ADB address, so the automation layer may
  mostly carry over, but it's still a distinct backend from the emulated one, with its own
  device-management story.
- **arm64 host support**: on an arm64 host, official `redroid/redroid` images run Instagram's arm64
  code natively — no NDK translation, sidestepping the whole "Which Android?" compatibility matrix.
  Needs a multi-arch app image (`platforms:` in `publish.yml`) and host docs (binder in the kernel).
- **Replay tests and a module split**: `scrape_once()` and the other device flows now run in CI
  against `tests/fakedevice.py`, but its screens are hand-written. Replaying *recorded* sequences —
  with a helper that promotes a `DEBUG_DIR` dump into a sanitised fixture — would catch real
  Instagram UI drift that synthetic screens can't. Splitting `scraper.py` (~1,700
  lines) into selectors/db/navigation/parsing/capture/retention modules, with numbered migrations
  in place of ad-hoc `PRAGMA user_version` checks, is a precondition for multi-account support and
  for the Rust evaluation below.
- **Investigate a Rust rewrite**: evaluate rewriting the driver (uiautomator2 automation + parsing,
  ~1,000 lines of Python today) in Rust — worth weighing once the automation logic stabilizes, not
  before.
