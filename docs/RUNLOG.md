# Version profiles: run log

Dated device runs behind the [version profiles](PROFILES.md): baselines, validations and probes of
Instagram builds on the reference device. Newest entries go at the bottom. Profile names are as they
were at the time: the root profile was `v440` until 2026-09-15, when it became `v424`.

**445 baseline, 2026-09-14 10:37-10:45 PDT** (3g limit, 4 CPUs, `MAX_SCROLLS=5`,
`MAX_STORIES_PER_RUN=2`): the 445 selectors work. 2 stories, 2 posts (a Reel and a carousel with an
extra slide), both with permalinks, captions, media and `ig_version=445.0.0.45.83`. Peak redroid
memory 2.40GiB (80% of 3g), 0 OOM kills, max host load 7.8. Rough edges: an early "left Instagram
while closing a sheet; relaunching", and Copy link often left the clipboard empty (6 of 8 attempts);
both affected cards were already-saved posts and were merged. An earlier attempt that morning ran
into OOM kills at the old 2g limit and froze the host ([INCIDENTS.md](INCIDENTS.md)); its dumps
showed an empty feed switcher popup holding focus, not broken selectors.

**446 with the 445 selectors, 2026-09-14 10:47-10:49 PDT**: `scraper.py install 446.0.0.49.77`
upgraded in place and the login survived. Stories worked. Posts weren't reached: the memory guard
stopped the run at "2756 of 3072 MiB", a figure that wrongly included reclaimable file cache (fixed:
the guard now excludes `inactive_file`).

**446 rerun, 2026-09-14 10:56-11:02 PDT** (fixed guard, first WebP build): 2 stories and 1 new post
(a Reel with permalink, caption, crop, `ig_version=446.0.0.49.77`); already-saved posts on later
screens were recognized; cards per screen similar to the 445 run. Peak 2.21GiB, 0 OOM kills, max
host load 9.6. WebP confirmed live: the 1080x1883 Reel crop is 103KB and stories 97-137KB, against
250-320KB for the 445 run's JPEG stories.

**446 validation run, 2026-09-14 12:19-12:30 PDT** (`IG_PROFILE=v446`, `MAX_SCROLLS=15`,
`MAX_STORIES_PER_RUN=5`, 3g limit): clean — no error, no run warning. 3 new stories and 6 new posts:
3 photos, 1 carousel (cover plus 1 extra slide), 1 Reel, all `ig_version=446.0.0.49.77`, 5 of 6 with
permalinks. redroid sampled at 1.1-2.0GiB (at most 67% of 3g) during the run. This closes the
"no new photo/carousel on 446" gap, so v446 is marked validated. Note: the app image used for this
run predated that day's scraper changes (no `runs.cards_per_screen` columns in the DB afterwards),
which doesn't affect the selector result, since `igprofiles/` was unchanged. The container was
started as `python scraper.py` without `once`, so after the run it went on into the daemon loop's
sleep and had to be stopped by hand.

Still open (none of these are 446-specific; all were seen on 445 too; tracked in [ROADMAP.md](ROADMAP.md)):

- **"no crop: media node not found"**, 4 times in a row on one Reel card, which was then
  stored with permalink and caption but no media file. No dump is taken on that path, so the card's
  hierarchy is still unseen; wiring a debug dump in there is the next step (done 2026-09-16: a
  `no_media_node` dump via `diagnostics.dump_debug()`).
- **Re-processing of a just-stored post**: 3 of the 6 new posts (a carousel, the Reel and a photo)
  came back on a later screen, failed Copy link three times each, and were then merged into the row
  stored moments earlier. This happens _within one run_, not just across runs, which fits the
  truncated-vs-expanded caption hashing theory for `_post_key()`; still unconfirmed.
- **One post without a permalink**: Copy link failed on every retry for one photo, so it's stored
  under a hash id.
- No `v446/fixtures/` yet: nothing in this run triggered a `last`/`empty_feed` dump to promote.

**Backfill with `new_profile.py`, 2026-09-14 15:08-15:43 PDT** (3g limit, 4 CPUs, capture mode,
`MAX_SCROLLS=5`, `MAX_STORIES_PER_RUN=2`, scratch database). redroid was started once and stepped down one
build at a time (446 → 444 → 443 → 442 → 441) with `baseline`. Every downgrade kept the login.

| Build         | Profile            | Result                                                                                                              | Peak memory |
| ------------- | ------------------ | ------------------------------------------------------------------------------------------------------------------- | ----------- |
| 444.0.0.46.85 | `v444` (from v445) | 2 stories, 2 posts (a Reel, a photo), full captions, media, permalinks; Following feed reached through the switcher | 2048 MiB    |
| 443.0.0.48.82 | `v443` (from v444) | same                                                                                                                | 2179 MiB    |
| 442.0.0.46.79 | `v442` (from v443) | same                                                                                                                | 2153 MiB    |
| 441.0.0.43.81 | `v441` (from v442) | same (baseline and check only; not promoted or validated yet)                                                       | 2088 MiB    |

