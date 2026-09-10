# instadroid (instagram via redroid to rss)

A real, logged-in Instagram Android app running in a headless Android emulator,
driven by `uiautomator2`, publishing the chronological _Following_ feed as Atom for FreshRSS.

```bash
Android emulator (Instagram APK)  <--ADB--  driver (uiautomator2, every 2.5-4.5h)
                                                    |
                                                SQLite + cropped images
                                                    |
                                          feed (FastAPI -> /instagram.xml)  <-- FreshRSS
```

## Which Android?

Instagram ships arm64 native code only. On an x86_64 host it **crashed at startup under redroid's
Android-11 libndk translation** (`abing7k/redroid:a11_ndk_amd`) across 3 tested APK versions, so
the Android Studio emulator on the host (Google's own ARM translation) became the default.

**Update 2026-09-10: redroid works.** Bumping the image to Android 13 with Google's NDK translation
as shipped in ChromeOS's ARC++ (`erstt/redroid:13.0.0_ndk_ChromeOS`) resolved the native-startup
crash: Instagram installs, logs in, and scrapes successfully — tested end-to-end (login, a full
scrape run, permalink capture, cropped media) with zero issues. `docker compose --profile redroid
up -d` with `ADB_ADDR=127.0.0.1:5555` is a validated, working alternative to the host emulator, and
being a plain container it's lighter-weight (no GPU-backed emulator required). One known rendering
quirk: the Following-feed switcher's bottom sheet doesn't open under this image's default
`androidboot.redroid_gpu_mode=guest`, so the driver falls back to scraping the Home feed — trying
`=host` (mirroring the AVD's own `-gpu host` requirement for bottom sheets) is the natural next
thing to test.

That same first attempt at running redroid **also caused a full kernel panic** on this specific
host, unrelated to Instagram compatibility — see `CLAUDE.md` for the root cause and the exact,
now-validated safe procedure before running the redroid profile here (or on any host you haven't
personally tested it on).

## Host requirements

- Android Studio SDK with the emulator and a Play Store system image (`~/Android/Sdk`), an AVD
  (default name `Pixel_10`, override with `AVD=...`), KVM, and a GPU the emulator can use
  (`-gpu host`; the software renderer leaves Instagram's bottom sheets blank, so the feed
  switcher never opens).
- Docker + compose for the driver and feed containers. The driver uses host networking to reach
  the emulator's ADB port.
- `adb` on the host. `scrcpy` is optional; `adb exec-out screencap -p > shot.png` is enough for checks.
- [`apkeep`](https://github.com/EFForg/apkeep) to fetch the Instagram APK from APKPure (apkmirror
  blocks scripted downloads).

## First-time setup

```bash
cp .env.example .env                 # fill in IG_USERNAME / IG_PASSWORD
./run-emulator.sh &                  # headless, lean; pins emulator-5556 (ADB on 5557)
adb -s emulator-5556 wait-for-device shell 'while [ "$(getprop sys.boot_completed)" != 1 ]; do sleep 2; done'
./tune-android.sh emulator-5556      # animations off, sync/location off, Google apps disabled

apkeep -a com.instagram.android -d apk-pure .
unzip -o com.instagram.android.xapk -d xapk
adb -s emulator-5556 install-multiple xapk/com.instagram.android.apk xapk/config.*.apk

docker compose up -d --build
docker compose exec driver python scraper.py login    # types the .env credentials into the login form
docker compose exec driver python scraper.py once     # first scrape, watch the output
```

The driver runs the login step at the start of every scrape, so once the session is saved in the
AVD it is a no-op. If Instagram asks for a code or "confirm it's you", the run aborts with a
`login_screen.jpg` / `login_hierarchy.xml` in `data/debug`; finish that step by hand and re-run.
First-run interstitials (notifications, location, "set up on new device") are dismissed automatically.
Login and feed selectors live in `SELECTORS` in `driver/scraper.py`.

If a run reports `no posts parsed on first screen`, look at `data/debug/last_hierarchy.xml`
and `last_screen.jpg`, then adjust `SELECTORS`.
`docker compose exec driver python scraper.py dump` grabs a fresh dump any time.

## How a scrape works

1. Log in if needed, open the Following feed (the switcher is retried; cold starts are slow).
2. Walk the accessibility tree screen by screen. A post is registered only once the bottom of its
   card (share button + caption/timestamp) is on screen, so it has a stable identity.
3. For each new post: crop the media from a screenshot, then tap Share → "Copy link" and read the
   clipboard. The shortcode becomes the post id and the feed links straight to the post. If the
   sheet fails to open it is retried on the next screen, then the post falls back to a content hash.
4. Stop after `STOP_AFTER_SEEN` consecutive already-stored posts or `MAX_SCROLLS` screens.

Taps are always made from a hierarchy dump taken immediately beforehand, and nothing is ever tapped
inside an open sheet except "Copy link" (a stray tap there could message a contact).

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt -r driver/requirements.txt -r feed/requirements.txt
ruff check . && ruff format --check .
PYTHONPATH=driver pytest driver/tests -q     # parser tests against a synthetic hierarchy fixture
PYTHONPATH=feed pytest feed/tests -q         # feed endpoint tests against a temp SQLite db
```

CI (`.github/workflows/ci.yml`) runs ruff, both test suites, `pip-audit` on every requirements
file (also weekly), shellcheck on the scripts, hadolint plus a build and smoke test of both images,
`docker compose config` for both profiles, and gitleaks. Dependabot watches pip, Docker base images
and GitHub Actions.

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
The first run after upgrading applies this merge once to the existing database.

Posts (and their media) older than `RETAIN_DAYS` (default 60, `0` keeps everything) are deleted at
the end of every scrape, along with any media file no row references any more, so disk use stays
flat. Debug dumps in `data/debug` are saved as JPEG and only the newest 12 are kept.

## Known limitations of v1

- When "Copy link" fails twice for a post, its id is a hash of author + caption (or media
  description) instead of the permalink shortcode.
- Images are screenshot crops of whatever was on screen (first carousel slide, video poster frame,
  including any in-app overlay such as the audio label on videos).
- Videos/Reels get a still only.
