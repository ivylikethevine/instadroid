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
  dumps. `scripts/new_profile.py` automates all of that except choosing the overrides; `docs/NEXT.md`
  has the step-by-step. Going back in versions on one device means `-r -d`
  downgrades, which an older Instagram may reject with data a newer build wrote, so expect a fresh
  login.
- **Detect and scaffold new Instagram builds in CI**: a scheduled workflow on a GitHub-hosted runner
  lists builds with `apkeep -l -a com.instagram.android -d apk-pure`, runs `new_profile.scaffold()`
  for any major version newer than the newest profile, and opens a draft PR. The PR includes a static
  resource-id report: `aapt2 dump resources` on the new build's base APK (about a second) lists every
  selector resource id missing from it, and the ids added or removed since the parent profile's build.
  - A research pass on 2026-09-14 ran this on the cached 443-446 builds. All 18 Instagram resource ids
    the selectors use were present in every one, matching the 445 selectors working unchanged on 446.
  - 12 of the 17 required keys in `igprofiles/screens.py` are resource ids. A removed id is near-certain
    drift, but text and content-desc keys can't be checked this way (those strings aren't in the APK),
    and layout changes don't show up either.
  - To confirm: whether APKPure serves GitHub's IP addresses, and that PRs opened with `GITHUB_TOKEN`
    need the repository setting that allows Actions to create PRs (their CI then waits for approval).
  - No account, session, dump or APK may end up in a cache or artifact: in a public repo, anyone can
    read both.

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
- **Device runs for new profiles outside a manual session**, in two parts:
  - **redroid on a GitHub-hosted runner (untested).** The ubuntu-24.04 runner kernel (Azure) ships
    `binder_linux` in its extra-modules package, and runners have sudo and 16 GB of RAM, but no public
    example of redroid in Actions was found, and that package is sometimes missing from the mirrors.
    A half-day `workflow_dispatch` test would settle it: modprobe binder, boot the image, install the
    build, launch, dump. If it works, add a logged-out check to the scaffold PR: the build installs,
    launches without crashing under the ARM translation, and shows the login screen. That can't test
    feed selectors, since logging in from datacenter IPs triggers challenges and puts the account at
    risk. The free arm64 runners, with official arm64 redroid images, would skip translation entirely
    but are unvalidated.
  - **Logged-in baselines, pulled by the host** (not a self-hosted runner: GitHub advises against those
    on public repos, since fork PRs can target them). A systemd timer polls with a fine-grained token
    for labelled work, e.g. a `needs-baseline` label. For each item it waits for a gap between polls,
    runs `docker compose stop app` and `new_profile.py baseline vXYZ --yes` (whose memory and app
    checks still apply), always runs `restore` and `docker compose start app` afterwards, and pushes
    only `report.md` to the draft branch. Dumps and the session never leave the host. The remaining
    risk is running unattended on the host that froze once (CLAUDE.md).
  - Fixing selectors, login challenges, reviewing scrubbed fixtures and `validate` stay manual.
