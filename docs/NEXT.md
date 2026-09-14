# Next: Instagram version profiles

## Why

Instagram ships a new major version roughly weekly, and any of them can move a resource-id or
change a card layout. On 2026-09-14 a fresh install pulled **446.0.0.49.77** while every selector had
been written against **445**. Rather than editing selectors in place and losing a known-good
baseline, each Instagram major version gets its own self-contained profile, and the scraper runs
exactly one of them.

- **Default:** `v445`, the validated baseline. `igprofiles.DEFAULT_PROFILE` moves forward as newer
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
    fixtures/      screen dumps for this version's tests (kept out of the Docker image)
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
| Test fixtures (hierarchy dumps) | `vXYZ/fixtures/` |
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
for instance). Every function in `scraper.py` that depends on Instagram's UI is marked `@versioned`:
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
silently do nothing. The shared code in `scraper.py` is the implementation for the default profile;
when a version needs something different, it goes in that version's directory, never as a
version check in shared code. `_post_key()` (post identity) is deliberately **not** versioned, so
stored posts dedupe across Instagram upgrades.

## Adding a version (e.g. backfilling 440-444)

1. **Fetch it.** `docker compose exec app python scraper.py install 444.x.y.z` (confirm APKPure
   still serves that build). A downgrade on a device that ran a newer build may need a fresh login.
2. **Create `app/igprofiles/v444/`.** `selectors.py` starting as `{**SELECTORS_445}`, and an
   `__init__.py` with `Profile(Profile445)` setting `major = 444`, `apk_version`, `notes`.
   `test_every_profile_meets_the_contract` picks the directory up automatically.
3. **Baseline run** with `IG_PROFILE=v444`, short (`MAX_SCROLLS=5`, `MAX_STORIES_PER_RUN=2`), and
   only with memory headroom (see CLAUDE.md's host-freeze section).
4. **Dump what differs** (`scraper.py dump`, and the automatic `last`/`empty_feed` dumps). Save the
   relevant ones under `v444/fixtures/`, override the changed selector keys or `@versioned`
   functions, and add tests that load that profile and its fixtures.

## Status

- [x] Profile system: directory profiles, loader with the 440 floor, `IG_PROFILE`, per-profile APK
  build, `@versioned` behavior overrides, contract tests, `scraper.py profiles`.
- [x] `v445`: validated.
- [ ] `v446`: partially validated (profile exists, inherits 445 unchanged). See the runs below.
- [ ] `v440`-`v444`: to backfill.

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

Open for 446:

- A *new* photo or carousel capture (only a Reel was new).
- One card logged "no crop: media node not found" (no dump; it was an already-stored post).
- In both 445 and 446 runs, an already-saved post gets re-processed as new (thewhorrorshowlive twice,
  6 failed Copy link attempts, then merged). `_post_key()` is username + caption, so the likely
  cause is the caption hashing differently truncated ("… more") vs expanded; unconfirmed.

Next: a longer `IG_PROFILE=v446` run to see a new photo/carousel; if nothing breaks, mark v446
validated and consider moving `DEFAULT_PROFILE` to it.
