# Roadmap

Ordered by scope, smallest first.

## Contents

- [Small](#small)
- [Medium](#medium)
- [Large](#large)
- [Cleanup](#cleanup)
  - [Device and scrape](#device-and-scrape)
  - [Support modules and shared](#support-modules-and-shared)
  - [Feed server](#feed-server)
  - [Profiles and devtools](#profiles-and-devtools)
  - [Tests](#tests)
  - [CI and scripts](#ci-and-scripts)

## Small

A config flag, one function, a CI tweak, or docs.

Nothing open.

Not doing: rewriting git history to remove leaked identifiers. The working tree was scrubbed (the
list is in [PROFILES.md](PROFILES.md)'s leak scan), but older commits keep real usernames, captions
and places; removing them would take a force-push and a fresh clone everywhere, which the owner
decided against.

Done: boolean settings that read `no`/`off` as on (one `_flag()` parser), the run budget's wait
matching `/status`, recording keeping the device caches, per-file SPDX headers replaced by
`REUSE.toml` (the synced code of conduct keeps its CC-BY-4.0 header, which changes at its source;
don't add one to new files), Markdown lint and format checks, a link check (relative links on pull
requests, external links after merge and weekly), spell check (typos), container image scanning (Trivy:
advisory in CI, blocking at release), dependency review on pull requests, shell formatting (shfmt), import boundaries
(import-linter), hashed dependency locks (pip-compile), a `no_media_node` debug dump on the
"media node not found" path, whole story frames, story frames matched across accounts, a retake of a
dark story frame, a story's header painted over, the clipboard emptied before Copy link, the twin-post merge migration, `/opml?aggregate=0`, an unreachable upstream host counted as tool-version drift, a story that only reshares a stored post dropped, a resource-id report in the new-builds issue, an in-place update when Instagram demands one, a hold on an action block or restricted account, a heartbeat URL, a host preflight and a deploy bundle on each release, a snapshot check before an Instagram update, a held-back shrunk Following list, a newer-validated-build alert, the schedule and disk use on `/status`, JSON Feed and feed filters, refresh pings for other readers (Miniflux), alerts through Apprise, continuous fuzzing (ClusterFuzzLite) and a leak search across deployments (`leak-scan`). Earlier: credentials from a file, caption hashtag/mention links, optional feed
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
  ([`COMPATIBILITY.md`](COMPATIBILITY.md)). Needs a multi-arch app image (`platforms:` in `publish.yml`), and README.md's
  "Kernel modules (Linux)" checked against an arm64 host's kernel.
- **Several Instagram accounts**: one redroid and app pair per account through compose profiles,
  each with its own `/data` volume and guard, ports, and 3g of memory.

## Cleanup

Simplifications from a whole-repository review. Each keeps behavior unless marked otherwise, and is
small unless sized. Items that change what users or CI see say so.

### Device and scrape

- **One selector query per candidate list**: `_challenge_present`, `_update_required`,
  `_sheet_open` (`app/instadroid/navigation.py`) and the `device.first()` callers run one
  `exists(timeout)` per candidate string, and every miss waits out its timeout (about 20 calls per
  `_prepare_app`). One `textMatches`/`descriptionMatches` query with N times the timeout keeps the
  worst case and returns on the first hit; an instant per-candidate check then picks the match in
  list order. `tests/fakedevice.py` needs `textMatches`. Medium.
- **`device.by_id(d, key, anchored=...)`** for the nine hand-built `resourceIdMatches=f".*:id/…"`
  selectors in `navigation.py` and `stories.py`, each keeping its current anchoring.
- **`ensure_logged_in`'s failure paths**: a `_raise_if_challenge` for the duplicated challenge block
  and a `_login_failure(...) -> NoReturn` for the seven dump-then-raise pairs.
- **`close_sheets` checks twice when nothing is open**: reuse the last `_sheet_open` result unless
  Back was pressed or the app relaunched (about 0.9 s, twice per card).
- **`fetch_permalink` re-checks "Copy link"** right after finding it (`app/instadroid/capture.py`).
- **`fetch_permalink` reports where it left the device**, so `_reopen_feed_if_left`
  (`app/instadroid/scrape.py`) checks only the feed mode instead of re-running `on_feed`.
- **One adb round trip for a device snapshot**: `device_snapshot` runs a `getprop` shell per
  property and doctor's `_device_section` five separate shells; batch each into one, and share a
  `device.parse_version_name` with `instagram_version`.
- **`_scrape_feed`'s loop state**: about 20 mutable locals across 236 lines; a `_FeedRun` dataclass
  and a `_handle_card` returning an enum in place of nested `break`/`continue`. Medium.
- **`capture.py` duplicates**: a `_card_on_screen(d, key)` for the two find-the-card-in-a-new-dump
  expressions, and `permalink_code(url)` as `_code_of(url) or url`.
- **Following queries**: a `db.following_set(con)` for the two allowlist reads, and one
  `COUNT(*), MAX(updated_at)` helper for the five copies in `app/instadroid/db.py`.
- **One `_add_columns` call for `posts`** in `db.py` instead of three, each with its own
  `PRAGMA table_info`.
- **Retry loops**: a `_wait_until(pred)` for navigation's two six-try polling loops and a
  `_reach_home(d, recover)` for the two Home-feed retry loops in `stories.py`.

### Support modules and shared

- **Cache reshare thumbnails across runs**: `drop_reshared_stories` (`app/instadroid/reshare.py`)
  decodes every stored cover and slide on each run that stored a story, thousands of WebP decodes in
  the 256m app container. Key a module-level cache on file name and mtime; measure its memory first.
  Medium.
- **`doctor.adb_run` shared**: move it where `diagnostics.py` and `tune.py` can use it for their
  hand-rolled `subprocess.run(["adb", "-s", …])` calls (`install._run_checked` stays).
- **`shared/control.py`'s file protocol**: private `_read`/`_write` helpers for the repeated
  mkdir-and-write and read-or-None blocks, and a `clear_run_now` so `instadroid/control.py` stops
  unlinking the request file itself.
- **`shared.control.minutes_since_last_run(con)`** for the latest-run query both processes run
  (`feedserver/queries.py`, `feedserver/control.py`, `instadroid/control.py`).
- **A shared run duration and result** for `/status` and `scraper.py doctor` (`status.py`,
  `doctor.py`), each keeping its own format; doctor's `has_column` plus `cell_str` is `opt_str`.
- **Retention in a few queries**: `prune_old_posts` reads each expired post's files separately; one
  `UNION` query and subquery deletes do it, and a `retain_cutoff()` replaces the three copies of the
  cutoff (`retention.py`, `db.py`).
- **One `tree_size()`** in `shared/` for `/status` and retention (retention then tolerates a file
  vanishing mid-walk), and one PRAGMA query for the database size.
- **`diagnostics.signatures_in(text)`** for the two crash-signature matches (`doctor.py`,
  `diagnostics.py`), and `short_error` for the logcat header's hand-trimmed error.
- **One `resized()` Pillow helper** for the `_Resizable` typing workaround copied in `stories.py`
  and `reshare.py`.
- **`tune_device` reads `tune_packages.txt` twice**; load it once.
- **One run-budget window**: a shared `window_starts()` for the parse-and-filter `status.py` and
  `scrape.py` both do, each keeping its arithmetic; and one `now` per `status_page`.

### Feed server

- **Per-account ETags count every account's slides**: `feed_signal` (`app/feedserver/queries.py`)
  counts the whole `media` table, so any new carousel turns every `?user=` feed's 304 into a 200.
  Scope the count to the user. Changes ETag values only.
- **A `count(con, sql)` helper** for the five "COUNT, or 0 before the table exists" copies in
  `queries.py`.
- **`render.feed_url(..., token=False)`** replaces `feeds._self_url`, byte-identical.
- **One `_respond(fmt, ...)`** for the Atom-or-JSON branch both feed builders repeat.
- **Avatars for the feed's accounts only**: `post_entries` reads every account's avatar; filter by
  the posts' usernames.
- **`render.profile_url(username)`** for the seven profile URLs, quoted in `feeds.py` and
  `render.py` but not in `entries.py`; quoting is a no-op for valid usernames.
- **`settings.ZONE`** computed once instead of an `env.zone()` lookup per timestamp.

### Profiles and devtools

- **One placeholder table**: `promote_dump`'s placeholder kinds are spelled out again in
  `leak_scan`'s `_PLACEHOLDER` regex and `_DUMP_KINDS`; define them once and derive both.
- **leak-scan loads the newest profile per dump file**; load it once in `collect`.
- **`igprofiles.fixtures_dir(name)` and a public `ROOT`** replace the `fixture(name, "")` workaround
  in `promote_dump.py` and `new_profile.py`, and `new_profile.PROFILES_DIR`.
- **`major_of` in `igprofiles/base.py`**, re-exported, instead of base's inline copies.

### Tests

- **A shell seam on `FakeDevice`**: `shell_replies={prefix: output or exception}` replaces about ten
  subclasses that only override `shell()`; `VersionedDevice` is redundant with the defaults.
- **`FakeDevice.fail_taps` and `clipboard_error`** replace the three tap-failing subclasses in
  `test_navigation.py` and the class-level clipboard patch in `test_capture.py`.
- **Feed database helpers**: a `feedclient.feed_sql()` and a shared `add_runs(db, runs, columns)`
  for about 27 connect-execute-commit blocks and eight hand copies of the legacy `runs` schema
  (`test_feed`, `test_health`, `test_status_page`, `test_alerts`, `test_control`); the legacy
  schemas stay hand-built.
- **`deviceflows.run_caps(...)`** for about 25 sites setting `MAX_STORIES_PER_RUN`, `MAX_SCROLLS`
  and `MAX_CAROUSEL_SLIDES` by hand, and the two local `one_screen` helpers.
- **One `support.insert_post`**, a keyword superset of `seed_post`, `insert_post` and
  `test_db._add_post`; a shared story-row helper; `db.store_following` in place of
  `test_post_identity`'s `_follow`.
- **`support.record_urlopen(monkeypatch, fail=...)`** for the eight or more fake `urlopen` handlers.
- **`fake_run(monkeypatch, stdout=, returncode=, raises=)`** for about nine nested
  `subprocess.run` fakes, with the missing/hanging pair parametrized.
- **Poll-loop stubs in `deviceflows`**: `stop_at_first_run` (three local `StopLoop` copies),
  `scrape_returns` and `recorded_run` (four copies), `connect_returns` (three copies).
- **`V424` and `FEED_445` defined once** in `support.py` instead of in seven files.
- **Shared CLI scaffolding**: `new_profile`'s host state, decline prompt, `dev_dir` and baseline
  fixtures between `test_profile_dev` and `test_scripts_cli`; the scraper CLI harness between
  `test_snapshot_check` and `test_scraper_cli`.
- **`test_db`'s setup**: `_legacy_db` for four inline copies, the `con` fixture and `_reopen_at` for
  five migration round trips, `sqlrows.scalar` for 19 `.fetchone()[0]` reads, and a
  `_fully_migrated` helper.
- **Parametrize near-copies** in `test_db` (record-run stats, refresh trios, the rename pair),
  `test_feed` (the 304 tests, the image pair) and `test_parser` (quiet hours, time distribution).
- **`test_feed` helpers**: an `unescaped()` for six `replace` chains, an `_add_story`, and three
  redundant in-function ElementTree imports removed.
- **`test_login` and `test_capture` helpers**: `_missing_ig`, `_first(calls, tool)`, `_cached_apk`,
  `_caption_device` and `deviceflows.top_card`.
- **Patches conftest already applies** (`TUNE_ON_CONNECT`, `wait_for_boot`, `DEVICE_TIMEZONE=""`)
  removed from `test_recording`, `test_tune` and `test_parser`, plus an `untuned` fixture.
- **Fixtures for repeated setup** in `test_permalink_backfill` (five tests) and the selector-drift
  baseline (`test_selector_drift`, `test_resilience`).
- **`test_leak_scan`'s git repository built once per module**: 12 git spawns down to 4.
- **Lesser test cleanups**: an in-process `FEED_MODE` fallback check instead of a subprocess, the
  unused `fast_offline` parameters in `test_navigation`, `test_stories`' repeated screenshot setup,
  and a `MEMINFO` constant in `new_profile.py` to patch instead of `Path`.

### CI and scripts

- **The suite runs twice on main**: `coverage.yml` re-runs what `ci.yml`'s test job just ran. Build
  the badge and HTML report in the test job and have `coverage.yml` download them; the pull-request
  comment half restructures triggers and can follow later. Medium.
- **One matrix job for lint and test** in `ci.yml`, which differ only in name and check group.
- **One copy of the `/data` version rule**: `scripts/preflight.sh` copies `android_major()` and the
  mismatch check from `guard-android-data.sh`; a read-only `--check` mode there replaces it.
  Preflight also fails when either version is empty and the guard doesn't, so `--check` keeps that.
- **One basedpyright run**: an explicit `include`/`exclude` in `pyproject.toml` stops pyright's
  default dot-directory exclude, so `check.sh` drops its second pass over `.github/scripts`.
- **`publish.yml`'s release notes as one heredoc** instead of about 35 `echo` lines.
- **`snapshot-android-data.sh` pruning in one container** instead of one per old snapshot, and one
  `tar` pass when `android.image` is missing.
- **Job-level `env:` in `cflite.yml`** for the four keys both steps repeat (`GITHUB_TOKEN` stays per
  step).
- **`ci.yml`'s Trivy step through `trivy_scan.sh`**, the third copy of its flags. Drops secret
  results from that job's advisory SARIF, so the owner's call.
- **`check.sh docker` once** in `ci.yml`'s docker job instead of `hadolint` and `compose` apart; a
  compose failure would then skip the build and smoke test.
- **Preflight sizing from `docker compose config`** instead of parsing `.env` and repeating
  compose's defaults.
- **`docs_drift.py` lists and reads the Markdown once** for both checks.
- **The `scratch/` scripts drop their `app/` `sys.path` entry**, which the editable install covers.
- **Keep or delete `scratch/reshare_census.py`**: no tracked file names it, and it last changed on
  2026-10-09. The docstring says what it answers; delete it if that question is settled.
