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
- **Save the whole story frame**: `stories.capture_story_media()` starts the crop at the bottom of
  `reel_viewer_top_shadow`, a gradient far taller than the header it sits behind, so about the top
  seventh of every story is lost (1080x1657 saved of a 1080x1920 frame at the default display size).
  Save the full `reel_viewer_media_container`, and compute the perceptual hash and the id on the part
  below the shadow, so the header's changing age text still can't split one frame into two. Masking
  only the header's own nodes is a possible follow-up.
- **Match a story frame across accounts**: `stories._find_story_duplicate()` only compares a capture
  with the same account's stories, so one frame reshared by two accounts within a day is stored twice.
  Drop the username filter and keep the one-day window.
- **Retake a dark story frame**: `stories._is_blank_frame()` rejects only a near-black, flat frame, so
  a video story caught during its fade-in is stored. Take a second screenshot when the first is dark
  and keep the brighter one, without adding a device round trip to the path a normal frame takes
  (`capture_story()` is on the story's display timer).
- **Merge stored twin posts**: a migration for the rows the double capture below left behind: the
  same account and an identical non-weak caption, one row with a permalink and one with neither a
  permalink nor a date. Keep the permalink row and discard the twin's media. It goes in after that
  fix, so no new twins follow it.
- **Say what `/opml` subscribes to**: the outline lists the aggregate feed and every per-account feed,
  and a reader dedupes entries per feed, so importing all of it shows each post twice.
  [FRESHRSS.md](FRESHRSS.md) should say to keep one or the other, or `/opml` should take a parameter
  that leaves the aggregate out.

Done: Markdown lint and format checks, a link check (relative links on pull
requests, external links after merge and weekly), spell check (typos), container image scanning (Trivy:
advisory in CI, blocking at release), dependency review on pull requests, shell formatting (shfmt), import boundaries
(import-linter), hashed dependency locks (pip-compile) and a `no_media_node` debug dump on the
"media node not found" path. Earlier: credentials from a file, caption hashtag/mention links, optional feed
auth, the compatibility table ([`COMPATIBILITY.md`](COMPATIBILITY.md)) and the committed OpenAPI
spec.

## Medium

A feature across several parts of the scraper, compose or CI, or repeated real-device work.

- **Posts captured twice in one run**: `_post_key()` (`app/instadroid/parsing.py`) hashes the
  caption's first 40 characters, but a collapsed caption can show fewer than that before "… more",
  with its line breaks flattened to spaces, so a card hashes differently once
  `capture.expand_caption()` has expanded it in place. The next dump takes it for a new card: it is
  cropped again, Copy link fails every retry, and it is merged into the row stored moments earlier, or
  stored as a second row when its header has scrolled off. A shorter key isn't the fix: distinct posts
  from one account often open with the same words.
  - Record every key a post is seen under: add the expanded caption's hash to the run's handled set
    straight after expansion, and store both hashes (a second column or an alias table) so a later run
    recognises either.
  - `fetch_permalink()` (`app/instadroid/capture.py`) reads a shortcode equal to the last one it handed
    out as a stale clipboard, which is also what copying the same post's link twice looks like. When the
    row stored under that shortcode has the card's author and caption, count the card as already
    stored instead of retrying.
  - `db.find_duplicate()` returns nothing for a card with no date, which the header-less top card is.
    Fall back to the same author and an identical non-weak caption within `RETAIN_DAYS`, and skip a
    header-less card that has neither a date nor a permalink.
  - Still unconfirmed by a device run: whether the `dumpsys clipboard` fallback fixes Copy link
    leaving the clipboard empty (6 of 8 attempts in the 445 baseline, [run log](RUNLOG.md)).
- **Stories that reshare a stored post**: a feed post shared to a story, by its own account or
  another followed one, repeats an entry the posts feed already has. Find the reshared-post sticker's
  node in the story viewer (no dump of one is recorded yet), hash that rectangle, and compare it with
  the covers and slides of recently stored posts; then drop the story or store it as a link to the
  post, which is still to decide. Matching the whole frame against stored images also works without
  the node, but needs template matching, which the app has no dependency for.
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
