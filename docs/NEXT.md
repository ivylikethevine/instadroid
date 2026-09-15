---
title: Version profiles
---

# Next: Instagram version profiles

## Why

Instagram ships a new major version roughly weekly, and any of them can move a resource-id or
change a card layout. On 2026-09-14 a fresh install pulled **446.0.0.49.77** while every selector had
been written against **445**. Rather than editing selectors in place and losing a known-good
baseline, what's specific to a range of Instagram versions lives in a self-contained profile, and
the scraper runs exactly one of them.

Most versions change nothing the scraper uses: 441-446 all ran on the 445 selectors unchanged. So a
profile exists **only where Instagram changed something**, and a build that changed nothing is just
recorded as validated with the profile that already covers it.

- **Floor:** 424 (`igprofiles.MIN_MAJOR`), the root profile `v424`, the oldest Instagram that runs on
  this image (run log). Named `v440` until 2026-09-15; run log entries before then use the old name.
- **Default install:** `igprofiles.DEFAULT_BUILD`, pinned to 445.0.0.45.83 while 446 crashes on launch
  (`igprofiles.default_build()`; `None` would mean the newest validated build).

## Design: profiles at change points

```text
app/igprofiles/
  __init__.py      available(), load(), covering(), select(), default_build(), fixture(); MIN_MAJOR, DEFAULT_BUILD
  base.py          BaseProfile, the contract every profile follows
  screens.py       which selector keys each screen needs (profile development, replay tests)
  v424/
    __init__.py    class Profile(BaseProfile): major, selectors, validated builds, notes, overrides
    selectors.py   the full selector dict
    fixtures/      scrubbed screen dumps + .expected.json replay records, one set per validated
                   version (feed_445.xml, home_feed_444.xml, ...), kept out of the image
  v4NN/            (only when Instagram NN changes something)
    __init__.py    class Profile(v424.Profile): only what differs
    selectors.py   {**v424 selectors, <changed keys>}
```

A profile vN covers every Instagram build from major N up to the next profile. The scraper runs
**the highest profile at or below the installed version** (`igprofiles.covering()`), so with only
`v424` today, every supported build runs under it. What lives in a profile:

| What                                                                               | Where                                                   |
| ---------------------------------------------------------------------------------- | ------------------------------------------------------- |
| Selectors (resource-ids, content-desc patterns, UI strings)                        | `vXYZ/selectors.py`                                     |
| Behavior that differs (parsing, navigation, capture, login)                        | methods on `Profile` (below)                            |
| The exact builds shown to work with it                                             | `Profile.validated` (on its own class, never inherited) |
| Test fixtures (hierarchy dumps and what they must parse to), per validated version | `vXYZ/fixtures/<screen>_<major>.xml`                    |
| Human notes (`scraper.py profiles` prints them)                                    | `Profile.notes`                                         |

`tests/test_profiles.py` enforces the shape: every profile except the lowest must differ from the one
it subclasses (in its selectors or an override method), and every validated build must be one its
profile actually covers.

### Choosing a profile

On every connect (and after an install) `versioning.activate_profile()` picks the profile covering
the installed version. Before a device is connected, the newest profile stands in. Warnings go to the
log and show on `/status` as a run warning:

- an installed major version with no build validated with its profile (e.g. a newer Instagram than
  anything checked so far): it runs anyway;
- an installed version below every profile: the lowest profile runs.

`IG_PROFILE` (`v424`, or just `424`) forces one profile instead, with a warning when it isn't the one
covering the installed version. A bad `IG_PROFILE` falls back to automatic selection with a warning.

`IG_APK_VERSION` overrides the build to install (`latest` = newest on APKPure). By default it's
`igprofiles.DEFAULT_BUILD` (445.0.0.45.83 today), or `IG_PROFILE`'s newest validated build when that
profile hasn't validated the default. `python scraper.py profiles` lists
each profile, the versions it covers, its validated builds and the default install.

Each run records the active profile in `runs.selector_profile` (e.g. `v424`), next to `ig_version`.

### Swapping behavior, not just data

Selectors alone can't express a structural change (`parse_hierarchy()` has card ordering built in,
for instance). Every function in `app/instadroid/` that depends on Instagram's UI is marked `@versioned`:
about 30 of them, covering login, feed navigation, the following list, post parsing, captions,
sheets, permalinks, carousels, avatars and stories. A profile replaces one by defining a method of
the same name, which receives the base implementation first:

```python
class Profile(Profile424):
    major = 447
    ...

    def parse_hierarchy(self, base, xml):
        posts = base(xml)  # or ignore base entirely and reimplement
        ...
        return posts
```

