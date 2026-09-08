# instagram-rss (redroid edition)

A real, logged-in Instagram Android app running inside a `redroid` container,
driven by `uiautomator2`, publishing the chronological *Following* feed as Atom for FreshRSS.

```
redroid (Android 12 + Instagram APK)  <--ADB--  driver (uiautomator2, every 2.5-4.5h)
                                                      |
                                                  SQLite + cropped images
                                                      |
                                            feed (FastAPI -> /instagram.xml)  <-- FreshRSS
```

## Host requirements

* Linux host with binder support. Either the `binder_linux` module
  (`sudo modprobe binder_linux devices="binder,hwbinder,vndbinder"`; Debian/Ubuntu/Fedora stock kernels)
  or a kernel with the built-in Rust binder + binderfs (Arch 7.x: `grep binder /proc/filesystems`
  shows `binder`; redroid mounts binderfs itself, nothing to do). Proxmox LXC and macOS/Docker Desktop
  generally don't work.
* `adb` on the host. `scrcpy` is optional — only needed if Instagram throws a 2FA / "confirm it's you"
  challenge; `adb exec-out screencap -p > shot.png` is enough for checking state.
* On an x86_64 host, install the **x86_64** Instagram APK variant from apkmirror (Instagram publishes
  one). Docker Hub has no libndk/houdini redroid tags, so an arm64-only APK will not run. On an ARM
  host use the arm64-v8a build.

## First-time setup

```bash
cp .env.example .env                # fill in IG_USERNAME / IG_PASSWORD
docker compose up -d redroid
adb connect localhost:5555
adb install instagram.apk           # x86_64 build on x86_64 hosts, see above
docker compose up -d --build
docker compose exec driver python scraper.py login    # types the .env credentials into the login form
docker compose exec driver python scraper.py once     # first scrape, watch the output
```

The driver also runs the login step automatically at the start of every scrape, so once the
session is persisted in `./data/android` it is a no-op. If Instagram asks for a code or
"confirm it's you", the run aborts with a `login_screen.png` / `login_hierarchy.xml` in `data/debug`;
finish that step by hand (`scrcpy -s localhost:5555`) and re-run. Login selectors live in
`SELECTORS` in `driver/scraper.py` alongside the feed ones.

If it reports `no posts parsed on first screen`, look at `data/debug/last_hierarchy.xml`
and `last_screen.png`, then adjust `SELECTORS` in `driver/scraper.py`.
`docker compose exec driver python scraper.py dump` grabs a fresh dump any time.

## FreshRSS

Subscribe to `http://<host>:8000/instagram.xml` (set `PUBLIC_URL` in compose to whatever
FreshRSS can reach so image links resolve). Per-account feeds: `/instagram.xml?user=somebody`.
`/users` lists everyone seen so far.

## Staying under the radar

* Keep `POLL_MIN_HOURS` ≥ 2. Instagram tolerates a phone that checks in a few times a day; it does not
  tolerate one that scrolls every 15 minutes with metronome timing.
* `MAX_SCROLLS` 25 is roughly 40–60 posts per run. If you follow more than that posts-per-3-hours, raise
  the poll frequency slowly rather than scroll depth.
* Occasionally open `scrcpy` and poke around yourself; it helps, and you'll need it anyway for
  the "confirm it's you" challenges that appear a few times a year.

## Known limitations of v1

* Post identity is a hash of author + date + caption, not the real shortcode, so a post edited
  after scraping shows up twice. Adding "Share → Copy link" + clipboard read fixes this.
* Images are screenshot crops of whatever was on screen (first carousel slide, video poster frame).
* Videos/Reels get a still only.
