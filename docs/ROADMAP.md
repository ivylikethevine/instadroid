---
title: Roadmap
---

# Roadmap

Ordered by scope, smallest first.

### Small

A config flag, one function, a CI tweak, or docs.

- **Credentials from a file**: `IG_PASSWORD_FILE` / Docker secrets instead of a plain environment
  variable.
- **Link hashtags and mentions in captions**: now that full captions are stored (see README.md's
  "How a scrape works"), hashtags and @mentions in them could be turned into links in the feed HTML.
- **Optional feed auth**: a token or basic auth, needed before `FEED_HOST=0.0.0.0` is safe —
  otherwise media from private accounts you follow is served to anyone on the LAN.
- **Document compatible Android image / Instagram version pairs**: partially done already —
  `CLAUDE.md`'s "What's validated" section already tracks which `erstt/redroid` tags work
  (`13.0.0_ndk_ChromeOS`) versus don't (`15.0.0_ndk_AVD`: binder ABI mismatch;
  `aureliolo/redroid:14.0.0_amd64_with_gapps`: no ARM translation at all; `abing7k`'s Android 11:
  Instagram crashes at native startup). What's still missing is a structured table cross-referencing
  specific Instagram APK versions against each image, kept current as Instagram updates — right now
  that history is narrative, not a lookup. The raw data now accumulates on its own: every run
  records the Instagram `versionName` and redroid image (`runs.ig_version` / `runs.redroid_image`).

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
  challenge-style stop) and reuse `_fetch_instagram_apk()`/`_install_instagram()` (`scraper.py`)
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
- **Replay tests and a module split**: `scrape_once()` and the other device flows now run in CI
  against `tests/fakedevice.py`, but its screens are hand-written. Replaying _recorded_ sequences —
  with a helper that promotes a `DEBUG_DIR` dump into a sanitised fixture — would catch real
  Instagram UI drift that synthetic screens can't. Splitting `scraper.py` (~2,500
  lines) into db/navigation/parsing/capture/retention modules (selectors already live in
  `app/igprofiles/`), with numbered migrations
  in place of ad-hoc `PRAGMA user_version` checks, is a precondition for the Rust evaluation below.
- **arm64 host support**: on an arm64 host, official `redroid/redroid` images run Instagram's arm64
  code natively — no NDK translation, sidestepping the whole "Which Android?" compatibility matrix
  (README.md). Needs a multi-arch app image (`platforms:` in `publish.yml`) and host docs (binder in
  the kernel).
- **Investigate a Rust rewrite**: evaluate rewriting the driver (uiautomator2 automation + parsing,
  ~1,000 lines of Python today) in Rust — worth weighing once the automation logic stabilizes, not
  before.
