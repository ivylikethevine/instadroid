# CLAUDE.md — working on instadroid

Only what an agent session can't derive from the code and the docs. The human contract (the checks,
what CI runs, the device-run rules, which docs change with what) is
[docs/CONTRIBUTING.md](docs/CONTRIBUTING.md); this file links to it rather than copying it, and adds
what an agent needs beyond it.

## Contents

- [Verification loop](#verification-loop)
- [Hard constraints](#hard-constraints)
- [Traps](#traps)
  - [redroid boot and logs](#redroid-boot-and-logs)
  - [Recovering a corrupted `/data`](#recovering-a-corrupted-data)
  - [Memory](#memory)
  - [Device-driving runs](#device-driving-runs)
- [Docs rules](#docs-rules)
- [Repository mechanics](#repository-mechanics)
- [Machine-specific notes](#machine-specific-notes)

## Verification loop

From the repository root, with the dev venv set up
([CONTRIBUTING.md](docs/CONTRIBUTING.md#development-setup)), `scripts/check.sh` runs the same
commands CI's blocking jobs run, grouped as those jobs are (`scripts/check.sh --help` lists them):

```bash
scripts/check.sh --install   # once: tools.txt's pinned actionlint, shellcheck, hadolint, lychee
scripts/check.sh             # everything: python, test, audit, shell, docs, workflows, docker
scripts/check.sh --fast      # python + test, the inner loop for a Python change
scripts/check.sh docs        # one group (or one check, e.g. `typos`)
```

Run it before declaring anything done; a tool it reports as SKIP locally fails under CI. A prose-only
diff can run just `docs`, and a change to a feed server route needs `export-openapi` (the suite fails
until `docs/openapi.json` matches). It never touches the device or starts a container.

## Hard constraints

The rules and the incidents behind them are in
[docs/CONTRIBUTING.md](docs/CONTRIBUTING.md#running-against-a-real-device) and
[docs/INCIDENTS.md](docs/INCIDENTS.md); read the matching INCIDENTS section before a recovery.

- **One Android major version per `/data` volume.** Never point two different Android major-version
  redroid images at the same volume; give another version its own path (e.g.
  `./local/data/android-15`). The `init` service's guard (`scripts/guard-android-data.sh`) refuses
  a mismatch; don't route around it by deleting `local/data/android.image`.
- **Ask the user before any device-driving run**: `scraper.py login`, `once`, `scrape-now`, a manual
  scrape, `new-profile baseline` and `new-profile restore` (`.claude/settings.json` asks for these).
  First check `docker stats` headroom and force-stop Instagram. `new-profile baseline` checks headroom
  and refuses while the `app` service runs, but that doesn't replace asking; its other subcommands
  (`check`, `promote`, `validate`, `fork`) never touch the device. Running `docker compose` itself,
  redroid included (`pull`, `up -d`, `logs`, `stop`, `down`), is normal work, not something to ask
  about each time.
- **Don't lower redroid's `mem_limit` without remeasuring peak memory.** It's 3g
  (`REDROID_MEM_LIMIT`) with `memswap_limit` equal to it (no container swap, the thrashing mode that
  stalls a host), `cpus: 4` (`REDROID_CPUS`), and the app container 256m with `memswap_limit: 256m`.
  redroid's `shm_size: 1g` is deliberately generous: under-provisioning it risks screenshot and
  graphics-buffer failures much harder to diagnose than a plain OOM kill. Peaks are in
  [docs/RUNLOG.md](docs/RUNLOG.md).
- **Never disable `com.android.packageinstaller`.** `PackageManagerService` requires exactly one
  enabled installer and crash-loops on the next cold boot without it.
- **Don't use host GPU mode** (`androidboot.redroid_gpu_mode=host`). `docker-compose.yml` stays on
  `guest`, the validated default; don't retry `=host` unless the user raises it again
  ([docs/COMPATIBILITY.md](docs/COMPATIBILITY.md#weighed-and-not-shipped)).

Good practice, not a gate: pull the image before starting it (`docker compose pull redroid`) so a bad
tag fails cheaply, prefer starting detached (`up -d`) with a quick look at logs and host
responsiveness after, over walking away mid-boot, and tear a test container down when done. Both
compose services use `restart: unless-stopped`, so they also come back on their own after a host
reboot; `docker compose stop`/`down` is what keeps them down.

## Traps

### redroid boot and logs

**A blind container restart costs 1-9+ minutes and usually fixes nothing.** When redroid boot is
slow, adb is stuck `offline`, or automation is flaky, read `adb -s 127.0.0.1:5555 logcat -d` first
(grep for `WATCHDOG KILLING`, `FATAL EXCEPTION`, `Version mismatch`, `Can't downgrade database`); the
log almost always names the blocked call directly. The running scraper also saves a filtered copy
after any device failure (`local/data/debug/logcat_<time>.txt`), so check there first.
`scripts/diagnose.sh` runs this triage, plus a host `dmesg` check for failures before adb is even up,
and prints the matching fix.

**`docker logs ig-redroid` stays almost empty even during a real startup failure, by design.**
redroid's `ENTRYPOINT` is Android's `/init`, which (privileged) writes to the _host's_ kernel ring
buffer, so a binder-level or pre-`adb` crash only shows up in host `dmesg`/`journalctl -k`. No
`androidboot.*` flag changes this. `scripts/diagnose.sh` checks both sources.

### Recovering a corrupted `/data`

**A corrupted `/data` can look like "redroid still works, just slow".** The pattern: `adb root` (this
image's adbd runs unauthenticated, `ro.adb.secure=0`), move the bad file aside (never delete it),
restart the container; Android regenerates it on next boot. App state (login session, installed APK)
lives elsewhere in `/data` and survives. Known cases, each written up in
[docs/INCIDENTS.md](docs/INCIDENTS.md):

- `Bad operation #N` from `AppOpsService.readUidOps`, system_server crash loop →
  `mv /data/system/appops.xml /data/system/appops.xml.corrupt-bak`.
- `Version mismatch in Idmap`, `idmap2d` restarting, boot taking minutes →
  `scripts/reset-resource-cache.sh` (run that first if boot is slow and `idmap` shows up in logcat).
- `Can't downgrade database` in `com.android.phone`, Instagram pushed back to the launcher mid-scrape
  → `am force-stop com.android.phone`, then move `telephony.db`, `mmssms.db`,
  `carrierIdentification.db` and their journals aside, in
  `/data/user_de/0/com.android.providers.telephony/databases/` (not `/data/data`).
- `There must be exactly one installer; found []` → move
  `/data/system/users/0/package-restrictions.xml` aside; that re-enables every disabled app, so
  re-run `scripts/tune-android.sh` afterward.
- system_server deadlocked in `PermissionPolicyService.grantOrUpgradeDefaultRuntimePermissionsIfNeeded`
  and Watchdog-killed every few minutes, never reaching `sys.boot_completed` (a code-level hang;
  resetting `package-restrictions.xml` alone doesn't fix it) → move `/data/system`,
  `/data/system_ce` and `/data/system_de` aside. Leave `/data/data` alone: Instagram's login survives.
  The orphaned Instagram install is reinstalled automatically by `ensure_logged_in()`
  (`app/instadroid/navigation.py`, via `apkeep`, cached under `local/data/apk`; `IG_AUTO_INSTALL`
  opts out).

Before a risky recovery step, stop redroid and snapshot `/data` with
`scripts/snapshot-android-data.sh`.

### Memory

**This container's Android never reclaims memory on its own.** It sees the host's full RAM, not the
cgroup limit, so `lmkd` never trips, and cached apps and a closed-but-running Instagram stay
resident. So it's done explicitly:

- The first-connect tuning (`app/instadroid/tune.py`, the list in `tune_packages.txt`, and
  `scripts/tune-android.sh` by hand) `pm disable-user`s the unused AOSP/Google apps. Deliberately left enabled:
  `com.android.settings`, `com.android.provision`/`com.android.managedprovisioning` (may need to run
  after a `/data/system` reset), and anything telephony/Bluetooth/secure-element-related.
- `scrape_once()` (`app/instadroid/scrape.py`) force-stops Instagram and the cached apps at the start
  and end of every run, the end in a `finally`; `scraper.py login` force-stops Instagram when it
  finishes.

**A `pm disable-user` change can pass a live test and crash-loop the very next boot.** Some AOSP
roles (the installer here) are only validated during `PackageManagerService` startup. Test any
change to the disable list against a full cold restart, not just the already-booted instance you
disabled it on.

**Counting file cache makes the memory guard stop healthy runs.** The guard (`device.MemoryGuard`,
`MEMORY_GUARD_PERCENT`, default 85) reads redroid's cgroup v2 files through adb and counts usage like
`docker stats` does, excluding `inactive_file`; counting that cache once stopped a run far below real
pressure. It checks before stories and every screen, stops a run early with a warning past the
threshold, and records `runs.mem_peak_mb` and `runs.oom_kills`; any OOM kill is a run warning. Keep
that accounting if you touch it.

### Device-driving runs

**Rapid-fire manual `adb`/`am` commands during a run fake a cold-start bug.** They once produced a
`DeviceNotReady: could not bring com.instagram.android to the foreground` that looked like a timing
bug and wasn't. Don't interleave manual commands with a scraper run, and verify a suspected
device-timing problem with a clean, real run.

**A Bash tool call that comes back "rejected" may already have started the container.** Check
`docker ps` / `docker events --since ...` before assuming nothing ran.

**The app container showing "unhealthy" during `scraper.py once` is noise.** The healthcheck probes
the feed server, which `scraper.py once` doesn't start.

**A run that parses zero posts isn't proof the selectors broke.** Look at the dumps first: an empty
feed-switcher `context_menu` popup holding focus (the guest-GPU switcher quirk) makes every screen
parse nothing while the screenshot shows a normal feed.

Optional host-side mitigation, the user's call since it's host-wide: `sysctl vm.oom_dump_tasks=0`
stops each OOM kill from dumping every process to the kernel log.

## Docs rules

- Which doc a change updates is
  [CONTRIBUTING.md's _Which docs change with what_](docs/CONTRIBUTING.md#which-docs-change-with-what);
  every fact has one home, mapped by [docs/README.md](docs/README.md). Link to that home, don't copy
  it, this file included.
- Image compatibility history lives in [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md), dated incident
  write-ups in [docs/INCIDENTS.md](docs/INCIDENTS.md), and dated device runs (with peak memory) in
  [docs/RUNLOG.md](docs/RUNLOG.md), newest at the bottom. A new rule from an incident goes in
  CONTRIBUTING's device-run rules and here, kept short, with the story in INCIDENTS.
- No Jekyll front matter in any Markdown file: the site is built from the repository root
  (`_config.yml`), with `README.md` as its home page.

## Repository mechanics

- **CI tiers.** Every job and workflow is
  [CONTRIBUTING.md's _What CI runs_](docs/CONTRIBUTING.md#what-ci-runs); the test tiers, and which of
  them CI runs, are [docs/TESTING.md](docs/TESTING.md). Real-device runs are never in CI.
- **Releases.** A pushed `vX.Y.Z` tag runs `publish.yml` unattended. Its gate requires the tag to be
  signed by a key in `.github/allowed_signers`, the tagged commit to be on `main`, and a green `ci.yml`
  run on that commit ([docs/RELEASING.md](docs/RELEASING.md)). The version is the tag: never bump
  `pyproject.toml`'s `0.0.0.dev0` placeholder.
- **Pins.** Every action is pinned to a commit SHA with a version comment. Python dependencies are
  hashed pip-compile locks: edit the `.in` files and regenerate app first
  ([CONTRIBUTING.md](docs/CONTRIBUTING.md#development-setup)). markdownlint-cli2 and prettier are
  locked in `.github/package-lock.json`. The CI tools (actionlint, shellcheck, hadolint, trivy,
  lychee) are sha256-pinned rows in `.github/actions/setup-tool/tools.txt`; move a pin there, not in
  a workflow. Images are pinned by digest (the Dockerfile's base, and compose's redroid and FreshRSS).
  `apkeep` is pinned by URL and checksum in `app/Dockerfile`'s `ADD`, which `new-builds.yml` reads,
  so keep that line's shape. Pins Dependabot can't move are drift-checked by `tool-versions.yml`.

## Machine-specific notes

Host-specific notes for the maintainer's machine (its kernel and binder history, its sizing, the
harness quirks seen there, and the dated narratives behind some rules above) live in untracked
`CLAUDE.local.md` beside this file; `.gitignore` keeps it out of the repository.
