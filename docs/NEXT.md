---
title: Version profiles
---

# Next: Instagram version profiles

## Why

Instagram ships a new major version roughly weekly, and any of them can move a resource-id or
change a card layout. On 2026-09-14 a fresh install pulled **446.0.0.49.77** while every selector had
been written against **445**. Rather than editing selectors in place and losing a known-good
baseline, each Instagram major version gets its own self-contained profile, and the scraper runs
exactly one of them.

- **Default:** `v446`, the newest validated version. `igprofiles.DEFAULT_PROFILE` moves forward as newer
  versions are validated.
- **Floor:** 440 (`igprofiles.MIN_MAJOR`). Profiles 440-444 are to be backfilled now that profiles
  can swap everything version-specific.

## Design: one directory per Instagram major version

```
app/igprofiles/
  __init__.py      loader: available(), load(), select(), fixture(); DEFAULT_PROFILE, MIN_MAJOR
  base.py          BaseProfile, the contract every profile follows
  v445/
    __init__.py    class Profile(BaseProfile): major, apk_version, selectors, notes, overrides
    selectors.py   the full selector dict
    fixtures/      scrubbed screen dumps + .expected.json replay records (kept out of the image)
  v446/
    __init__.py    class Profile(v445.Profile): only what differs
    selectors.py   {**v445 selectors, <changed keys>}
```

Everything specific to one Instagram version lives in its directory:

| What | Where |
|---|---|
| Selectors (resource-ids, content-desc patterns, UI strings) | `vXYZ/selectors.py` |
| The exact APK build `scraper.py install` and auto-install fetch | `Profile.apk_version` |
| Behavior that differs (parsing, navigation, capture, login) | methods on `Profile` (below) |
| Test fixtures (hierarchy dumps and what they must parse to) | `vXYZ/fixtures/` |
| Human notes on what's validated (`scraper.py profiles` prints them) | `Profile.notes` |

### Choosing a profile

`IG_PROFILE` names the directory (`v446`, or just `446`); empty means the default. **Nothing switches
automatically** on the installed Instagram version. The profile is the source of truth: if a
different major version is installed, the scraper warns (logged and shown on `/status` as a run
warning), suggesting `scraper.py install` or the matching `IG_PROFILE` when that profile exists. A
bad `IG_PROFILE` (unknown, below the floor, malformed) falls back to the default with a warning.

`IG_APK_VERSION` overrides the build to install (`latest` = newest on APKPure); by default it's the
profile's own `apk_version`. `python scraper.py profiles` lists every available profile.

Each run records the active profile in `runs.selector_profile` (e.g. `v445`), next to `ig_version`.

### Swapping behavior, not just data

Selectors alone can't express a structural change (`parse_hierarchy()` has card ordering built in,
for instance). Every function in `app/instadroid/` that depends on Instagram's UI is marked `@versioned`:
about 30 of them, covering login, feed navigation, the following list, post parsing, captions,
sheets, permalinks, carousels, avatars and stories. A profile replaces one by defining a method of
the same name, which receives the base implementation first:

```python
class Profile(Profile445):
    major = 446
    ...

    def parse_hierarchy(self, base, xml):
        posts = base(xml)  # or ignore base entirely and reimplement
        ...
        return posts
```

Overrides are inherited, so an older or newer profile subclassing another gets its fixes too. A
method name that matches no `@versioned` function is reported as a profile warning, so a typo can't
silently do nothing. The shared code in `app/instadroid/` is the implementation for the default profile;
when a version needs something different, it goes in that version's directory, never as a
version check in shared code. `_post_key()` (post identity) is deliberately **not** versioned, so
stored posts dedupe across Instagram upgrades.

## Adding a version (e.g. backfilling 440-444)

`scripts/new_profile.py` does the mechanical parts. It runs on the host from the dev venv, since it
writes into `app/igprofiles/`. The steps that touch the device go through `docker compose run` with
this working tree's `app/` mounted into the container, so a selector edit is live on the next run
without rebuilding the image. What stays manual is deciding what the new selectors or overrides
should be.

