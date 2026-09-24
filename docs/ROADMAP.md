# Roadmap

Ordered by scope, smallest first.

## Small

A config flag, one function, a CI tweak, or docs.

- **Rewrite git history to remove leaked identifiers**: the working tree was scrubbed, but older
  commits still carry real usernames, captions and places (the list is in [PROFILES.md](PROFILES.md)'s
  leak scan). `git filter-repo --replace-text` with a replacements file, then a force-push and a
  fresh clone everywhere. The repository owner's call; not done yet. The maintainer has an offline
  runbook for it, kept outside the repository.
- **Report a blocked upstream as drift**: for a row it cannot read, `check_tool_versions.sh` prints
  `(could not read upstream releases)` and counts no problem, while `tool-versions.yml` blocks
  egress to a fixed host list, so a pin whose upstream lives on a host missing from that list reads
  as fine forever. Count a problem when every row one host serves went unread (a blocked host, not a one-off
  rate limit), naming the host in the tracking issue.

Done: Markdown lint and format checks, a link check (relative links on pull
requests, external links after merge and weekly), spell check (typos), container image scanning (Trivy:
advisory in CI, blocking at release), dependency review on pull requests, shell formatting (shfmt), import boundaries
(import-linter), hashed dependency locks (pip-compile) and a `no_media_node` debug dump on the
"media node not found" path. Earlier: credentials from a file, caption hashtag/mention links, optional feed
auth, the compatibility table ([`COMPATIBILITY.md`](COMPATIBILITY.md)) and the committed OpenAPI
spec.

## Medium

A feature across several parts of the scraper, compose or CI, or repeated real-device work.

- **Retry Instagram 446**: 446.0.0.49.77 is in `v424.validated` but has crashed on launch since
  2026-09-15 (a native `SIGSEGV` in `RenderThread`; [run log](RUNLOG.md)), so `igprofiles.DEFAULT_BUILD`
  is pinned to 445. Retry it; if it still crashes, drop it from `v424.validated` and update its
  [`COMPATIBILITY.md`](COMPATIBILITY.md) row. If it works, promote its fixtures too: none have been
  recorded for 446.
- **Baseline what hasn't been checked yet**: no capture-mode baseline has covered the own profile,
  the Following list (`--following`) or the login form, so their required selector keys are unchecked
  on every build. And 425-439 run with the "hasn't been validated" warning until each gets a
  `new-profile baseline`/`validate`.
- **Instagram update path**: install-on-missing is automatic (see README.md's "First-time setup"),
  but an _outdated_ install isn't handled yet — detect the forced "update Instagram" screen (as a
  challenge-style stop) and reuse `install.install_instagram()` (`app/instadroid/install.py`)
  with a newer validated build (`new-profile baseline`/`validate`, or `fork` if it drifted), plus a `scraper.py dump` smoke check, keeping the previous xapk in
  `APK_CACHE_DIR` for rollback.
- **OpenSSF Best Practices badge**: Scorecard is wired up (`.github/workflows/scorecard.yml` and the
  README badge). What's left is bestpractices.dev, a manual self-certification questionnaire rather
  than a CI job, plus the Scorecard checks still open: branch protection and fuzzing. The answer sheet
  and the open checks are in [`OPENSSF-IMPROVEMENTS.md`](OPENSSF-IMPROVEMENTS.md).
- **Resource-id check for new Instagram builds in CI**: `.github/workflows/new-builds.yml` already opens
  an issue weekly when APKPure lists a major version newer than every validated build
  (`check-new-builds`, `app/devtools/check_new_builds.py`). Still to add: a static resource-id report in that issue.
  `aapt2 dump resources` on the new build's base APK (about a second) lists every selector resource id missing from
  it, and the ids added or removed since the newest validated build.
  - A research pass on 2026-09-14 ran this on the cached 443-446 builds. All 18 Instagram resource ids
    the selectors use were present in every one, matching the 445 selectors working unchanged on 446.
  - 12 of the 17 required keys in `igprofiles/screens.py` are resource ids. A removed id is near-certain
    drift, but text and content-desc keys can't be checked this way (those strings aren't in the APK),
    and layout changes don't show up either.
  - It means downloading the ~140MB xapk in CI; confirm APKPure serves GitHub's IP addresses. No
    account, session, dump or APK may end up in a cache or artifact: in a public repo, anyone can read
    both.

## Large

Open investigations, new capture mechanisms, or changes to the container/process topology.

- **Posts processed twice in one run, and Copy link misses**: in the 446 validation run, 3 of 6 new
  posts came back on a later screen, failed Copy link three times each, and were merged into the rows
  stored moments earlier ([run log](RUNLOG.md)). `_post_key()` (`app/instadroid/parsing.py`) now keys
  on the caption's first line with the "…" stripped, cut to 40 characters, with a rehash migration;
  no device run has confirmed it fixes the repeats. Copy link also often leaves the clipboard empty
  (6 of 8 attempts in the 445 baseline), mostly on cards already back on screen, and a post whose
  every retry fails is stored under a hash id (README.md's "Known limitations").
  `fetch_permalink()` (`app/instadroid/capture.py`) falls back to `dumpsys clipboard` over root adb
  and logs which source worked; the next real run decides whether that fixes it.
- **Reach the real Following feed without the switcher**: under `gpu_mode=guest` the switcher's
  bottom sheet may not open, and the scraper then falls back to Home — algorithmic, with suggested
  posts mixed in. Investigate a deep link or activity intent that opens Following directly;
  unconfirmed whether one exists. (A followed-accounts allowlist now filters the fallback's
  suggested posts after the fact — see README.md's "Followed-accounts allowlist" — but reaching the
  real feed directly would still be cheaper than the extra Following-list navigation that costs.)
- **Detect username changes automatically**: today a rename has to be noticed and reconciled by
  hand (`scraper.py rename <old> <new>`). Instagram's numeric user id never appears in the feed's
  accessibility tree, so detecting a rename would mean visiting each account's profile — extra
  in-app navigation and detection surface per run, which is why it wasn't done automatically here.
- **Full story-reel capture**: only a story's current frame is captured (see README.md's
  "Stories") — a deliberate scope decision, not a gap left for later, given that tapping to advance
  a story has been observed to eject the app to the OS launcher on this host once its queue is
  exhausted. Worth revisiting only with a materially different navigation approach (e.g. reading the
  tray's own `total` count to know exactly how many frames to expect, so the loop never has to
  discover exhaustion by tapping past the end).
- **Real video capture**: still a poster-frame still — Reels/videos never get the actual video. Likely needs screen recording rather than a screenshot,
  plus somewhere to store and serve a video file per post, and meaningfully longer dwell time per
  video post (see README.md's "Staying under the radar") — a real cost/benefit call, not just effort.
- **Replay whole navigation sequences**: the scraper is now split into `app/instadroid/` modules,
  and single recorded screens replay through the parsers (`promote-dump` scrubs a
  `DEBUG_DIR` dump into `igprofiles/vXYZ/fixtures/`, `tests/test_replay.py` checks it). The device
  flows still run against hand-written `tests/fakedevice.py` screens; recording a real run's
  sequence of dumps and taps, and replaying it through `fakedevice`, would catch navigation drift
  (a moved tab, a new interstitial) the same way.
- **arm64 host support**: on an arm64 host, official `redroid/redroid` images run Instagram's arm64
  code natively — no NDK translation, sidestepping the whole compatibility matrix
  ([`COMPATIBILITY.md`](COMPATIBILITY.md)). Needs a multi-arch app image (`platforms:` in `publish.yml`) and host docs (binder in
  the kernel).
