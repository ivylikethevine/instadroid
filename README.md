# instagram-rss

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

Instagram ships arm64 native code only. On an x86_64 host it **crashes at startup under redroid's
libndk translation** (tested v278, v360, v445), so the default is the Android Studio emulator on the
host, whose Google ARM translation runs it fine. The `redroid` compose service is kept behind a
profile for ARM hosts: `docker compose --profile redroid up -d` with `ADB_ADDR=127.0.0.1:5555`.

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
`login_screen.png` / `login_hierarchy.xml` in `data/debug`; finish that step by hand and re-run.
First-run interstitials (notifications, location, "set up on new device") are dismissed automatically.
Login and feed selectors live in `SELECTORS` in `driver/scraper.py`.

If a run reports `no posts parsed on first screen`, look at `data/debug/last_hierarchy.xml`
and `last_screen.png`, then adjust `SELECTORS`.
`docker compose exec driver python scraper.py dump` grabs a fresh dump any time.

## FreshRSS

Subscribe to `http://<host>:8000/instagram.xml` (set `PUBLIC_URL` in compose to whatever
FreshRSS can reach so image links resolve). Per-account feeds: `/instagram.xml?user=somebody`.
`/users` lists everyone seen so far.

## Staying under the radar

- Keep `POLL_MIN_HOURS` ≥ 2. Instagram tolerates a phone that checks in a few times a day; it does not
  tolerate one that scrolls every 15 minutes with metronome timing.
- `MAX_SCROLLS` 25 is roughly 15–20 posts per run on this feed layout. If you follow more than that
  posts-per-3-hours, raise the poll frequency slowly rather than scroll depth.
- Occasionally open `scrcpy` and poke around yourself; it helps, and you'll need it anyway for
  the "confirm it's you" challenges that appear a few times a year.

## Known limitations of v1

- Post identity is a hash of author + kind + caption + media description (dates are relative in
  the app and like counts change, so those are excluded), not the real shortcode. An edited caption
  shows up twice; two caption-less posts by one author with identical media text collapse into one.
  Adding "Share → Copy link" + clipboard read fixes this.
- Images are screenshot crops of whatever was on screen (first carousel slide, video poster frame,
  including any in-app overlay such as the audio label on videos).
- Videos/Reels get a still only.