1. **Find the exact build** on APKPure (e.g. `444.0.0.x.y`; the major version alone isn't enough).
2. **Scaffold** it: `python scripts/new_profile.py scaffold 444.0.0.x.y`. This creates
   `app/igprofiles/v444/`, with a `Profile` subclassing the nearest existing profile (v445 for 444,
   then v444 for 443 once that exists, so each one inherits the fixes before it), selectors starting
   as the parent's, and `validated = False`. `--parent vXYZ` picks another parent. An unvalidated
   profile runs, with a warning on every run.
3. **Baseline**: `python scripts/new_profile.py baseline v444`. This is a device-driving run, so read
   CLAUDE.md's host-freeze section first. Before asking for confirmation it refuses to start if:
   - the `app` service is running (its scraper loop drives the same device; `docker compose stop app`),
   - redroid is already over 60% of its memory limit,
   - or the host has less than 2GiB available.

   Then it installs the profile's build (`scraper.py install`, a `-r -d` downgrade when needed, which
   may need a fresh login) and does one run capped at `MAX_SCROLLS=5` and `MAX_STORIES_PER_RUN=2`
   (`--scrolls`, `--stories`; `--following` also visits the Following list). The run uses a scratch
   database and media directory, so nothing reaches the real feed or FreshRSS.

   In capture mode (`PROFILE_CAPTURE_DIR`) the scraper saves every screen it visits: feed screens,
   the Home feed with its story tray, the feed switcher menu, the Following feed, a story viewer, a
   share sheet, the profile and Following list, and the login form. That's up to 3 of each, plus
   every failure dump. They land in `local/data/debug/profile-dev/v444/dumps/` along with
   `baseline.log`.

   `python scripts/new_profile.py new 444.0.0.x.y` does steps 2-4 in one go.
4. **Check**: `python scripts/new_profile.py check v444` (run automatically after a baseline). For
   each captured screen it lists the selector keys the scraper needs there that matched nothing, and
   what the parsers find under the new profile and its parent. It also flags dumps with almost no
   Instagram UI (a popup holding focus, the launcher). The report is saved as `report.md`. Which keys
   belong to which screen is defined in `app/igprofiles/screens.py`. A required key missing from
   every dump of its screen is drift: find the new resource-id, content-desc or text in the dump (the
   screenshot next to it shows the screen) and override that key in `v444/selectors.py`. For a
   structural change, override the `@versioned` function as a method on `Profile`. Re-run `check`
   (no device needed), then `baseline --no-install` to try the fix live.
5. **Promote**: `python scripts/new_profile.py promote v444` scrubs the first clean feed, Home feed
   and Following-list capture into `v444/fixtures/` through `scripts/promote_dump.py`. Read the
   leftover text it prints before committing. `tests/test_replay.py` then pins both what those
   fixtures parse to and that each still has its screen's required selector keys.
6. **Validate**: `python scripts/new_profile.py validate v444`. It checks for fixtures that still
   replay, and a baseline run of the right major version that stored posts without an error. Then it
   flips `validated = True` and prints a `docs/COMPATIBILITY.md` row. Update the profile's `notes`,
   and add a run log entry below.
7. **Restore** the device before restarting the app service:
   `python scripts/new_profile.py restore` installs `DEFAULT_PROFILE`'s build again.

`scraper.py once` records its run in the `runs` table like a scheduled run, which is what `check` and
`validate` read from the baseline's scratch database.

## Status

- [x] Profile system: directory profiles, loader with the 440 floor, `IG_PROFILE`, per-profile APK
  build, `@versioned` behavior overrides, contract tests, `scraper.py profiles`.
- [x] `v445`: validated.
- [x] `v446`: validated (inherits 445's selectors unchanged; photos, carousels, Reels, stories and
  permalinks all captured live, see the runs below). No `v446/fixtures/` yet, and
  `DEFAULT_PROFILE` moved to `v446`.
- [x] Tooling for adding versions: `scripts/new_profile.py` (scaffold, capture-mode baseline, selector
  check per screen, fixture promotion, validation), `igprofiles/screens.py`, `Profile.validated`.
- [x] `v444`, `v443`, `v442`: validated 2026-09-14 with the tooling, each unchanged from its parent
  (see the run log). Replay fixtures (`feed`, `home_feed`) promoted and reviewed.
