# Roadmap

Ordered by scope, smallest first. Inside Small, Medium and Large, the most value for the size comes
first, and an item waiting on something outside the repository comes last. Inside Cleanup, the most
sites or time saved comes first, and medium items come last.

## Contents

- [Small](#small)
- [Medium](#medium)
- [Large](#large)
- [Cleanup](#cleanup)
  - [CI and scripts](#ci-and-scripts)

## Small

A config flag, one function, a CI tweak, or docs.

Nothing open.

Not doing: rewriting git history to remove leaked identifiers. The working tree was scrubbed (the
list is in [PROFILES.md](PROFILES.md)'s leak scan), but older commits keep real usernames, captions
and places; removing them would take a force-push and a fresh clone everywhere, which the owner
decided against.

Not doing: per-file SPDX headers. `REUSE.toml` covers the repository, so don't add one to a new file;
the synced code of conduct keeps its CC-BY-4.0 header, which changes at its source.

## Medium

A feature across several parts of the scraper, compose or CI, or repeated real-device work.

- **Back up and restore the login alone**: save `/data/data/com.instagram.android` (with `adb root`)
  so a fresh `/data` volume, another Android version's included, or a `/data/system` reset gets a
  working login without a new challenge. A device run has to show the session survives the copy:
  Instagram may tie it to Android's keystore or device id.
- **Solve challenges from a browser**: an optional compose profile running ws-scrcpy on loopback, so
  a "confirm it's you" challenge on a headless server needs only an SSH tunnel, not adb and scrcpy
  on a desktop.
- **Type authenticator codes at login**: `IG_TOTP_SECRET` (with a `_FILE` variant) enters a 2FA code
  when the login form asks for one. It bends the rule that a failure needing a person holds the
  scraper ([ARCHITECTURE.md](ARCHITECTURE.md#design-principles)): a routine 2FA prompt on the
  account's own login is arguably not a suspicious-login challenge, so it's the owner's call first.
- **Account groups**: a config file mapping accounts to groups, served as `?group=` feeds and OPML
  categories.
- **Readable text-only stories**: OCR of story frames into the entry text, searchable on `/browse`.
  Opt-in, and measured first against the app container's 256m limit.
- **Confirm the batched selectors and shells on a device**: tested only against `tests/fakedevice.py`.
  A run has to show that `textMatches` and `descriptionMatches` accept escaped candidates with `(?is)`
  on Android's regex, that `textContains` ignores case as the fake now does, that a batched `getprop`
  keeps a blank line for an unset property, that doctor's single marker-split shell reads correctly,
  and that `scraper.py tune` and the split `ensure_logged_in` and story capture behave as before.
- **Confirm the forced-update and restriction screens**: two screens are recognised by wording no
  device run has shown yet, both in `app/igprofiles/v424/selectors.py`. An installed build that
  Instagram refuses to run until it's updated is replaced in place with a newer validated one
  (README.md's "First-time setup"), on `update_texts`. A run holds itself on an action block or a
  locked, suspended or disabled account ([OPERATIONS.md](OPERATIONS.md#health-and-restarts)), on
  `restriction_texts`. Replace each with the text of a real `update` or `restricted` debug dump, and
  promote that dump to a fixture, the first time one turns up.

## Large

Open investigations, new capture mechanisms, or changes to the container/process topology.

- **Reach the real Following feed without the switcher**: under `gpu_mode=guest` the switcher's
  bottom sheet may not open, and the scraper then falls back to Home — algorithmic, with suggested
  posts mixed in. No deep link does it on 450: of the links its manifest and code name,
  `instagram://mainfeed` and `instagram://peoplefeed` open, alone or with a `feed_type=following` or
  `variant=following` query, and so does `https://www.instagram.com/?variant=following`, and each
  lands on Home; `instagram://feed_favorites_list`, `instagram://self_following` and
  `instagram://pandroid_mainfeed` don't resolve from outside the app (`scratch/try_deeplinks.py`
  repeats the check). What's left is an explicit activity or fragment intent, unconfirmed to exist.
  (A followed-accounts allowlist now filters the fallback's suggested posts after the fact — see
  README.md's "Followed-accounts allowlist" — but reaching the real feed directly would still be
  cheaper than the extra Following-list navigation that costs.)
- **Commit a real run's tape**: a run can be recorded as a tape of screens and actions and replayed
  through the navigation code ([TESTING.md](TESTING.md#tier-3-fixture-replay)), but the only tape in
  the test suite is one recorded from `tests/fakedevice.py`. A real one would catch navigation
  drift (a moved tab, a new interstitial) the way the promoted single screens catch parser drift. It
  needs `promote-dump`'s pseudonymizing applied across a whole tape, the same alias for an account
  on every screen and in the copied links, before one can go in a profile's `fixtures/`.
- **Several Instagram accounts**: one redroid and app pair per account through compose profiles,
  each with its own `/data` volume and guard, ports, and 3g of memory.
- **arm64 host support**: on an arm64 host, official `redroid/redroid` images run Instagram's arm64
  code natively — no NDK translation, sidestepping the whole compatibility matrix
  ([`COMPATIBILITY.md`](COMPATIBILITY.md)). Needs a multi-arch app image (`platforms:` in `publish.yml`), and README.md's
  "Kernel modules (Linux)" checked against an arm64 host's kernel.
- **Real video capture**: still a poster-frame still — Reels/videos never get the actual video. Likely needs screen recording rather than a screenshot,
  plus somewhere to store and serve a video file per post, and meaningfully longer dwell time per
  video post (see README.md's "Staying under the radar") — a real cost/benefit call, not just effort.
- **Full story-reel capture**: only a story's current frame is captured (see README.md's
  "Stories") — a deliberate scope decision, not a gap left for later, given that tapping to advance
  a story has been observed to eject the app to the OS launcher on this host once its queue is
  exhausted. Worth revisiting only with a materially different navigation approach (e.g. reading the
  tray's own `total` count to know exactly how many frames to expect, so the loop never has to
  discover exhaustion by tapping past the end).
- **Turn on automatic username changes**: a suspected rename is applied without a person once the
  Following list has the new username and not the old one, and a wrong suspicion can be dismissed
  ([README](../README.md#how-a-scrape-works)), but only with `RENAME_AUTO_APPLY=1`. It stays off
  until a real rename has gone through it: none has been seen on a device yet, and a wrong merge
  joins two accounts' histories for good. Still open either way: an account none of whose stored
  posts are still in the feed, which only a visit to its profile would catch.

## Cleanup

Simplifications from a whole-repository review. Each keeps behavior unless marked otherwise, and is
small unless sized. Items that change what users or CI see say so.

### CI and scripts

- **The suite runs twice on a pull request into main**: `coverage.yml`'s comment still runs what
  `ci.yml`'s test job runs. Have it read the test job's figures instead, which restructures its
  triggers. A push to main already hands on what the test job measured. Medium.
