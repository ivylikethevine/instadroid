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

1. **Fetch it.** `docker compose exec app python scraper.py install 444.x.y.z` (confirm APKPure
   still serves that build). A downgrade on a device that ran a newer build may need a fresh login.
2. **Create `app/igprofiles/v444/`.** `selectors.py` starting as `{**SELECTORS_445}`, and an
   `__init__.py` with `Profile(Profile445)` setting `major = 444`, `apk_version`, `notes`.
   `test_every_profile_meets_the_contract` picks the directory up automatically.
3. **Baseline run** with `IG_PROFILE=v444`, short (`MAX_SCROLLS=5`, `MAX_STORIES_PER_RUN=2`), and
   only with memory headroom (see CLAUDE.md's host-freeze section). Pass overrides with `-e`
   (`docker compose run --rm --no-deps -e IG_PROFILE=v444 -e MAX_SCROLLS=5 app python scraper.py
   once`) or put them in `.env`; compose no longer forwards shell variables for these.
4. **Dump what differs** (`scraper.py dump`, and the automatic `last`/`empty_feed` dumps). Check each
   against the parent profile first — `python scripts/promote_dump.py <dump> v445 <name>` prints what
   parses (no posts, or posts without captions, means drift) — then promote the useful ones under
   `v444` the same way. The script scrubs accounts, names, places and captions, and records what the
   parsers find in `<name>.expected.json` for `tests/test_replay.py`; read the leftover text it
   prints before committing. Override the changed selector keys or `@versioned` functions until the
   fixtures parse correctly, then re-record with `promote_dump.py --update v444`.

## Status

- [x] Profile system: directory profiles, loader with the 440 floor, `IG_PROFILE`, per-profile APK
  build, `@versioned` behavior overrides, contract tests, `scraper.py profiles`.
- [x] `v445`: validated.
- [x] `v446`: validated (inherits 445's selectors unchanged; photos, carousels, Reels, stories and
  permalinks all captured live, see the runs below). No `v446/fixtures/` yet, and
  `DEFAULT_PROFILE` moved to `v446`.
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

- **"no crop: media node not found"**, 4 times in a row on one Reel card (vogadvil), which was then
  stored with permalink and caption but no media file. No dump is taken on that path, so the card's
  hierarchy is still unseen; wiring `_dump_debug` in there is the next step.
- **Re-processing of a just-stored post**: 3 of the 6 new posts (a carousel, the Reel and a photo)
  came back on a later screen, failed Copy link three times each, and were then merged into the row
  stored moments earlier. This happens *within one run*, not just across runs, which fits the
  truncated-vs-expanded caption hashing theory for `_post_key()`; still unconfirmed.
- **One post without a permalink**: Copy link failed on every retry for one photo, so it's stored
  under a hash id.
- No `v446/fixtures/` yet: nothing in this run triggered a `last`/`empty_feed` dump to promote.

Done: `DEFAULT_PROFILE` is now `v446` (a fresh install fetches 446, and a
device still on 445 gets a mismatch warning until `scraper.py install`).
