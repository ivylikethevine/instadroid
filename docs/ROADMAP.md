---
title: Roadmap
---

# Roadmap

Ordered by scope, smallest first.

### Small

A config flag, one function, a CI tweak, or docs. Nothing open right now: credentials from a file,
caption hashtag/mention links, optional feed auth, the compatibility table
([`COMPATIBILITY.md`](COMPATIBILITY.html)) and the committed OpenAPI spec are done.

### Medium

A feature across several parts of the scraper, compose or CI, or repeated real-device work.

- **Backfill missing permalinks**: 17 of 35 stored posts have no permalink (16 of them from before
  `PERMALINK_RETRIES` existed). When an already-stored hash-id post is back on screen, try Copy
  link once and fill in its `url` — keeping its existing `id`, since the Atom entry id is derived
  from it and changing it would make FreshRSS show the post twice.
- **Guard `/data` against Android version mixing**: record the image tag in `local/data/android` on
  first boot and refuse to start a different Android major version against it — the appops.xml,
  idmap and telephony.db corruption in `CLAUDE.md` all came from exactly that.
- **Backups**: `sqlite3 .backup` of the posts database, plus a snapshot of redroid's `/data` before
  image or APK upgrades — the saved login session is the expensive thing to lose.
- **Manual-use lock and run-now**: a lock (file or endpoint) that keeps the scraper from starting
  while you're driving the device in scrcpy, and a rate-limited "scrape now" trigger.
- **Failure alerts**: push a notification (ntfy, Apprise, or a plain webhook) on a login challenge,
  N consecutive failed runs, or no new posts for X hours. A zero-dependency variant: a synthetic
  "scraper needs attention" entry in `/instagram.xml`, since the feed is already being read.
- **Instagram update path**: install-on-missing is automatic (see README.md's "First-time setup"),
  but an _outdated_ install isn't handled yet — detect the forced "update Instagram" screen (as a
  challenge-style stop) and reuse `install.install_instagram()` (`app/instadroid/install.py`)
  with the version profile's `apk_version` bumped (or a new `app/igprofiles/vXYZ/`), plus a `scraper.py dump` smoke check, keeping the previous xapk in
  `APK_CACHE_DIR` for rollback.
- **OpenSSF registration and badges**: two distinct things. Scorecard is automatable (a scheduled
  `ossf/scorecard-action` run publishing to the public dashboard plus a README badge) and this repo
  already scores well on several of its checks — every workflow action SHA-pinned,
  `persist-credentials: false` everywhere, CodeQL, gitleaks, a pinned base image, Dependabot — with
  signed/attested releases and a published security policy (`docs/SECURITY.md`) now closing more gaps
  (see README.md's "Releases"); branch protection and fuzzing remain open. Best Practices
  (bestpractices.dev) is a manual self-certification questionnaire, not a CI job, which is why it's
  listed here rather than wired into a workflow.
- **Profiles for Instagram 440-444**: 440 is the supported floor, and only `v445` (validated) and
  `v446` (both validated) exist today. The profile system can now swap everything version-specific, so each
  of 440, 441, 442, 443 and 444 gets its own `app/igprofiles/v44N/` directory: confirm APKPure still
  serves a build (`scraper.py install <version>`), start from the 445 selectors, take a short
  `IG_PROFILE=v44N` baseline run, and override only what differs, with fixtures from that version's
  dumps. `docs/NEXT.md` has the step-by-step. Going back in versions on one device means `-r -d`
  downgrades, which an older Instagram may reject with data a newer build wrote, so expect a fresh
  login.
- **Semi-automate new profile development**: Instagram ships a major version roughly weekly, so
  `docs/NEXT.md`'s "Adding a version" steps will recur. Script the mechanical parts into one
  command (e.g. `scraper.py new-profile <version>`): scaffold `app/igprofiles/vXYZ/` subclassing the
  current default, install that build, take a short capped baseline run (only with memory headroom),
  and run each screen's dump through `scripts/promote_dump.py` under the parent profile — which
  already reports what parses and saves scrubbed fixtures — plus a report of which selector keys
  matched nothing. The human step that stays is deciding
  what the new selectors or `@versioned` overrides should be, then marking the profile validated.

### Large

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
  and single recorded screens replay through the parsers (`scripts/promote_dump.py` scrubs a
  `DEBUG_DIR` dump into `igprofiles/vXYZ/fixtures/`, `tests/test_replay.py` checks it). The device
  flows still run against hand-written `tests/fakedevice.py` screens; recording a real run's
  sequence of dumps and taps, and replaying it through `fakedevice`, would catch navigation drift
  (a moved tab, a new interstitial) the same way.
- **arm64 host support**: on an arm64 host, official `redroid/redroid` images run Instagram's arm64
  code natively — no NDK translation, sidestepping the whole "Which Android?" compatibility matrix
  (README.md). Needs a multi-arch app image (`platforms:` in `publish.yml`) and host docs (binder in
  the kernel).
