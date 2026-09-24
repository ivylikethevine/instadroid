# Instagram version profiles

The dated device runs behind the decisions here are in the [run log](RUNLOG.md).

## Contents

- [Why](#why)
- [Design: profiles at change points](#design-profiles-at-change-points)
- [Adding a version](#adding-a-version)
- [Status](#status)
- [Leak scan (2026-09-14)](#leak-scan-2026-09-14)

## Why

Instagram ships a new major version roughly weekly, and any of them can move a resource-id or
change a card layout, and a fresh install can pull a build newer than the one the selectors were
written against. Rather than editing selectors in place and losing a known-good
baseline, what's specific to a range of Instagram versions lives in a self-contained profile, and
the scraper runs exactly one of them.

Most versions change nothing the scraper uses: 441-446 all ran on the 445 selectors unchanged. So a
profile exists **only where Instagram changed something**, and a build that changed nothing is just
recorded as validated with the profile that already covers it.

- **Floor:** 424 (`igprofiles.MIN_MAJOR`), the root profile `v424`, the oldest Instagram that runs on
  this image ([run log](RUNLOG.md)). Named `v440` until 2026-09-15; run log entries before then use the old name.
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

`new-profile` (`app/devtools/new_profile.py`) does the mechanical parts, and everything in it works on
an exact build. It runs on the host from the dev venv (`pip install --no-deps -e .` puts it on
the `PATH`), since it writes into `app/igprofiles/`. The steps that touch the
device go through `docker compose run` with this working tree's `app/` mounted into the container, so
a selector edit is live on the next run without rebuilding the image. What stays manual is deciding
what a changed selector or override should be.

1. **Find the exact build** on APKPure: `apkeep -l -a com.instagram.android -d apk-pure` (inside the
   app image) lists them. The major version alone isn't enough. The weekly `new-builds.yml` workflow
   keeps an issue open listing each major newer than every validated build (`check-new-builds`,
   `app/devtools/check_new_builds.py`).
2. **Baseline**: `new-profile baseline 447.0.0.x.y`. This is a device-driving run, so read
   the [device-run rules](CONTRIBUTING.md#running-against-a-real-device) and the host freeze in [INCIDENTS.md](INCIDENTS.md) first. Before asking for confirmation it refuses to start if:
   - the `app` service is running (its scraper loop drives the same device; `docker compose stop app`),
   - redroid is already over 60% of its memory limit,
   - or the host has less than 2GiB available.

   Then it installs that build (`scraper.py install <build>`, a `-r -d` downgrade when needed, which
   may need a fresh login) and does one run under whichever profile covers it, capped at
   `MAX_SCROLLS=5` and `MAX_STORIES_PER_RUN=2` (`--scrolls`, `--stories`; `--following` also visits the
   Following list; `--below-floor` runs a build older than every profile under the lowest one). The run uses a scratch database and media directory, so nothing reaches the real
   feed or FreshRSS.

   In capture mode (`PROFILE_CAPTURE_DIR`) the scraper saves every screen it visits: feed screens,
   the Home feed with its story tray, the feed switcher menu, the Following feed, a story viewer, a
   share sheet, the profile and Following list, and the login form. That's up to 3 of each, plus
   every failure dump. They land in `local/data/debug/profile-dev/447/dumps/` along with
   `baseline.log`.

3. **Check**: `new-profile check 447.0.0.x.y` (run automatically after a baseline). For
   each captured screen it lists the selector keys the scraper needs there that matched nothing, and
   what the parsers find under the covering profile and its parent. It also flags dumps with almost no
   Instagram UI (a popup holding focus, the launcher). The report is saved as `report.md`. Which keys
   belong to which screen is defined in `app/igprofiles/screens.py`.
4. **Only if something drifted, fork**: `new-profile fork 447.0.0.x.y` creates
   `app/igprofiles/v447/`, subclassing the profile that covers 447 now, with no validated builds. A
   required key missing from every dump of its screen is drift: find the new resource-id,
   content-desc or text in the dump (the screenshot next to it shows the screen) and override that key
   in `v447/selectors.py`. For a structural change, override the `@versioned` function as a method on
   `Profile`. Re-run `check` (no device needed), then `baseline --no-install` to try the fix live.
   `fork` refuses when the covering profile already has validated builds at or above that version,
   since the fork would take them over unchecked.
5. **Promote**: `new-profile promote 447.0.0.x.y` scrubs the best feed, Home feed and
   Following-list captures into the covering profile's fixtures as `feed_447.xml` etc., through
   `promote-dump` (`app/devtools/promote_dump.py`). Read the leftover text it prints before committing. `tests/test_replay.py`
   then pins both what those fixtures parse to and that each still has its screen's required keys.
6. **Validate**: `new-profile validate 447.0.0.x.y`. It checks for that version's
   fixtures replaying, and a baseline run of exactly that build that stored posts without an error.
   Then it adds the build to the covering profile's `validated` tuple and prints a
   `docs/COMPATIBILITY.md` row. Add an entry to the [run log](RUNLOG.md).
7. **Restore** the device before restarting the app service:
   `new-profile restore` installs the default build again.

`scraper.py once` records its run in the `runs` table like a scheduled run, which is what `check` and
`validate` read from the baseline's scratch database.

## Status

- [x] Profile system: directory profiles, loader with the 424 floor (440 until 2026-09-15), `IG_PROFILE`, `@versioned`
      behavior overrides, contract tests, `scraper.py profiles`.
- [x] Tooling for adding versions: `new-profile` (`app/devtools/new_profile.py`), capture mode, `igprofiles/screens.py`.
- [x] Profiles only where something changes (2026-09-14): one root profile, `v424`; the covering
      profile is chosen automatically; `validated` lists builds; fixtures per version.
- [x] Validated with `v424`: 424.0.0.49.64, 440.1.0.46.86, 441.0.0.43.81, 442.0.0.46.79, 443.0.0.48.82,
      444.0.0.46.85, 445.0.0.45.83, 446.0.0.49.77 (see the [run log](RUNLOG.md)). Replay fixtures for 424 and 440-445
      (none recorded for 446). 425-439 run with the "hasn't been validated" warning until each gets a baseline.
- [x] Floor moved to 424 and the default install pinned to 445 (2026-09-15).
- [ ] Retry 446; if it still crashes, drop it from `v424.validated` (tracked in [ROADMAP.md](ROADMAP.md)).
- [x] Old-build probe, `400.0.0.49.68`: installs, but crashes at native startup on every launch ([run log](RUNLOG.md)).
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
- an account name in the run log (then part of this file, now [RUNLOG.md](RUNLOG.md)).

None of the scrubbing misses found during the backfill (the own account's story tray item,
suggested-post headers, @mentions, collab headers, audio credits) ever reached git: the committed
fixtures were promoted after those fixes. The owner's GitHub handle in `docs/CODE_OF_CONDUCT.md` is
intentional.

**Still in git history** (older commits of `driver/scraper.py`, `driver/tests/`,
`app/igprofiles/v445.py`, `app/tests/test_parser.py`, `NEXT.md`/`docs/NEXT.md`): the same identifiers.
Removing them means rewriting published history, e.g. `git filter-repo --replace-text` with a
replacements file, then a force-push and re-cloning everywhere. That's the repository owner's call;
it hasn't been done ([ROADMAP.md](ROADMAP.md)).
