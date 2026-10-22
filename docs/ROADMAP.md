# Roadmap

Ordered by scope, smallest first.

## Small

A config flag, one function, a CI tweak, or docs.

- **Rewrite git history to remove leaked identifiers**: the working tree was scrubbed, but older
  commits still carry real usernames, captions and places (the list is in [PROFILES.md](PROFILES.md)'s
  leak scan). `git filter-repo --replace-text` with a replacements file, then a force-push and a
  fresh clone everywhere. The repository owner's call; not done yet. The maintainer has an offline
  runbook for it, kept outside the repository. One followed account's handle that the scan missed
  sat in `tests/test_parser.py`'s Following-list fixture from commit
  `53886eda962c5bf816ed449310238dbb048d3f4b` until it was replaced in the working tree, so the
  replacements file needs it too.
- **A wider leak search**: a search for real identifiers only knows the ones in the local data of
  the machine it runs on, which is how a followed account's handle outlasted the 2026-09-14 scan.
  Make it a script that collects identifiers from every deployment's database, media folder names
  and raw dumps (including accounts since unfollowed and names only in a Following list), searches
  every tracked file and every added line of history, and also flags what no list can know: any
  `<user> posted a`, `<user>'s story` or `<user> and N others` text and any Instagram URL whose
  name or shortcode isn't one of the placeholders the fixtures use.
- **Refresh pings for other readers**: `FRESHRSS_REFRESH_URL` generalized to a list of URLs with a
  configurable method, covering Miniflux's feed-refresh API, with a short section per reader.
- **Alerts through Apprise**: `ALERT_URL` POSTs to ntfy or a webhook; Apprise URLs would add
  Discord, Telegram, Matrix, Gotify and email behind the one setting.
- **Continuous fuzzing**: `tests/fuzz/fuzz_parsing.py` under ClusterFuzzLite instead of by hand
  ([OPENSSF-IMPROVEMENTS.md](OPENSSF-IMPROVEMENTS.md)).
- **The per-file SPDX headers come out**: `REUSE.toml` (`path = "**"`) and `LICENSES/MIT.txt`
  arrive with the next sync of the shared CI files and cover every file, and Scorecard's License
  check reads only `LICENSE.md`, so the `SPDX-License-Identifier` comment at the top of each file
  is redundant. Strip it from this repository's own files and stop adding it; the synced CI files
  change in their source, which the sync carries. Done when
  `git grep -l SPDX-License-Identifier` lists `REUSE.toml` alone.

Done: Markdown lint and format checks, a link check (relative links on pull
requests, external links after merge and weekly), spell check (typos), container image scanning (Trivy:
advisory in CI, blocking at release), dependency review on pull requests, shell formatting (shfmt), import boundaries
(import-linter), hashed dependency locks (pip-compile), a `no_media_node` debug dump on the
"media node not found" path, whole story frames, story frames matched across accounts, a retake of a
dark story frame, a story's header painted over, the clipboard emptied before Copy link, the twin-post merge migration, `/opml?aggregate=0`, an unreachable upstream host counted as tool-version drift, a story that only reshares a stored post dropped, a resource-id report in the new-builds issue, an in-place update when Instagram demands one, a hold on an action block or restricted account, a heartbeat URL, a host preflight and a deploy bundle on each release, a snapshot check before an Instagram update, a held-back shrunk Following list, a newer-validated-build alert, the schedule and disk use on `/status`, JSON Feed and feed filters. Earlier: credentials from a file, caption hashtag/mention links, optional feed
auth, the compatibility table ([`COMPATIBILITY.md`](COMPATIBILITY.md)) and the committed OpenAPI
spec.

## Medium

A feature across several parts of the scraper, compose or CI, or repeated real-device work.

- **Confirm the forced-update screen**: an installed build that Instagram refuses to run until it's
  updated is replaced in place with a newer validated one (README.md's "First-time setup"), but the
  screen is recognised by wording no device run has shown yet (`update_texts` in
  `app/igprofiles/v424/selectors.py`). Replace it with the text of a real `update` debug dump, and
  promote that dump to a fixture, the first time one turns up.
- **Confirm the restriction screens**: a run holds itself on an action block or a locked, suspended
  or disabled account ([OPERATIONS.md](OPERATIONS.md#health-and-restarts)), recognised by wording no
  device run has shown yet (`restriction_texts` in `app/igprofiles/v424/selectors.py`). Replace it
  with the text of a real `restricted` debug dump, and promote that dump to a fixture.
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
- **Search and a browse page**: SQLite FTS5 over captions and a `/browse` page of each account's
  timeline, so the stored history works as an archive and not only a feed; `scraper.py export` to
  static HTML or JSON keeps posts past `RETAIN_DAYS`.
- **Readable text-only stories**: OCR of story frames into the entry text, searchable with the
  above. Opt-in, and measured first against the app container's 256m limit.

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
- **Turn on automatic username changes**: a suspected rename is applied without a person once the
  Following list has the new username and not the old one, and a wrong suspicion can be dismissed
  ([README](../README.md#how-a-scrape-works)), but only with `RENAME_AUTO_APPLY=1`. It stays off
  until a real rename has gone through it: none has been seen on a device yet, and a wrong merge
  joins two accounts' histories for good. Still open either way: an account none of whose stored
  posts are still in the feed, which only a visit to its profile would catch.
- **Full story-reel capture**: only a story's current frame is captured (see README.md's
  "Stories") — a deliberate scope decision, not a gap left for later, given that tapping to advance
  a story has been observed to eject the app to the OS launcher on this host once its queue is
  exhausted. Worth revisiting only with a materially different navigation approach (e.g. reading the
  tray's own `total` count to know exactly how many frames to expect, so the loop never has to
  discover exhaustion by tapping past the end).
- **Real video capture**: still a poster-frame still — Reels/videos never get the actual video. Likely needs screen recording rather than a screenshot,
  plus somewhere to store and serve a video file per post, and meaningfully longer dwell time per
  video post (see README.md's "Staying under the radar") — a real cost/benefit call, not just effort.
- **Commit a real run's tape**: a run can be recorded as a tape of screens and actions and replayed
  through the navigation code ([TESTING.md](TESTING.md#tier-3-fixture-replay)), but the only tape in
  the test suite is one recorded from `tests/fakedevice.py`. A real one would catch navigation
  drift (a moved tab, a new interstitial) the way the promoted single screens catch parser drift. It
  needs `promote-dump`'s pseudonymizing applied across a whole tape, the same alias for an account
  on every screen and in the copied links, before one can go in a profile's `fixtures/`.
- **arm64 host support**: on an arm64 host, official `redroid/redroid` images run Instagram's arm64
  code natively — no NDK translation, sidestepping the whole compatibility matrix
  ([`COMPATIBILITY.md`](COMPATIBILITY.md)). Needs a multi-arch app image (`platforms:` in `publish.yml`) and host docs (binder in
  the kernel).
- **Several Instagram accounts**: one redroid and app pair per account through compose profiles,
  each with its own `/data` volume and guard, ports, and 3g of memory.