Overrides are inherited, so a later profile subclassing another gets its fixes too. A method name
that matches no `@versioned` function is reported as a profile warning, so a typo can't silently do
nothing. The shared code in `app/instadroid/` is the root profile's implementation; when a version
needs something different, it goes in that version's profile, never as a version check in shared
code. `_post_key()` (post identity) is deliberately **not** versioned, so stored posts dedupe across
Instagram upgrades.

**Backfilling below the lowest profile** (should the floor ever move down) inverts the chain: the
new root holds that older version's full selectors, and the old root becomes a subclass holding only
what changed after it.

## Adding a version

`scripts/new_profile.py` does the mechanical parts, and everything in it works on an exact build. It
runs on the host from the dev venv, since it writes into `app/igprofiles/`. The steps that touch the
device go through `docker compose run` with this working tree's `app/` mounted into the container, so
a selector edit is live on the next run without rebuilding the image. What stays manual is deciding
what a changed selector or override should be.

1. **Find the exact build** on APKPure: `apkeep -l -a com.instagram.android -d apk-pure` (inside the
   app image) lists them. The major version alone isn't enough.
2. **Baseline**: `python scripts/new_profile.py baseline 447.0.0.x.y`. This is a device-driving run, so
   read CLAUDE.md's host-freeze section first. Before asking for confirmation it refuses to start if:
   - the `app` service is running (its scraper loop drives the same device; `docker compose stop app`),
   - redroid is already over 60% of its memory limit,
   - or the host has less than 2GiB available.

   Then it installs that build (`scraper.py install <build>`, a `-r -d` downgrade when needed, which
   may need a fresh login) and does one run under whichever profile covers it, capped at
   `MAX_SCROLLS=5` and `MAX_STORIES_PER_RUN=2` (`--scrolls`, `--stories`; `--following` also visits the
   Following list). The run uses a scratch database and media directory, so nothing reaches the real
   feed or FreshRSS.

   In capture mode (`PROFILE_CAPTURE_DIR`) the scraper saves every screen it visits: feed screens,
   the Home feed with its story tray, the feed switcher menu, the Following feed, a story viewer, a
   share sheet, the profile and Following list, and the login form. That's up to 3 of each, plus
   every failure dump. They land in `local/data/debug/profile-dev/447/dumps/` along with
   `baseline.log`.

3. **Check**: `python scripts/new_profile.py check 447.0.0.x.y` (run automatically after a baseline). For
   each captured screen it lists the selector keys the scraper needs there that matched nothing, and
   what the parsers find under the covering profile and its parent. It also flags dumps with almost no
   Instagram UI (a popup holding focus, the launcher). The report is saved as `report.md`. Which keys
   belong to which screen is defined in `app/igprofiles/screens.py`.
4. **Only if something drifted, fork**: `python scripts/new_profile.py fork 447.0.0.x.y` creates
   `app/igprofiles/v447/`, subclassing the profile that covers 447 now, with no validated builds. A
   required key missing from every dump of its screen is drift: find the new resource-id,
   content-desc or text in the dump (the screenshot next to it shows the screen) and override that key
   in `v447/selectors.py`. For a structural change, override the `@versioned` function as a method on
   `Profile`. Re-run `check` (no device needed), then `baseline --no-install` to try the fix live.
   `fork` refuses when the covering profile already has validated builds at or above that version,
   since the fork would take them over unchecked.
5. **Promote**: `python scripts/new_profile.py promote 447.0.0.x.y` scrubs the best feed, Home feed and
   Following-list captures into the covering profile's fixtures as `feed_447.xml` etc., through
   `scripts/promote_dump.py`. Read the leftover text it prints before committing. `tests/test_replay.py`
   then pins both what those fixtures parse to and that each still has its screen's required keys.
6. **Validate**: `python scripts/new_profile.py validate 447.0.0.x.y`. It checks for that version's
   fixtures replaying, and a baseline run of exactly that build that stored posts without an error.
   Then it adds the build to the covering profile's `validated` tuple and prints a
   `docs/COMPATIBILITY.md` row. Add a run log entry below.
7. **Restore** the device before restarting the app service:
   `python scripts/new_profile.py restore` installs the default build again.

`scraper.py once` records its run in the `runs` table like a scheduled run, which is what `check` and
`validate` read from the baseline's scratch database.

## Status

- [x] Profile system: directory profiles, loader with the 424 floor (440 until 2026-09-15), `IG_PROFILE`, `@versioned`
      behavior overrides, contract tests, `scraper.py profiles`.
- [x] Tooling for adding versions: `scripts/new_profile.py`, capture mode, `igprofiles/screens.py`.
- [x] Profiles only where something changes (2026-09-14): one root profile, `v424`; the covering
      profile is chosen automatically; `validated` lists builds; fixtures per version.
