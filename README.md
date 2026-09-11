# instadroid (instagram via redroid to rss)

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
./scripts/tune-android.sh 127.0.0.1:5555     # animations off, sync/location off, Google apps disabled

apkeep -a com.instagram.android -d apk-pure local
unzip -o local/com.instagram.android.xapk -d local/xapk
adb -s 127.0.0.1:5555 install-multiple local/xapk/com.instagram.android.apk local/xapk/config.*.apk

docker compose up -d --build
docker compose exec app python scraper.py login      # types the .env credentials into the login form
docker compose exec app python scraper.py once        # first scrape, watch the output
```

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
2. Walk the accessibility tree screen by screen. A post is registered only once the bottom of its
   card (share button + caption/timestamp) is on screen, so it has a stable identity. The first time
   an account's own header is on screen each run, its avatar is cropped and saved (once per account,
   refreshed after `AVATAR_REFRESH_DAYS`).
3. For each new post: crop the media from a screenshot (a video/Reel gets `VIDEO_SETTLE_SECONDS` to
   let autoplay start and the audio-label overlay fade first; a carousel is swiped through in place,
   capturing up to `MAX_CAROUSEL_SLIDES`), then tap Share → "Copy link" and read the clipboard. The
   shortcode becomes the post id and the feed links straight to the post. If the sheet fails to open
   or the clipboard never updates, it's retried on a later screen (`PERMALINK_RETRIES`), then the
   post falls back to a content hash.
4. Stop after `STOP_AFTER_SEEN` consecutive already-stored posts or `MAX_SCROLLS` screens.

Taps are always made from a hierarchy dump taken immediately beforehand, and nothing is ever tapped
inside an open sheet except "Copy link" (a stray tap there could message a contact).

If a followed account renames itself, `docker compose exec app python scraper.py rename <old>
<new>` repoints its stored history to the new username (there's no automatic detection — Instagram's
numeric user id never appears in the feed's accessibility tree). It doesn't fix an existing
`?user=<old>` FreshRSS subscription; re-subscribe under the new username after renaming.

## Development

```bash
python -m venv local/.venv && . local/.venv/bin/activate
pip install -r scripts/requirements-dev.txt -r app/requirements.txt
ruff check . && ruff format --check .
PYTHONPATH=app pytest app/tests -q     # parser + feed tests, against a fixture / temp SQLite db
```

CI (`.github/workflows/ci.yml`) runs ruff, the test suite, `pip-audit` on the requirements file
(also weekly), shellcheck on the scripts, hadolint plus a build and smoke test of the image,
`docker compose config`, and gitleaks. Dependabot watches pip, Docker base images and GitHub
Actions.

## FreshRSS

Subscribe to `http://<host>:8000/instagram.xml` (set `PUBLIC_URL` in compose to whatever
FreshRSS can reach so image links resolve). Per-account feeds: `/instagram.xml?user=somebody`.
`/users` lists everyone seen so far.

## Staying under the radar

- Keep `POLL_MIN_HOURS` ≥ 2. Instagram tolerates a phone that checks in a few times a day; it does not
  tolerate one that scrolls every 15 minutes with metronome timing.
- `MAX_SCROLLS` 25 is roughly 10–15 posts per run on this feed layout (each new post costs a
  share-sheet round trip). If you follow enough accounts to post more than that in a ~3-hour
  window, raise the poll frequency slowly (lower `POLL_MIN_HOURS`/`POLL_MAX_HOURS`) rather than
  scroll depth.
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
and only the newest 12 are kept.

## Known limitations of v1

- When "Copy link" fails on every retry (`PERMALINK_RETRIES`) for a post, its id is a hash of
  author + caption (or media description) instead of the permalink shortcode.
- Avatar and video-still crops are positional, not selector-based — Instagram's accessibility tree
  has no addressable node for either (the header is a collapsed leaf; the video frame is whatever's
  on screen after `VIDEO_SETTLE_SECONDS`) — so their exact framing hasn't been verified against a
  live device yet.
- Videos/Reels get a still only, never the actual video.

## Roadmap

Grouped by how much of the current architecture each would touch, roughly smallest to largest.

### Capture and data fidelity

These extend the existing scrape/store/serve flow without changing its shape. Done, this round:
more reliable permalinks (configurable retries, and "sheet never opened" vs. "clipboard never
updated" are now counted separately — see `/status`), full carousel capture, a better-timed video
still, profile picture display, and a configurable storage size cap — see "Storage and retention"
and "Known limitations" above for each's current shape and remaining caveats. `rename_account()` /
`scraper.py rename` gives username changes a manual reconciliation path (schema + CLI only, see
"How a scrape works" above) — still open:

- **Detect username changes automatically**: today a rename has to be noticed and reconciled by
  hand (`scraper.py rename <old> <new>`). Instagram's numeric user id never appears in the feed's
  accessibility tree, so detecting a rename would mean visiting each account's profile — extra
  in-app navigation and detection surface per run, which is why it wasn't done automatically here.
- **Real video capture**: still a poster-frame still (now better-timed, not swapped for video) —
  Reels/videos never get the actual video. Likely needs screen recording rather than a screenshot,
  plus somewhere to store and serve a video file per post, and meaningfully longer dwell time per
  video post (see "Staying under the radar" above) — a real cost/benefit call, not just effort.

### New scrape surfaces

- **Stories support**: today only the chronological Following-feed flow is scraped. Stories live in
  a completely separate UI surface, so this is closer to a second automation flow than a field
  addition to the existing one.

### Anti-detection: timing and device tuning

No infrastructure change needed — these extend the existing pause/scroll randomization and
`tune-android.sh`.

- **Configurable time-fuzzing**: `POLL_MIN_HOURS`/`POLL_MAX_HOURS` and the scroll swipe/pause ranges
  are all plain uniform-random today, with no time-of-day or day-of-week shape. Make the
  distribution itself pluggable (e.g. log-normal, day/night-aware, weekday vs. weekend) instead of
  just the min/max bounds.
- **Fingerprint consistency (locale, timezone, density, GPS)**: `tune-android.sh` only handles
  animations, sync, screen timeout, location-off, and disabling unused Google apps — locale, device
  timezone, display density, and a mock GPS fix are all still whatever redroid defaults to. These
  need to agree with each other *and* with wherever the network traffic egresses (see the
  proxy/VPN items below) — a mismatch between IP geolocation, GPS, and device timezone is an easy
  signal for Instagram to notice.

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
  that history is narrative, not a lookup.

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
- **Investigate a Rust rewrite**: evaluate rewriting the driver (uiautomator2 automation + parsing,
  ~1,000 lines of Python today) in Rust — worth weighing once the automation logic stabilizes, not
  before.
