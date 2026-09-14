# Next: version-specific selector profiles

## Why

On 2026-09-14 a fresh redroid install pulled Instagram **446.0.0.49.77** from APKPure. Every
selector in `scraper.py` was written and tested against **445** (`445.0.0.45.83` is the version in
the test fakes, and `fixture_feed.xml` comes from that era), and 446 is suspected of breaking them.
Not yet confirmed: that needs a logged-in session on the device (there's no `.env` yet).

Goal: keep the 445 logic that's known to work, and add 446 support as a separate layer on top,
rather than editing the selectors in place and losing the 445 baseline.

## Design: one selector profile per Instagram major version

A **profile** is a class holding one Instagram version's selectors. The 446 profile subclasses the
445 one and overrides only what changed. The scraper picks a profile at connect time from the
version installed on the device.

The version key is the **major number** (`446.0.0.49.77` → `446`). Instagram ships a new major
roughly weekly; the minor parts don't change the UI.

### Layout

```
app/igprofiles/__init__.py   PROFILES registry, resolve(version) -> profile
app/igprofiles/v445.py       class V445: today's SELECTORS moved over as-is, plus stray hardcoded bits
app/igprofiles/v446.py       class V446(V445): SELECTORS = {**V445.SELECTORS, <only the changed keys>}
```

### Why a class, not just a dict of overrides

Some 445 logic isn't in `SELECTORS`. `parse_hierarchy()` has structure built in:
`action_bar_container`, `android:id/list`, `"Turn sound"`, header-before-media ordering. A dict
overlay can't express a structural change.

- **Step 1 moves only the data**: the `SELECTORS` dict and the stray strings.
- **Structural hooks come later, only when needed.** When 446 needs different *behaviour*, that
  specific piece becomes a profile method: 445 keeps the current code, 446 overrides it. Nothing is
  abstracted before a real difference exists.

### How call sites get the active profile

`activate_profile()` rebinds the module globals `SELECTORS` and `PROFILE`, so the ~40
`SELECTORS[...]` call sites stay unchanged. A global swap is acceptable here: the scraper is a
single-threaded loop, and the tests already work by monkeypatching module globals.

Activation points:

- **In `connect_device()`**, which every entry point goes through (`main`, `once`, `login`, `dump`).
- **Again at the end of `_install_instagram()`**, since an auto-install can change the version
  mid-run.

### Resolution rules

| Installed | Profile used | Warning? |
|---|---|---|
| exact match (446) | V446 | no |
| newer than any profile (447) | highest ≤ installed (V446) | yes, in the run's `warning`, shown on `/status` |
| older than all / unparseable / missing | nearest available | yes |
| `IG_SELECTOR_PROFILE=445` set | forced | logged |

The override lets you run the 445 selectors against a 446 device, the quickest way to see exactly
what broke. Each run records its profile in `runs.selector_profile`, next to `ig_version`.

Default for an unknown newer version is "warn and use the newest profile", not "refuse to scrape":
it keeps the feed alive when a release changes nothing, which is common.

### Preserving 445

- **All current tests are 445 tests.** An autouse fixture in `app/tests/conftest.py` pins them to
  `V445`, so they become the 445 regression suite with no edits.
- **Rule: 446 differences live in `V446`, never in edits to shared code.** Any change to shared
  code in `scraper.py` must keep the 445 suite green.
- **New 446 fixtures** go in `app/tests/fixtures/446/`, captured with `scraper.py dump` on the live
  device; parser tests for 446 run against `V446`.
- **`test_profiles.py`** covers the resolution table, the override, and that a subclass profile
  inherits every key it doesn't override.

## Order of work

1. **Refactor, no behaviour change.** Create the profiles, move selectors into `V445`, add
   activation, `runs.selector_profile`, the conftest pin, and a per-version APK cache (below). All
   existing tests pass unchanged. Safe to merge on its own.
2. **Add `V446`.** Start as an empty subclass. Log in on the device, `dump` the feed, login and
   story screens, and diff against what V445 expects. Add overrides key by key, each with a fixture
   test.
3. **Hooks.** Only if step 2 finds a structural change.

## Things to know

- **Switching Instagram versions on the device.** `IG_APK_VERSION` now defaults to
  `445.0.0.45.83`, and `scraper.py install [VERSION]` replaces whatever is installed, including a
  downgrade (`adb install -r -d`, allowed because redroid is a userdebug build). The saved login
  survives the replace, but an older build may reject data written by a newer one. Verified
  2026-09-14: 446.0.0.49.77 → 445.0.0.45.83 in place, fetched from APKPure.
- **APK cache per version.** `_fetch_instagram_apk()` used to reuse whatever sat in
  `local/data/apk` regardless of `IG_APK_VERSION`. A pinned version is now cached in its own
  `APK_CACHE_DIR/<version>/` folder so switching between 445 and 446 doesn't silently reinstall the
  wrong one. Unpinned (latest) keeps the existing top-level layout.
- **Step 2 needs a login first** (`.env` with `IG_USERNAME`/`IG_PASSWORD`, or by hand via scrcpy).
  To compare the two versions: log in on 445 and confirm a scrape still works (baseline), then
  `scraper.py install 446.0.0.49.77`, run with `IG_SELECTOR_PROFILE=445`, and `dump` what breaks.

## Progress

- [x] Step 1: profile refactor (`app/igprofiles/`, activation, `runs.selector_profile`, conftest
  pin, per-version APK cache, `scraper.py install`, 445 install default)
- [ ] Step 2: V446 overrides. Logged in on 445 (2026-09-14), but the first 445 baseline run
  pushed redroid into OOM kills and froze the host (CLAUDE.md, "Host freeze during a scrape at the
  2g limit"). That run's dumps didn't show broken 445 selectors: the feed switcher left an empty
  popup focused, so every hierarchy dump missed the feed.
  **Clean 445 baseline, 2026-09-14 10:37-10:45 PDT** (3g limit, 4 CPUs, `MAX_SCROLLS=5`,
  `MAX_STORIES_PER_RUN=2`): 445 selectors work. 2 stories, 2 posts (a Reel and a carousel with an
  extra slide), both with permalinks, captions, media and `ig_version=445.0.0.45.83`. Peak redroid
  memory 2.40GiB (80% of 3g, just under the 85% guard), 0 OOM kills, max host load 7.8, Instagram
  force-stopped at the end. Rough edges: an early "left Instagram while closing a sheet;
  relaunching", and Copy link often left the clipboard empty (6 of 8 attempts); both affected cards
  turned out to be already-saved posts and were merged, so nothing was lost. A full 25-screen run
  will likely reach the guard and stop early at 3g.
  **446 with the 445 selectors, 2026-09-14 10:47-10:49 PDT**: `scraper.py install 446.0.0.49.77`
  upgraded in place and the login survived. Stories still work (2 captured). Posts weren't reached:
  the memory guard stopped the run right after stories at "2756 of 3072 MiB", but that figure wrongly
  included reclaimable file cache (`docker stats` peaked at 2.31GiB). The guard now excludes
  `inactive_file`.
  **446 rerun with the 445 selectors, 2026-09-14 10:56-11:02 PDT** (fixed guard, first WebP build):
  the 445 selectors work on 446 as far as this run reached. 2 stories; 1 new post (a Reel, with
  permalink, caption, crop, `ig_version=446.0.0.49.77`); already-saved posts on later screens were
  recognized (seen-streak 2); cards per screen similar to the 445 run. Peak 2.21GiB (`docker
  stats`), guard not tripped, 0 OOM kills, max host load 9.6, Instagram stopped after. WebP
  confirmed live: the 1080x1883 Reel crop is 103KB and the stories 97-137KB, against 250-320KB for
  the 445 run's JPEG stories.
  Not yet covered on 446: a *new* photo or carousel capture (only a Reel was new), and one card on
  screen 3 logged "no crop: media node not found" (no dump saved; it turned out to be an
  already-stored post). Also seen in both runs: an already-saved post gets re-processed as new
  (thewhorrorshowlive twice, 6 failed Copy link attempts, then merged). `_post_key()` is username +
  caption, so the likely cause is the caption hashing differently truncated ("… more") vs expanded;
  unconfirmed.
  Next: a longer 446 run to see a new photo/carousel; if nothing breaks, add `V446(V445)` as an empty
  subclass so the "no selector profile" warning goes away.
- [ ] Step 3: structural hooks, if needed