- [ ] `v441`: baseline taken 2026-09-14, and `check` found every required key; promote and validate still to do.
- [ ] `v440`: scaffolded (`440.1.0.46.86`), baseline not taken yet.
- [ ] Profiles only where something changes (plan below).
- [ ] Old-build probe: can `400.0.0.49.68` install, launch and keep the login? (plan below)
- [ ] Scan all fixtures and committed example data for real data leaked from testing (plan below).

## Plan (paused 2026-09-14)

Work stopped after the v441 baseline. **Device state when paused:** redroid running, Instagram
441.0.0.43.81 installed, the `app` service stopped. Before starting `app` again, either finish the
steps below or put the default build back with `python scripts/new_profile.py restore`.

Builds APKPure serves (`apkeep -l -a com.instagram.android -d apk-pure`, 2026-09-14): 129, from
`360.0.0.52.192` to `446.0.0.49.77`. Every major from 360 to 446 is listed except 371, 376, 382, 386,
389, 406 and 418; 440-446 have one build each (`440.1.0.46.86`, `441.0.0.43.81`, `442.0.0.46.79`,
`443.0.0.48.82`, `444.0.0.46.85`). Listed isn't proven installable.

### 1. Finish v441

No device needed: `check v441` (its report is already in `local/data/debug/profile-dev/v441/`),
`promote v441`, read the leftover text, `validate v441`.

### 2. Profiles only where something changes

A profile that changes nothing shouldn't exist. Instead, the scraper uses **the highest profile
whose major is at or below the installed version**.

- **Profiles mark change points.** vN covers every build from N up to the next profile. The lowest
  profile holds the full selectors. Since 440-446 have all run on the 445 selectors unchanged, the
  only profile becomes `v440`: v445's selectors and fixtures move there, and v441-v446 are deleted.
- **Enforce it:** a test that every profile except the lowest differs from its parent, in its
  selectors or by defining an override method.
- **Automatic selection:** `activate_profile(installed)` picks the covering profile. `IG_PROFILE`
  becomes an optional override (empty = automatic). Before a device is connected, the newest profile
  is the provisional one. A build newer than every validated build runs with a warning, and one
  below the lowest profile is an error.
- **`validated` becomes a tuple of builds** on the covering profile (e.g. `440.1.0.46.86` through
  `446.0.0.49.77`), replacing the bool.
- **Fixtures stay per build**, named `<screen>_<major>.xml` (e.g. `feed_444.xml`), so the replay tests
  still prove every validated version parses. `test_replay.py` and `new_profile.validation_problems()`
  strip the suffix to find the screen.
- **Default install:** the newest validated build across all profiles, replacing `DEFAULT_PROFILE`
  and each profile's `apk_version`. `IG_APK_VERSION` still overrides it.
- **`scripts/new_profile.py` works on builds:**
  - `baseline <build>` runs under whichever profile covers the build; dev directories are keyed by
    major.
  - `check` is unchanged.
  - If nothing drifted, `validate <build>` appends the build to the covering profile's `validated`
    and promotes its fixtures there.
  - Only on drift does `fork <build>` create a new vN subclassing the covering profile (today's
    `scaffold`).
  - `restore` installs the newest validated build.
