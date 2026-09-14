# instadroid (instagram via redroid to rss)

> EXPERIMENTAL UNTIL v1.0.0

[![instadroid image](https://img.shields.io/github/v/release/ivylikethevine/instadroid?logo=docker&logoColor=white&label=ghcr.io%2Finstadroid)](https://github.com/ivylikethevine/instadroid/pkgs/container/instadroid)
[![coverage](https://img.shields.io/endpoint?url=https://ivylikethevine.github.io/instadroid/coverage.json)](https://github.com/ivylikethevine/instadroid/actions/workflows/pages.yml)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/ivylikethevine/instadroid/badge)](https://scorecard.dev/viewer/?uri=github.com/ivylikethevine/instadroid)

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

[`docs/COMPATIBILITY.md`](docs/COMPATIBILITY.md) has the same history as a lookup table of image and
Instagram build pairs, and `docker compose exec app python scraper.py compat` lists every pair your own
database has run, from what each run records.

That same first attempt at running redroid **also caused a full kernel panic** on this specific
host, unrelated to Instagram compatibility — see `CLAUDE.md` for the root cause and the exact,
now-validated safe procedure before running redroid here (or on any host you haven't personally
tested it on).

Switching `docker-compose.yml` to a different Android major version against the same
`local/data/android` volume is what corrupted system state repeatedly here, so compose now refuses:
a one-shot `android-data-guard` service runs before redroid, records the image in
`local/data/android.image`, and fails with instructions when the configured image's Android version
differs. Give another Android version its own volume instead.

## Host requirements

- Docker + compose, privileged containers allowed, for redroid and the `app` container. `app` uses
  host networking to reach redroid's ADB port.
- `adb` on the host. `scrcpy` is optional; `adb exec-out screencap -p > shot.png` is enough for checks.
- About 3.5GB of free RAM and a few spare cores. redroid is capped at 3g of memory with no swap
  (`REDROID_MEM_LIMIT`) and 4 CPUs (`REDROID_CPUS`); the app container at 256m and 1.5 CPUs. A live
  scrape has measured close to 2GiB, and at the old 2g limit a run OOM-killed Android processes
  and froze the host (see CLAUDE.md).

## First-time setup

```bash
cp .env.example .env                 # fill in IG_USERNAME / IG_PASSWORD (or IG_PASSWORD_FILE)
docker compose pull redroid
docker compose up -d redroid
adb connect 127.0.0.1:5555
adb -s 127.0.0.1:5555 wait-for-device shell 'while ! pm list packages >/dev/null 2>&1; do sleep 2; done'
./scripts/tune-android.sh 127.0.0.1:5555     # animations off, sync/location off, unused Google/AOSP
                                              # apps disabled (cuts idle memory), + DEVICE_TIMEZONE
                                              # from .env if set (see "Staying under the radar")

docker compose up -d --build
docker compose exec app python scraper.py login      # types the .env credentials into the login form
docker compose exec app python scraper.py once        # first scrape, watch the output
```

The password doesn't have to live in `.env`: `IG_PASSWORD_FILE` (and `IG_USERNAME_FILE`) read the value
from a file instead, such as a Docker secret mounted at `/run/secrets/` — `docker-compose.yml` has a
commented `secrets:` example. `FEED_TOKEN` and `FRESHRSS_REFRESH_URL` accept a `_FILE` variant the same
way. Setting both forms of one, or a file the app can't read, stops the process with an error.

The `app` container installs Instagram on the device itself the first time it finds it missing:
`ensure_logged_in()` fetches it with `apkeep` (built into the image, from APKPure) and
`adb install-multiple`s it, caching the downloaded bundle in `local/data/apk` so a later reinstall
(e.g. after a `/data/system` reset — see CLAUDE.md) doesn't re-download it. Set `IG_AUTO_INSTALL=0`
in `.env` to disable this and fall back to a manual install instead:

```bash
apkeep -a com.instagram.android -d apk-pure local
unzip -o local/com.instagram.android.xapk -d local/xapk
adb -s 127.0.0.1:5555 install-multiple local/xapk/com.instagram.android.apk local/xapk/config.*.apk
```

(needs [`apkeep`](https://github.com/EFForg/apkeep) on the host; apkmirror blocks scripted
downloads, hence APKPure). The build installed is the newest one validated with the version
profiles (see below; `scraper.py profiles` shows it); `IG_APK_VERSION` overrides it, and `latest` means
whatever APKPure has newest. Each pinned version is cached in its own `local/data/apk/<version>/` folder.
`APK_CACHE_DIR`/`APK_FETCH_TIMEOUT` tune the cache location and download/install timeout — see
`.env.example`.

Auto-install only runs when Instagram is missing, so a newer validated build doesn't replace an
installed version by itself. The scraper picks the profile covering whatever is installed, and warns
when no build of that major version has been validated. To switch, including a downgrade:

```bash
docker compose exec app python scraper.py profiles           # what's available, and what each installs
docker compose exec app python scraper.py install            # the active profile's build
docker compose exec app python scraper.py install 446.0.0.49.77
```

The saved login lives in `/data` and survives the replace, but an older Instagram may not accept
data written by a newer one, so a downgrade can still need a fresh login.

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
What's specific to a range of Instagram versions (selectors, any behavior that differs, the builds
checked to work, test fixtures) lives in a version profile under `app/igprofiles/`. A profile exists
only where Instagram changed something: today that's just `v440`, covering 440 (the oldest supported)
onward. The scraper runs the highest profile at or below the installed version; `IG_PROFILE` forces
one. The active profile is shown on `/status` and recorded in `runs.selector_profile`.
`scripts/new_profile.py` handles the mechanical work of supporting a new build: a capped
capture-mode baseline run, a per-screen selector check, fixtures, validation, and a new profile only
when something drifted. See `docs/NEXT.md` for the design and the steps.

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
not a "not yet implemented" gap. Stories have no permalink/shortcode the way posts do, so a
capture is matched by how it looks: a crop within 10 bits (of 64) of a perceptual hash of a story
the same account had stored in the last day is a re-capture and is discarded, and so is a
near-black viewer transition frame. (An exact byte hash missed these, since every capture
re-encodes a fresh screenshot.) The crop skips the header overlay, so the relative timestamp
ticking over between runs doesn't change it.
Captured stories are served at `/stories.xml` and share `RETAIN_DAYS` with posts — no separate
story-retention window.

## Followed-accounts allowlist

Under `androidboot.redroid_gpu_mode=guest` the Following-feed switcher's bottom sheet doesn't
always open (see "Which Android?" above); when it fails, `scrape_once()` falls back to whatever's
on screen — Home, algorithmic, with suggested posts from accounts you don't follow mixed in.
`FEED_MODE=home` (see "How a scrape works" above) hits the same problem on purpose, every run, by
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
`_human_scroll_list()`), but it's Instagram's own chunked rendering, not something this project can
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

## Development

```bash
python -m venv local/.venv && . local/.venv/bin/activate
pip install -r scripts/requirements-dev.txt -r app/requirements.txt
ruff check . && ruff format --check . && pyright
pytest -q                              # parser, feed, and device-flow tests; temp SQLite db
pytest -q --cov=app --cov-report=term-missing   # with coverage
python scripts/export_openapi.py       # after changing a route in app/app.py
```

All Python code, tests included, is fully type-annotated: ruff's `ANN` rules enforce annotations on
every function, and pyright type-checks `app/` (tests excluded) and `scripts/`.

The feed server's OpenAPI spec is committed as [`docs/openapi.json`](docs/openapi.json) and published
with the project site. `tests/test_openapi.py` compares it with the routes, so CI fails until the spec
is regenerated after a route change.

The device-driving code (login, feed navigation, share sheet, carousels, stories, the scrape loop)
is tested against `app/tests/fakedevice.py`: a scripted stand-in for a uiautomator2 device whose
screens are synthetic hierarchy XML, with `goto`/`clip` attributes on nodes scripting what a tap
does. No real account data is used in any fixture.

CI (`.github/workflows/ci.yml`) runs ruff, the test suite, `pip-audit` on the requirements file
(also weekly), shellcheck on the scripts, hadolint plus a build and smoke test of the image,
`docker compose config`, and gitleaks. `.github/workflows/scorecard.yml` publishes the OpenSSF Scorecard
result weekly and on every push to `main` (the badge above).
`.github/workflows/new-builds.yml` checks APKPure weekly and opens an issue when an Instagram build newer
than every validated one appears. Tests run with coverage there too, for visibility in the
run's own log. Dependabot watches pip, Docker base images and GitHub Actions.

`.github/workflows/pages.yml` builds and deploys the [project site](https://ivylikethevine.github.io/instadroid/)
(see "Roadmap" above) on every push to `main`: it re-runs the test suite with coverage, builds the
Jekyll site from `docs/`, and writes the total as `coverage.json` (a shields.io endpoint payload)
into the built site alongside it — the README's coverage badge reads that URL, so no external
coverage service, token, or dedicated git branch is involved.

### Releases

Pushing a `vX.Y.Z` tag runs `.github/workflows/publish.yml`: it builds and smoke-tests the app
image first, with no approval needed, so a broken build just fails. Only once that build succeeds
does the `publish` job wait for approval against the `publish` GitHub Environment — approving it
tags and pushes the already-built image (no rebuild) and attests its provenance. A final job then
creates a GitHub Release with the image digest, pull command, a compose snippet, and the
attestation-verify command. A tag like `v1.0.0-rc1` is treated as a prerelease and never moves
`:latest`.

## FreshRSS

Subscribe to `http://<host>:8000/instagram.xml` (set `PUBLIC_URL` in compose to whatever
FreshRSS can reach so image links resolve). Per-account feeds: `/instagram.xml?user=somebody`.
`/users` lists everyone seen so far. `/stories.xml` is a separate feed of currently-unexpired
stories (see "Stories" above) — subscribe to it separately if you want it. In each post, @mentions
and #hashtags in the caption link to that account's or hashtag's Instagram page.

**Feed auth**: the feed server is open to anything that can reach it, which is fine on the default
`FEED_HOST=127.0.0.1`. Before setting `FEED_HOST=0.0.0.0`, set `FEED_TOKEN` (or `FEED_TOKEN_FILE`):
every path except `/health` then needs the token, as `Authorization: Bearer <token>`, as the
password of HTTP basic auth (any username — FreshRSS's per-feed HTTP username/password fields), or
as `?token=<token>` on the feed URL. Images in the feeds keep loading in a reader without the token:
their URLs carry a signature that opens that one file only. `/opml` writes `?token=` into each feed
URL, so importing it (or a Dynamic OPML category, itself subscribed with `?token=`) needs no per-feed
setup. A `?token=` value is blanked in the access log. Browsers get a basic-auth prompt on `/status`.
Without a token, a non-loopback `FEED_HOST` logs a warning at startup.

If FreshRSS runs on the same host (see below), set `PUBLIC_URL=http://127.0.0.1:8000`, not
`http://localhost:8000` — confirmed the hard way: a FreshRSS container's `localhost` resolved to
`::1` first, and the feed server only binds the IPv4 loopback (`FEED_HOST=127.0.0.1` default), so
every subscription failed with "Failed to resolve domain" until `PUBLIC_URL` used the literal IP.

**One-step bulk subscribe**: `/opml` is an OPML outline listing all of the above — the aggregate
feed, `/stories.xml`, and one entry per account in `/users` — nested under a single "Instagram"
category. It's a subscription list, not a feed: adding `/opml` as a feed URL fails with FreshRSS's
"A feed could not be found at …/opml" (SimplePie only parses RSS/Atom). Either:

- **Import it once**: Subscription management → Import/Export, then import the file saved from
  `/opml`.
- **Keep it in sync** (FreshRSS 1.20+): create a category and set its *Dynamic OPML* URL to
  `$PUBLIC_URL/opml`. FreshRSS then re-reads the outline on its own schedule, so accounts that
  appear later (or a rename via `scraper.py rename`) are picked up without re-importing.

**Push instead of poll**: by default FreshRSS finds new posts on its own poll interval. To have
the scraper tell it instead, set `FRESHRSS_REFRESH_URL` in `.env` to FreshRSS's "online cron"
actualize URL (`http://<freshrss-host>/i/?c=feed&a=actualize&user=<name>&token=<token>` — the
token comes from the user you create below); after any run that stores something new, the scraper
GETs that URL so FreshRSS fetches immediately. A reader being unreachable is logged as a run
warning (shows yellow on `/status`), never fails the scrape.

**Running FreshRSS on this host**: an optional `freshrss` service in `docker-compose.yml`, off by
default (`docker compose --profile freshrss up -d freshrss`). One-time setup after it's up:

```bash
docker compose exec freshrss ./cli/do-install.php \
  --default-user ivy --auth-type form --db-type sqlite
docker compose exec freshrss ./cli/create-user.php \
  --user ivy --password <a password> --token <a token> --no-default-feeds
# Required, not optional: do-install.php's own output tells you this, and skipping it fails every
# request (including the plain login page) with "Error during context user init!" — the install
# leaves data/users/<name> group-owned by root with no www-data access, so PHP running as www-data
# can't read the very config it just wrote.
docker compose exec --user root freshrss ./cli/access-permissions.sh
```

That token is what `FRESHRSS_REFRESH_URL` above is built from. FreshRSS listens on
`127.0.0.1:8080` by default (`FRESHRSS_LISTEN`) — loopback only, matching `FEED_HOST`, since the
feed itself has no auth unless `FEED_TOKEN` is set (see "Feed auth" above). It needs
`FRESHRSS_INTERNAL_HOST_ALLOWLIST` (defaulted in compose) to be allowed to fetch a feed on
`127.0.0.1` at all — FreshRSS 1.30+ blocks that as an SSRF guard otherwise.

## Health and restarts

Both services use `restart: unless-stopped`, so they come back after a host reboot or a crash;
`docker compose stop` (or `down`) keeps them down. `/status` is a plain-HTML page of recent runs.
`/health` returns 503 — which the compose healthcheck turns into `unhealthy` in `docker ps` — when
no run has finished within `POLL_MAX_HOURS` + 30min of the last one (the loop looks stuck), or no
run has _succeeded_ for 2 × `POLL_MAX_HOURS` + 1h (e.g. a login challenge is waiting for you).
Docker doesn't restart an unhealthy container by itself. After a long downtime the stack reports
unhealthy until its first run finishes.

**Manual lock and scrape-now**: before driving the device yourself in scrcpy, lock the scraper so a
scheduled run can't start underneath you, and unlock when done:

```bash
docker compose exec app python scraper.py lock      # or: touch local/data/db/manual.lock
docker compose exec app python scraper.py unlock    # or: rm local/data/db/manual.lock
docker compose exec app python scraper.py scrape-now
```

A run already in progress finishes; a lock older than `LOCK_MAX_HOURS` (default 6) counts as
forgotten and is ignored with a warning. `scrape-now` starts a run within about 30 seconds, but only
once `RUN_NOW_MIN_MINUTES` (default 30) have passed since the last one. The feed server offers the
same as `GET /control`, `POST`/`DELETE /control/lock` and `POST /control/scrape-now` (behind
`FEED_TOKEN` when that's set), and `/status` shows both.

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
a filtered `adb logcat -d` (error and fatal lines plus the known crash-loop signatures from CLAUDE.md,
last `LOGCAT_TAIL_LINES`) to `local/data/debug/logcat_<time>.txt`, so the log is already on disk
before anyone restarts anything.

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
`_is_weak_caption()`) — as `runs.cards_per_screen`/`share_captioned`/`share_complete`. If any of the
three falls below `SELECTOR_DRIFT_THRESHOLD` (default 0.5, i.e. half) of the rolling average over
the last `SELECTOR_DRIFT_BASELINE_RUNS` successful runs (default 10; needs at least
`SELECTOR_DRIFT_MIN_RUNS`, default 3, prior runs with a nonzero baseline before it judges anything),
a run warning like `selector drift? cards/screen 0.40 vs 3.80 baseline (10 runs)` is raised — this
is meant to catch an Instagram UI change (a moved resource-id, a changed card layout) well before a
run goes fully blank, rather than only noticing once new posts stop arriving. `SELECTOR_DRIFT_BASELINE_RUNS=0` disables it.

Memory: redroid's Android never reclaims memory on its own here, so the scraper manages it. Every
run starts and ends by force-stopping Instagram and a few cached system apps (the end even when the
run fails), and `scraper.py login` stops Instagram when it's done. During a run the scraper reads
redroid's container memory through adb before stories and before each screen, and stops early with
a warning once it reaches `MEMORY_GUARD_PERCENT` (default 85) of the container's limit. Each run
records its peak (`runs.mem_peak_mb`) and any kernel OOM kills inside redroid (`runs.oom_kills`,
also a run warning), shown in `/status`'s "Peak mem" column.

Docker keeps at most 3 × 10MB of log per container (the `x-logging` block
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

## Storage and retention

Every post also gets a `posted_at` column (parsed from its relative/absolute timestamp), which is
what the feed and DB are ordered by — not `scraped_at`, since the newest post is always scraped
_first_ within a run. Before storing a new card, the driver checks for an existing post by the same
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
and only the newest 12 hierarchy/screenshot pairs (and 12 failure logcats) are kept; any
`.xml`/`.jpg`/`.png`/`.txt` there older
than `DEBUG_RETAIN_DAYS` (default 7) is deleted at the end of every run. Other files in that
directory (e.g. scratch `test*.sqlite` databases) are never touched.

Saved images are WebP by default (`MEDIA_FORMAT=jpeg` switches back). Re-encoding real crops,
WebP came out ~36% smaller than JPEG at the default `MEDIA_QUALITY=95` and ~56% smaller at 90.
Switching formats needs no migration: the database stores each file's name, so earlier files keep
serving, and the orphan sweep and retention handle both extensions. One side effect: stories are
deduplicated by file bytes, so a story still live when you switch formats may be stored once more.
In the feeds, every image carries its `width`/`height`, each entry gets a `<media:thumbnail>` (the
cover image) for readers that show pictures in list view, and video/Reel titles start with ▶.

**Backups**: at the end of a run, once the newest backup is `BACKUP_EVERY_HOURS` old (default 24), the
scraper writes a consistent copy of the database to `BACKUP_DIR` (default `local/data/db/backups`)
and keeps the newest `BACKUP_KEEP` (default 7); `docker compose exec app python scraper.py backup`
takes one now. The expensive thing to lose is redroid's `/data`, with the logged-in session: stop
redroid and run `scripts/snapshot-android-data.sh [--keep N]` before changing the redroid image or
Instagram build, or a risky recovery step. It writes `local/data/backups/android-<time>.tar.gz`.

Stories live in their own `stories` table and `media/stories` subdirectory, but share `RETAIN_DAYS`
with posts (`MEDIA_MAX_MB`'s size cap only ever considers posts, not stories) — see "Stories" above.

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

Moved to [`docs/ROADMAP.md`](docs/ROADMAP.md), ordered by scope (small/medium/large). Also
published as part of the [project site](https://ivylikethevine.github.io/instadroid/) built from
`docs/` (see `.github/workflows/pages.yml`).
