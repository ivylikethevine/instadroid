# instadroid (instagram via redroid to rss)

> EXPERIMENTAL UNTIL v1.0.0

A real, logged-in Instagram Android app running in redroid (a containerised Android device),
driven by `uiautomator2`, publishing the chronological _Following_ feed as Atom for FreshRSS.

<!-- Where each badge's data comes from:
     - Release, ghcr.io image: the newest GitHub Release, which .github/workflows/publish.yml creates
       from a vX.Y.Z tag.
     - Tests: shields.io check-runs on main, filtered to ci.yml's `test` job by its name ("Unit
       tests"). A rename of that job must be mirrored in nameFilter, or the badge reads "no check
       runs". The site also serves a tests-passed endpoint at badges/tests.json, an alternative
       (https://ivylikethevine.github.io/instadroid/badges/tests.json).
     - Coverage: a shields.io endpoint JSON at badges/coverage.json on the Pages site. The figures are
       measured by .github/workflows/coverage.yml (.github/scripts/coverage_badges.sh writes both
       files) and served by .github/workflows/pages.yml.
     - OpenSSF Scorecard: api.scorecard.dev, fed by .github/workflows/scorecard.yml.
     - OpenSSF Best Practices: the project's bestpractices.dev entry. Not registered yet; uncomment
       the line below with the real project id once it is.
     - License: GitHub's detection of LICENSE.md.
     - Python: static, mirrors pyproject.toml's requires-python. -->

[![Release](https://img.shields.io/github/v/release/ivylikethevine/instadroid?logo=github&label=release)](https://github.com/ivylikethevine/instadroid/releases/latest)
[![instadroid image](https://img.shields.io/github/v/release/ivylikethevine/instadroid?logo=docker&logoColor=white&label=ghcr.io%2Finstadroid)](https://github.com/ivylikethevine/instadroid/pkgs/container/instadroid)
[![Tests](https://img.shields.io/github/check-runs/ivylikethevine/instadroid/main?nameFilter=Unit%20tests&label=tests)](https://github.com/ivylikethevine/instadroid/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/endpoint?url=https%3A%2F%2Fivylikethevine.github.io%2Finstadroid%2Fbadges%2Fcoverage.json)](https://github.com/ivylikethevine/instadroid/actions/workflows/coverage.yml)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/ivylikethevine/instadroid/badge)](https://scorecard.dev/viewer/?uri=github.com/ivylikethevine/instadroid)

<!-- [![OpenSSF Best Practices](https://www.bestpractices.dev/projects/PROJECT_ID/badge)](https://www.bestpractices.dev/projects/PROJECT_ID) -->

[![License: MIT](https://img.shields.io/github/license/ivylikethevine/instadroid)](LICENSE.md)
[![Python 3.14](https://img.shields.io/badge/python-3.14-blue?logo=python&logoColor=white)](https://github.com/ivylikethevine/instadroid/blob/main/pyproject.toml)

> These docs are also a [website](https://ivylikethevine.github.io/instadroid/).
> [docs/README.md](docs/README.md) indexes the rest.

## Contents

- [What it does](#what-it-does)
  - [Which Android?](#which-android)
- [Host requirements](#host-requirements)
- [First-time setup](#first-time-setup)
- [How a scrape works](#how-a-scrape-works)
  - [Stories](#stories)
  - [Followed-accounts allowlist](#followed-accounts-allowlist)
  - [Known limitations of v1](#known-limitations-of-v1)
- [Configuration highlights](#configuration-highlights)
  - [FreshRSS](#freshrss)
  - [Health and restarts](#health-and-restarts)
  - [Staying under the radar](#staying-under-the-radar)
  - [Storage and retention](#storage-and-retention)
- [Building and testing](#building-and-testing)
- [Getting help and contributing](#getting-help-and-contributing)
- [AI usage](#ai-usage)
- [Roadmap](#roadmap)
- [License](#license)

## What it does

The non-Android half ships as one image (`app/`): a scraper process and a feed server running
side by side in the same container (see `app/entrypoint.sh`).

```text
redroid (Instagram APK)  <--ADB-->  app: scraper (uiautomator2, every 2.5-4.5h)
                                            |
                                        SQLite + cropped images
                                            |
                                     app: feed (FastAPI -> /instagram.xml)  <-- FreshRSS
```

[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) has the component map, the trust boundaries and where
state lives.

### Which Android?

Instagram ships arm64 native code only, so an x86_64 host needs a redroid image with working ARM
translation. `erstt/redroid:13.0.0_ndk_ChromeOS` (Android 13) works and is the image in
`docker-compose.yml`; the Android 11, 14 and 15 images tried don't, each for a different reason. One
known quirk: under the default `androidboot.redroid_gpu_mode=guest` the Following-feed switcher
doesn't always open, so the scraper falls back to the Home feed (see
[Followed-accounts allowlist](#followed-accounts-allowlist)).

Compose refuses to start redroid on a `local/data/android` volume last used by a different Android
major version, since mixing them corrupted system state repeatedly; give another version its own
volume. [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md) has the full history, every tested image and
Instagram build pair, and what was weighed and not shipped (host GPU mode among them), and
`docker compose exec app python scraper.py compat` lists every pair your own database has run.

The first attempt at running redroid on the maintainer's host also caused a kernel panic, unrelated
to Instagram: two binder drivers on one kernel. [docs/INCIDENTS.md](docs/INCIDENTS.md) has the root
cause, and [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md#running-against-a-real-device) the rules to
follow before running redroid on a host you haven't personally tested it on.

## Host requirements

- Docker + compose, privileged containers allowed, for redroid and the `app` container. `app` uses
  host networking to reach redroid's ADB port.
- `adb` on the host. `scrcpy` is optional; `adb exec-out screencap -p > shot.png` is enough for checks.
- About 3.5GB of free RAM and a few spare cores. redroid is capped at 3g of memory with no swap
  (`REDROID_MEM_LIMIT`) and 4 CPUs (`REDROID_CPUS`); the app container at 256m and 1.5 CPUs. Scrapes
  have peaked at up to 2.4GiB ([docs/RUNLOG.md](docs/RUNLOG.md)), and at the old 2g limit a run
  OOM-killed Android processes and froze the host (see [docs/INCIDENTS.md](docs/INCIDENTS.md)).

## First-time setup

```bash
cp .env.example .env                 # fill in IG_USERNAME / IG_PASSWORD (or IG_PASSWORD_FILE)
docker compose pull                  # a bad image tag fails here, cheaply
docker compose up -d --build         # redroid boots (1-9 min; `docker ps` shows it healthy once it has)
docker compose exec app python scraper.py login      # types the .env credentials into the login form
docker compose exec app python scraper.py once        # first scrape, watch the output
```

On its first connect after each app start (a restart of redroid alone doesn't count; run
`scripts/tune-android.sh` then, or restart the app) the app waits for Android to finish booting and
tunes the device: animations, sync and location off, the screen never sleeping, `DEVICE_TIMEZONE` from `.env`
if set (see "Staying under the radar"), and the unused Google/AOSP apps in
`app/instadroid/tune_packages.txt` disabled so they never sit resident (cuts idle memory). It's
idempotent, one adb round trip; `TUNE_ON_CONNECT=0` skips it, and `scripts/tune-android.sh` does the
same by hand, e.g. after a `/data/system` reset.

The password doesn't have to live in `.env`: `IG_PASSWORD_FILE` (and `IG_USERNAME_FILE`) read the value
from a file instead, such as a Docker secret mounted at `/run/secrets/` — `docker-compose.yml` has a
commented `secrets:` example. `FEED_TOKEN`, `ALERT_URL` and `FRESHRSS_REFRESH_URL` accept a `_FILE`
variant the same way. Setting both forms of one, or a file the app can't read, stops the process with an error.

The `app` container installs Instagram on the device itself the first time it finds it missing:
`ensure_logged_in()` fetches it with `apkeep` (built into the image, from APKPure) and
`adb install-multiple`s it, caching the downloaded bundle in `local/data/apk` so a later reinstall
(e.g. after a `/data/system` reset — see [docs/INCIDENTS.md](docs/INCIDENTS.md)) doesn't re-download
it. Set `IG_AUTO_INSTALL=0` in `.env` to disable this and fall back to a manual install instead:

```bash
apkeep -a com.instagram.android -d apk-pure local
unzip -o local/com.instagram.android.xapk -d local/xapk
adb -s 127.0.0.1:5555 install-multiple local/xapk/com.instagram.android.apk local/xapk/config.*.apk
```

(needs [`apkeep`](https://github.com/EFForg/apkeep) on the host; apkmirror blocks scripted
downloads, hence APKPure). The build installed is the version profiles' default build (see below;
`scraper.py profiles` shows it); `IG_APK_VERSION` overrides it, and `latest` means
whatever APKPure has newest. Each pinned version is cached in its own `local/data/apk/<version>/` folder.
`APK_CACHE_DIR`/`APK_FETCH_TIMEOUT` tune the cache location and download/install timeout — see
`.env.example`.

Auto-install only runs when Instagram is missing, so a newer validated build doesn't replace an
installed version by itself. The scraper picks the profile covering whatever is installed, and warns
when no build of that major version has been validated. To switch, including a downgrade:

```bash
docker compose exec app python scraper.py profiles           # what's available, and what each installs
docker compose exec app python scraper.py install            # the default build (445.0.0.45.83)
docker compose exec app python scraper.py install 444.0.0.46.85
```

The saved login lives in `/data` and survives the replace, but an older Instagram may not accept
data written by a newer one, so a downgrade can still need a fresh login.

The device tuning disables a curated list of unused system apps (`app/instadroid/tune_packages.txt`)
to cut idle memory. One package must never be added to that list: `com.android.packageinstaller`. Disabling it crash-loops
`system_server` on every later cold boot; [docs/INCIDENTS.md](docs/INCIDENTS.md) has the measurement,
the log line, and the recovery.

The scraper runs the login step at the start of every scrape, so once the session is saved on the
device it is a no-op. If Instagram asks for a code or "confirm it's you", the run aborts with a
`login_screen.jpg` / `login_hierarchy.xml` in `local/data/debug` and holds every later run; finish
that step by hand, then `scraper.py unlock` ([docs/OPERATIONS.md](docs/OPERATIONS.md#health-and-restarts)).
First-run interstitials (notifications, location, "set up on new device") are dismissed automatically.
What's specific to a range of Instagram versions (selectors, any behavior that differs, the builds
checked to work, test fixtures) lives in a version profile under `app/igprofiles/`. A profile exists
only where Instagram changed something: today that's just `v424`, covering 424 (the oldest supported)
onward. The scraper runs the highest profile at or below the installed version; `IG_PROFILE` forces
one. The active profile is shown on `/status` and recorded in `runs.selector_profile`.
`new-profile` (`app/devtools/new_profile.py`, installed with the dev tools; see
[docs/CONTRIBUTING.md](docs/CONTRIBUTING.md#development-setup)) handles the mechanical work of
supporting a new build: a capped capture-mode baseline run, a per-screen selector check, fixtures,
validation, and a new profile only when something drifted. See [docs/PROFILES.md](docs/PROFILES.md)
for the design and the steps.

If a run reports `no posts parsed on first screen`, look at `local/data/debug/last_hierarchy.xml`
and `last_screen.jpg`, then fix it in that Instagram version's own profile directory rather than in
an older one or in shared code.
`docker compose exec app python scraper.py dump` grabs a fresh dump any time.

## How a scrape works

1. Log in if needed, open the target feed — `FEED_MODE=chrono` (default): the real chronological
   Following feed, reached via the switcher (retried; cold starts are slow). `FEED_MODE=home`:
   deliberately stay on the algorithmic Home feed instead, no switcher involved at all — see
   "Followed-accounts allowlist" below for why you might want that.
2. Capture up to `MAX_STORIES_PER_RUN` not-yet-seen accounts' current story frame from the Home
   feed's tray — stories don't appear on the Following screen, so in chrono mode this means
   switching to Home and back; in home mode it's already there. See "Stories" below for what this
   does and doesn't cover.
3. Walk the accessibility tree screen by screen. A post is registered only once the bottom of its
   card (share button + caption/timestamp) is on screen, so it has a stable identity. The first time
   an account's own header is on screen each run, its avatar is cropped and saved (once per account,
   refreshed after `AVATAR_REFRESH_DAYS`).
4. For each new post: crop the media from a screenshot (a video/Reel gets `VIDEO_SETTLE_SECONDS` to
   let autoplay start and the audio-label overlay fade first; a carousel is swiped through in place,
   capturing up to `MAX_CAROUSEL_SLIDES`), then tap Share → "Copy link" and read the clipboard. The
   shortcode becomes the post id and the feed links straight to the post. If the sheet fails to open
   or the clipboard never updates, it's retried on a later screen (`PERMALINK_RETRIES`), then the
   post falls back to a content hash. When a post stored that way is back on screen in a later run,
   Copy link is tried again and the link filled in, keeping the post's id so readers don't show it
   twice (`PERMALINK_BACKFILL_PER_RUN`, `PERMALINK_BACKFILL_TRIES`). If the caption was truncated at "… more", its "more" span is
   tapped (expanding it in place, no navigation) and the fully-rendered caption is stored instead
   (`CAPTION_EXPAND_TRIES` taps before giving up and keeping the truncated text).
5. Stop after `STOP_AFTER_SEEN` consecutive already-stored posts or `MAX_SCROLLS` screens. If
   `EMPTY_SCREEN_LIMIT` screens in a row show no recognisable post, a debug dump (`empty_feed0`) is
   saved and the feed is reopened once; if it happens again the run stops early (`empty_feed1`)
   and `/status` shows it as a warning, rather than swiping through the rest of `MAX_SCROLLS` blind.
6. Force-stop Instagram and a short list of cached system apps (Settings, permission controller,
   etc.). This container's Android never reclaims memory on its own between runs, and Instagram
   alone measured ~820MiB resident once opened ([docs/INCIDENTS.md](docs/INCIDENTS.md)) —
   without this, that memory just sits there for the full `POLL_MIN_HOURS`-`POLL_MAX_HOURS` gap
   until the next run. The saved login session lives on disk, not in the running process, so the
   next run's normal login-check handles the resulting cold start the same way it always does.

Taps are always made from a hierarchy dump taken immediately beforehand, and nothing is ever tapped
inside an open sheet except "Copy link" (a stray tap there could message a contact).

If a followed account renames itself, `docker compose exec app python scraper.py rename <old>
<new>` repoints its stored history to the new username (there's no automatic detection — Instagram's
numeric user id never appears in the feed's accessibility tree). It doesn't fix an existing
`?user=<old>` FreshRSS subscription; re-subscribe under the new username after renaming.

### Stories

Opening a story is a real, visible view — the scraping account shows up in that account's story
viewer list, same as a human opening it would. That's accepted as the cost of this feature, not a
bug; keep `MAX_STORIES_PER_RUN` in mind alongside "Staying under the radar" below if that's a
concern for a given account.

Only the story's current frame is captured — never actually tapped, since only the initial open tap
is made and every visit exits via Back. Advancing a story via tap was tried during development and,
on the maintainer's host, reliably ejects Instagram to the OS launcher once a single-frame story's
queue is exhausted (Back, by contrast, always returns cleanly to the tray). Given that, multi-frame
stories only ever contribute their currently-shown frame, not the whole reel — a deliberate scope
decision, not a "not yet implemented" gap. Stories have no permalink/shortcode the way posts do, so a
capture is matched by how it looks: a crop within 10 bits (of 64) of a perceptual hash of a story
the same account had stored in the last day is a re-capture and is discarded, and so is a
near-black viewer transition frame. (An exact byte hash missed these, since every capture
re-encodes a fresh screenshot.) The crop skips the header overlay, so the relative timestamp
ticking over between runs doesn't change it.
Captured stories are served at `/stories.xml` and share `RETAIN_DAYS` with posts — no separate
story-retention window.

### Followed-accounts allowlist

Under `androidboot.redroid_gpu_mode=guest` the Following-feed switcher's bottom sheet doesn't
always open (see [Which Android?](#which-android)); when it fails, `scrape_once()` falls back to
whatever's on screen — Home, algorithmic, with suggested posts from accounts you don't follow mixed
in. `FEED_MODE=home` (see "How a scrape works" above) hits the same problem on purpose, every run, by
design — it skips the switcher outright. Either way, set `FOLLOWING_REFRESH_DAYS` above its
default of `0` to filter that out: periodically (every `FOLLOWING_REFRESH_DAYS`) the scraper
navigates to the account's own profile → Following list and scrolls it (`MAX_FOLLOWING_SCROLLS`
screens, `FOLLOWING_LIST_EMPTY_LIMIT` empty screens to stop), replacing the stored list with
whatever it collected; any post from a username not on it is dropped before any of the expensive
per-post work (media crop, carousel, share-sheet permalink) rather than after, so filtered posts
cost nothing beyond the parse itself. The list being fully replaced on every refresh is also how
an unfollow gets reflected automatically.

A single refresh isn't guaranteed to be exhaustive — confirmed live against a real 30-account list,
which one refresh captured completely and another captured 27 of 30 (a different 3 missed each
time). The scroll amount is tuned to the list's own row height specifically to keep this rare (see
`device.human_scroll_list()`), but it's Instagram's own chunked rendering, not something this project can
fully control from the outside. A miss is self-correcting: the account reappears once it's on
screen for the _next_ scheduled refresh, so this only ever means "may take an extra
`FOLLOWING_REFRESH_DAYS` before a newly-followed or missed account's posts start showing up," not a
permanent gap. An account you follow but haven't seen post yet is unaffected either way, since
filtering only acts on posts that actually show up.

This is a real, visible navigation like opening a story (see "Staying under the radar" below) and
a genuine behavior change — posts can now be silently dropped — so it's off by default. It's also
safe to turn on at any point: filtering only takes effect after the list has actually captured
something at least once (an empty/never-populated list means "not initialized yet," not "you follow
nobody"), and a refresh that fails outright (selectors broken, navigation never reached the list)
leaves whatever list is already stored alone rather than replacing it with nothing — it just retries
on the next run. `scraper.py rename` keeps a renamed account's allowlist entry in sync along with
everything else it reconciles. Each run's `/status` page shows how many posts a run filtered.

### Known limitations of v1

- When "Copy link" fails on every retry (`PERMALINK_RETRIES`) for a post, its id is a hash of
  author + caption (or media description) instead of the permalink shortcode.
- Avatar and video-still crops are positional, not selector-based — Instagram's accessibility tree
  has no addressable node for either (the header is a collapsed leaf; the video frame is whatever's
  on screen after `VIDEO_SETTLE_SECONDS`) — so their exact framing hasn't been verified against a
  live device yet.
- Videos/Reels get a still only, never the actual video.

## Configuration highlights

Every setting is in `.env.example`, commented, with its default. `docker-compose.yml`'s header
explains the two kinds: settings the app reads itself, and settings compose uses to size the
containers and the device. The ones worth knowing about first:

### FreshRSS

Subscribe to `http://<host>:8000/instagram.xml` (per account: `?user=somebody`; stories:
`/stories.xml`), or import `/opml` to subscribe to everything at once. Set `FEED_TOKEN` before exposing
the feed beyond loopback. [docs/FRESHRSS.md](docs/FRESHRSS.md) covers feed auth, OPML, push refresh
and running FreshRSS on the same host.

### Health and restarts

The services restart on their own, `/health` turns the container `unhealthy` when scraping looks
stuck or keeps failing, and `scraper.py lock`/`unlock`/`scrape-now` hold or trigger runs while you
drive the device yourself. A run that ends on a login challenge holds every later run until you
`unlock`, so the scraper never retries a login on its own; repeated failures widen the poll
interval, and `MAX_RUNS_PER_DAY` caps how often Instagram is launched at all. [docs/OPERATIONS.md](docs/OPERATIONS.md) covers that plus failure alerts,
device-failure retries, the selector-drift canary and memory management.

### Staying under the radar

- Keep `POLL_MIN_HOURS` ≥ 2. Instagram tolerates a phone that checks in a few times a day; it does not
  tolerate one that scrolls every 15 minutes with metronome timing.
- `TIME_DISTRIBUTION` shapes every in-session pause and the poll interval, not just their bounds:
  `uniform` (default) draws flat across the range; `lognormal` clusters near the midpoint with the
  occasional longer outlier; `daynight` is lognormal but widens the top of the range during
  `DAYNIGHT_QUIET_START`..`DAYNIGHT_QUIET_END` local hours (default 0–6). `daynight` is a cheap
  extra layer: a metronome that's merely slow is still a metronome, whereas real usage thins out
  overnight.
- `DEVICE_TIMEZONE` (e.g. `America/Los_Angeles`) is applied to the device by the first-connect tuning
  (or `tune-android.sh`; takes effect immediately, no reboot) and defines "local" for `daynight`. It's empty by default
  on purpose: a timezone that doesn't match the network egress may be a worse signal than the
  device's default GMT.
- `MAX_SCROLLS` 25 is roughly 10–15 posts per run on this feed layout (each new post costs a
  share-sheet round trip). If you follow enough accounts to post more than that in a ~3-hour
  window, raise the poll frequency slowly (lower `POLL_MIN_HOURS`/`POLL_MAX_HOURS`) rather than
  scroll depth.
- `MAX_STORIES_PER_RUN` is a real, visible view of each story it opens — unlike scrolling the feed,
  which is invisible to the accounts posting it. Keep it modest if the followed accounts would find
  a stranger's account watching every one of their stories every few hours notable.
- Occasionally open `scrcpy` and poke around yourself; it helps, and you'll need it anyway for
  the "confirm it's you" challenges that appear a few times a year.

### Storage and retention

Posts, stories and media older than `RETAIN_DAYS` (default 60) are pruned after every run, the
database is backed up daily, and images are stored as WebP by default. [docs/OPERATIONS.md](docs/OPERATIONS.md)
covers duplicate merging, `MEDIA_MAX_MB`, debug-dump retention, and snapshotting redroid's `/data`.

## Building and testing

All Python code is 100% type annotated and at least 95% covered by tests, and CI enforces both.
[docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) has the development setup, the checks to run before a
pull request (`scripts/check.sh`, the same commands CI runs) and what CI runs; [docs/TESTING.md](docs/TESTING.md) has the test tiers, fixtures and
coverage. The device-driving code is tested against a scripted fake device, so the suite needs no
emulator and no Instagram account.

Releases are cut by pushing a signed `vX.Y.Z` tag, which publishes the image unattended once CI has
passed on that commit and the image scan is clean: see [docs/RELEASING.md](docs/RELEASING.md).

## Getting help and contributing

[docs/SUPPORT.md](docs/SUPPORT.md) says where to ask and what to include (the output of
`scripts/diagnose.sh`, for anything that goes wrong on the device). A security problem goes through
[docs/SECURITY.md](docs/SECURITY.md), never a public issue. Bug reports, selector fixes for a new
Instagram version and focused pull requests are welcome: [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md).

## AI usage

Heavily inspired by
[Dictionarry/Profilarr's AI Transparency Statement](https://v2.dictionarry.dev/ai-transparency).

I have used generative AI to write large parts of this code base. All of the
code here is my _responsibility_ regardless: AI is a tool, not an owner of a
project. I have personally understood, reviewed
and approved all of the AI-generated code in this repository, and **mainline
releases** carry the same accountability to me as anything I write and publish
myself.

## Roadmap

[docs/ROADMAP.md](docs/ROADMAP.md), ordered by scope (small/medium/large).

## License

MIT; see [LICENSE.md](LICENSE.md).