- [x] Validated with `v424`: 424.0.0.49.64, 440.1.0.46.86, 441.0.0.43.81, 442.0.0.46.79, 443.0.0.48.82,
      444.0.0.46.85, 445.0.0.45.83, 446.0.0.49.77 (see the run log). Replay fixtures for 424 and 440-445
      (none recorded for 446). 425-439 run with the "hasn't been validated" warning until each gets a baseline.
- [x] Floor moved to 424 and the default install pinned to 445 (2026-09-15).
- [ ] Retry 446; if it still crashes, drop it from `v424.validated`.
- [x] Old-build probe, `400.0.0.49.68`: installs, but crashes at native startup on every launch (run log).
- [x] Leak scan of fixtures, tests, docs and git history (below); working tree cleaned, history not rewritten.

## Leak scan (2026-09-14)

Searched every tracked file, and every added line in all 74 commits of git history. The search
covered:

- 130 real identifiers collected locally: the usernames, captions, shortcodes and places in the real
  and scratch databases, the handles and display names in raw debug dumps, and the logged-in
  account's username;
- patterns: `<user> posted a`, `<user>'s story`, Instagram post and profile URLs, display names in
  media descriptions, @handles, email addresses and phone numbers.

It also checked for committed images, databases, APKs or raw dumps: none, ever.

**Found and replaced in the working tree:**

- account names and places from real posts in `v440/selectors.py` (now `v424/`) comments and in `test_parser.py`
  (four usernames, a display name, a venue, two permalink shortcodes);
- a real city and state in the 445 feed fixture and two tests;
- an account name in this file's run log.

None of the scrubbing misses found during the backfill (the own account's story tray item,
suggested-post headers, @mentions, collab headers, audio credits) ever reached git: the committed
fixtures were promoted after those fixes. The owner's GitHub handle in `docs/CODE_OF_CONDUCT.md` is
intentional.

**Still in git history** (older commits of `driver/scraper.py`, `driver/tests/`,
`app/igprofiles/v445.py`, `app/tests/test_parser.py`, `NEXT.md`/`docs/NEXT.md`): the same identifiers.
Removing them means rewriting published history, e.g. `git filter-repo --replace-text` with a
replacements file, then a force-push and re-cloning everywhere. That's the repository owner's call;
it hasn't been done.

## Run log

**445 baseline, 2026-09-14 10:37-10:45 PDT** (3g limit, 4 CPUs, `MAX_SCROLLS=5`,
`MAX_STORIES_PER_RUN=2`): the 445 selectors work. 2 stories, 2 posts (a Reel and a carousel with an
extra slide), both with permalinks, captions, media and `ig_version=445.0.0.45.83`. Peak redroid
memory 2.40GiB (80% of 3g), 0 OOM kills, max host load 7.8. Rough edges: an early "left Instagram
while closing a sheet; relaunching", and Copy link often left the clipboard empty (6 of 8 attempts);
both affected cards were already-saved posts and were merged. An earlier attempt that morning ran
into OOM kills at the old 2g limit and froze the host (CLAUDE.md); its dumps showed an empty feed
switcher popup holding focus, not broken selectors.

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

Still open (none of these are 446-specific; all were seen on 445 too):

- **"no crop: media node not found"**, 4 times in a row on one Reel card, which was then
  stored with permalink and caption but no media file. No dump is taken on that path, so the card's
  hierarchy is still unseen; wiring `_dump_debug` in there is the next step.
- **Re-processing of a just-stored post**: 3 of the 6 new posts (a carousel, the Reel and a photo)
  came back on a later screen, failed Copy link three times each, and were then merged into the row
  stored moments earlier. This happens _within one run_, not just across runs, which fits the
  truncated-vs-expanded caption hashing theory for `_post_key()`; still unconfirmed.
- **One post without a permalink**: Copy link failed on every retry for one photo, so it's stored
  under a hash id.
- No `v446/fixtures/` yet: nothing in this run triggered a `last`/`empty_feed` dump to promote.

**Backfill with `scripts/new_profile.py`, 2026-09-14 15:08-15:43 PDT** (3g limit, 4 CPUs, capture mode,
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
only where something changes" plan above. Not captured (so unchecked): the own profile, the Following
list (`--following` wasn't used) and the login form (the session held). Two things learned:
`timestamp` is off screen whenever a tall Reel fills it, so it's now optional on feed screens; and
`promote_dump.py` needed more scrubbing rules (see the leak scan plan). The already-known Copy link
clipboard misses on a card that's back on screen showed up here too.

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

Done at the time: `DEFAULT_PROFILE` became `v446` (since replaced by automatic selection; a fresh install fetches 446, and a
device still on 445 gets a mismatch warning until `scraper.py install`).
