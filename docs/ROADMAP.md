# Roadmap

What this repository could take next. Each item carries its size: S a config flag, one function, a
CI tweak or docs; M a feature across several parts of the scraper, compose or CI, or repeated
real-device work; L an open investigation, a new capture mechanism, or a change to the
container/process topology. A finished item is deleted; git history has it.

## Contents

- [Where it stands](#where-it-stands)
- [Order of work](#order-of-work)
- [Now](#now)
- [On hold](#on-hold)
- [Later](#later)
- [Decisions](#decisions)
- [Done](#done)

## Where it stands

As of 2026-11-20, branch `dev`.

- 10 items are open: 1 ready to start, 5 waiting on a device run, a real dump, a real rename, an
  arm64 host or the owner's call, and 4 large ones for later.
- Account groups, story OCR and the login backup are in the tree, with tests; story OCR's memory was
  measured in the image (`docs/OPERATIONS.md`). The login backup and restore held a session on the
  same install ([RUNS.md](RUNS.md#2026-11-20)); not run: a restore onto a fresh `/data`, and CI.
- Open question: whether a routine 2FA prompt may be answered without a person (4).

## Order of work

- 3, the one ready to start.
- 14 on the next device run; 7 and 13 once a real dump or a real rename allows.
- 8, 9, 10 and 12 when one is taken up; 11 once an arm64 host is at hand.

## Now

### 3. Solve challenges from a browser (M)

An optional compose profile running ws-scrcpy on loopback, so a "confirm it's you" challenge on a
headless server needs only an SSH tunnel, not adb and scrcpy on a desktop.

## On hold

### 4. Type authenticator codes at login (M)

`IG_TOTP_SECRET` (with a `_FILE` variant) enters a 2FA code when the login form asks for one. It
bends the rule that a failure needing a person holds the scraper
([ARCHITECTURE.md](ARCHITECTURE.md#design-principles)): a routine 2FA prompt on the account's own
login is arguably not a suspicious-login challenge, so it's the owner's call first.

### 7. Confirm the forced-update and restriction screens (M)

Two screens are recognised by wording no device run has shown yet, both in
`app/igprofiles/v424/selectors.py`. An installed build that Instagram refuses to run until it's
updated is replaced in place with a newer validated one (README.md's "First-time setup"), on
`update_texts`. A run holds itself on an action block or a locked, suspended or disabled account
([OPERATIONS.md](OPERATIONS.md#health-and-restarts)), on `restriction_texts`. Replace each with the
text of a real `update` or `restricted` debug dump, and promote that dump to a fixture, the first
time one turns up.

### 11. arm64 host support (L)

On an arm64 host, official `redroid/redroid` images run Instagram's arm64 code natively — no NDK
translation, sidestepping the whole compatibility matrix ([`COMPATIBILITY.md`](COMPATIBILITY.md)).
Needs a multi-arch app image (`platforms:` in `publish.yml`), and README.md's "Kernel modules
(Linux)" checked against an arm64 host's kernel. Waits on an arm64 host.

### 13. Turn on automatic username changes (L)

A suspected rename is applied without a person once the Following list has the new username and not
the old one, and a wrong suspicion can be dismissed ([README](../README.md#how-a-scrape-works)), but
only with `RENAME_AUTO_APPLY=1`. It stays off until a real rename has gone through it: none has been
seen on a device yet, and a wrong merge joins two accounts' histories for good. Still open either
way: an account none of whose stored posts are still in the feed, which only a visit to its profile
would catch.

### 14. Restore the login onto a fresh `/data` (M)

`scraper.py login-restore` kept the session on the same install ([RUNS.md](RUNS.md#2026-11-20)), but
the case it exists for is a fresh `/data` volume or a reset `/data/system`
([OPERATIONS.md](OPERATIONS.md#storage-and-retention)), where Android's keystore and device id are
new and Instagram may ask for a new login. One run: back up, start redroid on a new volume path,
install the same build, restore, then `scraper.py login`. Waits on a device run.

## Later

### 8. Reach the real Following feed without the switcher (L)

Under `gpu_mode=guest` the switcher's bottom sheet may not open, and the scraper then falls back to
Home — algorithmic, with suggested posts mixed in. No deep link does it on 450: of the links its
manifest and code name, `instagram://mainfeed` and `instagram://peoplefeed` open, alone or with a
`feed_type=following` or `variant=following` query, and so does
`https://www.instagram.com/?variant=following`, and each lands on Home;
`instagram://feed_favorites_list`, `instagram://self_following` and `instagram://pandroid_mainfeed`
don't resolve from outside the app (`scripts/scratch/try_deeplinks.py` repeats the check). What's
left is an explicit activity or fragment intent, unconfirmed to exist. (A followed-accounts
allowlist now filters the fallback's suggested posts after the fact — see README.md's
"Followed-accounts allowlist" — but reaching the real feed directly would still be cheaper than the
extra Following-list navigation that costs.)

### 9. Commit a real run's tape (L)

A run can be recorded as a tape of screens and actions and replayed through the navigation code
([TESTING.md](TESTING.md#tier-3-fixture-replay)), but the only tape in the test suite is one recorded
from `tests/fakedevice.py`. A real one would catch navigation drift (a moved tab, a new
interstitial) the way the promoted single screens catch parser drift. It needs `promote-dump`'s
pseudonymizing applied across a whole tape, the same alias for an account on every screen and in the
copied links, before one can go in a profile's `fixtures/`.

### 10. Several Instagram accounts (L)

One redroid and app pair per account through compose profiles, each with its own `/data` volume and
guard, ports, and 3g of memory.

### 12. Real video capture (L)

Still a poster-frame still — Reels/videos never get the actual video. Likely needs screen recording
rather than a screenshot, plus somewhere to store and serve a video file per post, and meaningfully
longer dwell time per video post (see README.md's "Staying under the radar") — a real cost/benefit
call, not just effort.

## Decisions

- Not doing: rewriting git history to remove leaked identifiers. The working tree was scrubbed (the
  list is in [PROFILES.md](PROFILES.md)'s leak scan), but older commits keep real usernames,
  captions and places; removing them would take a force-push and a fresh clone everywhere, which the
  owner decided against.
- Not doing: per-file SPDX headers. `REUSE.toml` covers the repository, so don't add one to a new
  file; the synced code of conduct keeps its CC-BY-4.0 header, which changes at its source.
- Not doing: full story-reel capture. Only a story's current frame is captured (see README.md's
  "Stories"): tapping to advance a story has been observed to eject the app to the OS launcher on
  this host once its queue is exhausted. Worth revisiting only with a materially different
  navigation approach, such as reading the tray's own `total` count to know exactly how many frames
  to expect, so the loop never has to discover exhaustion by tapping past the end.

## Done

Finished items are deleted; git history has them, and dated device runs are in [RUNS.md](RUNS.md).
