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
- [ ] Step 2: V446 overrides (blocked on login)
- [ ] Step 3: structural hooks, if needed
