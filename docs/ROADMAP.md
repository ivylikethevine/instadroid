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

Done: Markdown lint and format checks, a link check (relative links on pull
requests, external links after merge and weekly), spell check (typos), container image scanning (Trivy:
advisory in CI, blocking at release), dependency review on pull requests, shell formatting (shfmt), import boundaries
(import-linter), hashed dependency locks (pip-compile), a `no_media_node` debug dump on the
"media node not found" path, whole story frames, story frames matched across accounts, a retake of a
dark story frame, a story's header painted over, the clipboard emptied before Copy link, the twin-post merge migration, `/opml?aggregate=0`, an unreachable upstream host counted as tool-version drift, a story that only reshares a stored post dropped, a resource-id report in the new-builds issue and an in-place update when Instagram demands one. Earlier: credentials from a file, caption hashtag/mention links, optional feed
auth, the compatibility table ([`COMPATIBILITY.md`](COMPATIBILITY.md)) and the committed OpenAPI
spec.

## Medium

A feature across several parts of the scraper, compose or CI, or repeated real-device work.

- **Confirm the forced-update screen**: an installed build that Instagram refuses to run until it's
  updated is replaced in place with a newer validated one (README.md's "First-time setup"), but the
  screen is recognised by wording no device run has shown yet (`update_texts` in
  `app/igprofiles/v424/selectors.py`). Replace it with the text of a real `update` debug dump, and
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