- **Also touches:** `igprofiles/__init__.py` (`select`, `load`, `DEFAULT_PROFILE`, `MIN_MAJOR` = the
  lowest profile), `base.py`, `versioning.py`, `install._apk_version()`, `scraper.py profiles` (show
  each profile's range and validated builds), `conftest.py` (pin `v440`), and the tests that name
  `v445`/`v446` (test_profiles, test_device_flows, test_parser, test_health, test_compat,
  test_profile_dev). Docs: this file's design section, README, CONTRIBUTING, `.env.example`
  (`IG_PROFILE`), COMPATIBILITY.
- **Backfilling below the lowest profile later** means the new root holds that version's full
  selectors, and the old root becomes a subclass holding only what changed.

### 3. v440 and the old-build probe

- **v440** through the new tooling: `baseline 440.1.0.46.86`, then `check`, then `validate` (or `fork`
  on drift). About 12 minutes of device time.
- **Probe `400.0.0.49.68`** (below the floor, so no profile and no scrape):
  `docker compose run --rm --no-deps -v "$PWD/app:/app:ro" app python scraper.py install 400.0.0.49.68`,
  then `scraper.py login` and `scraper.py dump`. Record whether it installs, whether it launches
  without crashing under the ARM translation, whether the login survives a 441 → 400 downgrade, and
  whether Instagram shows a forced "update" screen. A failed login means `scraper.py login` retypes
  the `.env` credentials, which can hit a challenge.
- Then `restore` (newest validated build), stop redroid, and `docker compose start app` if it should
  be running.

### 4. Scan for leaked real data

Look for real data that got into committed files:
- every `app/igprofiles/*/fixtures/*` (XML text, content-desc and hint attributes, and the
  `.expected.json` files);
- test files and the synthetic screens in `tests/fakedevice.py` and `test_device_flows.py`;
- docs examples (NEXT.md's run log names accounts, e.g. "some.account"), CLAUDE.md and README;
- anything else committed under `docs/` or `scripts/`;
- git history, not just the working tree.

Search for the real handles and names seen in local dumps and scratch databases
(`local/data/debug/profile-dev/*/posts.sqlite`, `local/data/db/posts.sqlite`), the logged-in
account's own username, email addresses, phone numbers, real permalink shortcodes, and caption
text. `scripts/promote_dump.py` scrubs what it recognises: during this backfill it missed the own
story tray item, suggested-post headers, @mentions, "Follow <name>", collab "a and b" headers and
Reel audio credits, each now fixed with a test in `test_profile_dev.py`. Earlier fixtures were
promoted before those fixes.


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

- **"no crop: media node not found"**, 4 times in a row on one Reel card (some.account), which was then
  stored with permalink and caption but no media file. No dump is taken on that path, so the card's
  hierarchy is still unseen; wiring `_dump_debug` in there is the next step.
- **Re-processing of a just-stored post**: 3 of the 6 new posts (a carousel, the Reel and a photo)
  came back on a later screen, failed Copy link three times each, and were then merged into the row
  stored moments earlier. This happens *within one run*, not just across runs, which fits the
  truncated-vs-expanded caption hashing theory for `_post_key()`; still unconfirmed.
- **One post without a permalink**: Copy link failed on every retry for one photo, so it's stored
  under a hash id.
- No `v446/fixtures/` yet: nothing in this run triggered a `last`/`empty_feed` dump to promote.

**Backfill with `scripts/new_profile.py`, 2026-09-14 15:08-15:43 PDT** (3g limit, 4 CPUs, capture mode,
`MAX_SCROLLS=5`, `MAX_STORIES_PER_RUN=2`, scratch database). redroid was started once and stepped down one
build at a time (446 → 444 → 443 → 442 → 441) with `baseline`. Every downgrade kept the login.

| Build | Profile | Result | Peak memory |
|---|---|---|---|
| 444.0.0.46.85 | `v444` (from v445) | 2 stories, 2 posts (a Reel, a photo), full captions, media, permalinks; Following feed reached through the switcher | 2048 MiB |
| 443.0.0.48.82 | `v443` (from v444) | same | 2179 MiB |
| 442.0.0.46.79 | `v442` (from v443) | same | 2153 MiB |
| 441.0.0.43.81 | `v441` (from v442) | same (baseline and check only; not promoted or validated yet) | 2088 MiB |

`check` found every required selector key on every captured screen: the feed switcher menu, the
Following feed, the Home feed and story tray, the story viewer, the share sheet and the feed. No
selector changed, so all of them are identical to v445, which is what prompted the "profiles
only where something changes" plan above. Not captured (so unchecked): the own profile, the Following
list (`--following` wasn't used) and the login form (the session held). Two things learned:
`timestamp` is off screen whenever a tall Reel fills it, so it's now optional on feed screens; and
`promote_dump.py` needed more scrubbing rules (see the leak scan plan). The already-known Copy link
clipboard misses on a card that's back on screen showed up here too.

Done: `DEFAULT_PROFILE` is now `v446` (a fresh install fetches 446, and a
device still on 445 gets a mismatch warning until `scraper.py install`).