`check` found every required selector key on every captured screen: the feed switcher menu, the
Following feed, the Home feed and story tray, the story viewer, the share sheet and the feed. No
selector changed, so all of them are identical to v445, which is what prompted the "profiles
only where something changes" design in [PROFILES.md](PROFILES.md). Not captured (so unchecked): the own
profile, the Following list (`--following` wasn't used) and the login form (the session held). Two
things learned: `timestamp` is off screen whenever a tall Reel fills it, so it's now optional on feed
screens; and `promote_dump.py` needed more scrubbing rules (see the leak scan in
[PROFILES.md](PROFILES.md)). The already-known Copy link clipboard misses on a card that's back on
screen showed up here too.

**440 and an old-build probe, 2026-09-14 15:55-16:06 PDT**, after the change to profiles at change
points. `new_profile.py baseline 440.1.0.46.86` (downgrading from 441) ran under `v440`, chosen
automatically from the installed version: 2 stories, 2 posts with full captions, media and permalinks,
every required selector key on every captured screen, peak 1878 MiB. Validated with `v440` like the
rest, so 440-446 all run on the one root profile.

`400.0.0.49.68` (below the floor, no scrape) installed over 440 without trouble, but crashes at
native startup on every launch: `ExceptionInInitializerError`, caused by `RuntimeException: could not
hook fn signal: mprotect: errno: 13, Permission denied` in `libstartup.so`, plus
`NoClassDefFoundError: com.facebook.common.dextricks.DalvikInternals`. That's the same class of failure
the Android 11 image had, so builds this old are out on this image, and the 440 floor stands. Side
finding: `scraper.py login` printed "logged in: True" against the crashing build, because Instagram
was briefly in the foreground before dying; `ensure_logged_in()`'s foreground check doesn't confirm
the app stays up (fixed the same day: it now checks Instagram is still in front before calling the
session live, and raises a retryable DeviceNotReady otherwise). The device was then restored to 446.0.0.49.77.

**Old-build bisect, 2026-09-14 16:25-16:40 PDT** (install and launch only, no scrape; builds pre-downloaded
into `APK_CACHE_DIR` in parallel with the tests). 420.0.0.55.74 crashes at native startup exactly like
400 (`could not hook fn signal: mprotect: errno: 13` in `libstartup.so`). 430.0.0.36.80 and 425.0.0.47.61
both stay open, keep the login across the downgrades and reach the Home feed, with `on_feed()` and
`on_home_feed()` (the root profile's selectors) both matching. Continuing the bisect the same evening: 423.0.0.47.66 crashes the same way and
424.0.0.49.64 launches like 425, so **424 is the oldest Instagram that runs on this image** (the newest
build of each major was used). So the native-startup crash ends at 424, and 424+ is at least launchable; whether the 440 floor could move
down to 425 would take a capture-mode baseline of those builds.

**424 under the root profile, 2026-09-15 00:09-00:14 PDT.** A capped capture-mode baseline of
424.0.0.49.64 (`new_profile.py baseline --below-floor`, from a frozen copy of `app/` while the typing
pass was editing the working tree) ran under `v440` unchanged: 2 stories, 2 posts (a carousel and a
Reel) with permalinks and captions, every required selector key on every captured screen, peak
1963 MiB. So the 440 selectors work at least as far back as 424; moving the floor (`MIN_MAJOR`, the
root profile's name) down to 424 is a decision still to make. 425-439 would then run with the
"hasn't been validated" warning until each gets a baseline.

Restoring 446 afterwards (a 424 → 446 jump) wasn't smooth: the first launches landed back on the
launcher and Android logged an ANR for Instagram at over 200% CPU, apparently first-launch work on
data from 22 versions back under ARM translation. What followed, 00:15-00:42 PDT:

- **446 now crashes on launch.** Every launch reaches `MainTabActivity`, then dies within about 5 seconds:
  a native `SIGSEGV` in `RenderThread`, with an empty backtrace (translated ARM code), sometimes preceded
  by an ANR. A redroid restart didn't change it.
- **Moving the app's regenerable data aside didn't help either:** `cache`, `code_cache`, `lib-compressed`,
  `modules` and `app_overtheair` went to `/data/local/tmp/ig-424-leftovers` (still there), leaving login
  state alone.
- **The installed splits were consistent** (446's own base and `config.xxxhdpi`).
- **445.0.0.45.83 installed over the same data works**: logged in and on the Home feed within 20-30
  seconds, and again after a fresh boot.
- **446 installed over 445 still crashes**, so it's not simply data carried over from 424.

446 had run fine on this device earlier the same day, so the trigger is unknown: possibly server-side
(a feed item or experiment 446 can't render under guest GPU mode), possibly something the 424 run
left behind outside the moved directories. **The device was left on 445.0.0.45.83**, redroid stopped.

To do: retry 446 later. If it still crashes, drop it from `v424.validated`. The default install no
longer depends on it: `igprofiles.DEFAULT_BUILD` pins auto-install and `restore` to 445.0.0.45.83.

**Floor moved to 424, 2026-09-15.** The root profile `v440` became `v424` (`MIN_MAJOR = 424`) with
its selectors unchanged, and the 424 baseline above was promoted (`feed_424`, `home_feed_424`) and
validated. Promoting it turned up one scrubbing miss, a display name in a Reel's media description
on a card no parser returned (`Reel by <name>, 82 likes, ...`); `promote_dump.pseudonymize()` now
catches those, and no committed fixture had one. `DEFAULT_BUILD = "445.0.0.45.83"` replaced the
newest validated build as the default install.

Done at the time: `DEFAULT_PROFILE` became `v446` (since replaced by automatic selection of the
profile covering the installed version).
