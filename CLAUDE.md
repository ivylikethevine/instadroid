# redroid on this host

The rules and takeaways for running redroid and the scraper here. The dated write-ups behind them
(symptoms, log lines, root causes, full recovery steps) are in
[`docs/INCIDENTS.md`](docs/INCIDENTS.md); read the matching section there before a recovery. The
image compatibility history is in `README.md`'s "Which Android?" and `docs/COMPATIBILITY.md`.

## History

On 2026-09-10, starting the privileged `redroid` compose service on this host caused a full kernel
panic. Root cause: this host's kernel ships Android's Rust binder driver built in
(`CONFIG_ANDROID_BINDER_IPC_RUST=y`), and a classic out-of-tree binder driver (`binder_linux-dkms`)
was also installed the same day — two binder IPC implementations stacked on one kernel, with the
in-kernel Rust one separately carrying a disclosed race-condition bug (CVE-2025-68260) that panics
under binder IPC load. The user removed `binder_linux-dkms` and rebooted.

Since then, `docker compose up` (which starts redroid) has been run repeatedly on this host — by
the user and by Claude directly — with zero host impact every time, including container crashes.
Running `docker compose`, including redroid, is normal, permitted work here, not something to ask
permission for each time. (Device-driving scraper runs are the exception: see "Before any
device-driving run" below.)

## What's validated

- `erstt/redroid:13.0.0_ndk_ChromeOS` (Android 13, ChromeOS's ARC++ NDK translation) — **works**.
  Instagram installs, logs in, and scrapes successfully end-to-end. This is the image in
  `docker-compose.yml`. Known quirk: the Following-feed switcher's bottom sheet doesn't open under
  `androidboot.redroid_gpu_mode=guest`; `=host` was tried and rejected (see below) — stay on `guest`.
- `erstt/redroid:15.0.0_ndk_AVD` (Android 15) — **does not work** on this host: `hwservicemanager`/
  `servicemanager` fatal within ~3s of boot, twice, host unaffected; memory ruled out; a binder ABI
  mismatch with this host's kernel binder driver. Not worth retrying without a new hypothesis.
  Today's `mem_limit`/`shm_size` were tuned for Android 13, so don't read them as evidence either way
  for a future Android-15 retry.
- No Android 14 NDK build exists upstream (`erstt/redroid` only publishes 11/12/13/15).
  `aureliolo/redroid:14.0.0_amd64_with_gapps` boots fine and is host-safe, but ships **no ARM
  translation at all** (launching Instagram crashes the linker:
  `EM_AARCH64 ... instead of EM_X86_64`). Not fixable by config; this image class just can't run arm64 apps. `erstt/redroid` is
  the only source found with confirmed, working ARM translation.
- `abing7k/redroid:a11_ndk_amd` (Android 11, the original image) crashed Instagram at native
  startup across 3 tested APK versions.

## Good practice, not a gate

Pull the image before starting it (`docker compose pull redroid`) so a bad tag
fails cheaply, and prefer starting detached (`up -d`) with a quick look at logs/host responsiveness
after, over walking away mid-boot unattended. Tear a test container down when done rather than
leaving it running. None of this requires checking in first. Both compose services use
`restart: unless-stopped` (since 2026-09-10), so they also come back on their own after a host
reboot; `docker compose stop`/`down` is what keeps them down.

One separate, harness-level thing worth knowing: the auto-mode permission classifier has, on this
host, sometimes blocked a `docker compose up`/`run` for `redroid` outright, inconsistently (a later
identical command has also gone through). That's independent of this file and not something editing
it changes — if it happens, don't try to route around it; say so and let the user run the command or
grant a Bash permission rule.

## One Android major version per `/data` volume

**Never point two different Android major-version redroid images at the same `/data` volume.**
Android 15 leftovers in Android 13's `./local/data/android` caused a string of crash loops
(appops.xml, idmap cache, telephony.db; see `docs/INCIDENTS.md`). If a different Android version
needs testing again, give it its own volume path (e.g. `./local/data/android-15`). Since 2026-09-14
compose enforces this: the one-shot `init` service (`scripts/guard-android-data.sh`)
records the image that last used the volume in `local/data/android.image`, and redroid won't start
if the configured image is a different Android major version.

## Recovering a corrupted `/data`

The pattern: `adb root` (this image's adbd runs unauthenticated, `ro.adb.secure=0`), move the bad
file aside (never delete it), restart the container; Android regenerates it on next boot. App state
(login session, installed APK) lives elsewhere in `/data` and survives. Known cases, each written up
in `docs/INCIDENTS.md`:

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

## Read the logs before restarting

**When redroid boot is slow, adb is stuck `offline`, or automation is flaky, read
`adb -s 127.0.0.1:5555 logcat -d` (grep for `WATCHDOG KILLING`, `FATAL EXCEPTION`, `Version
mismatch`, `Can't downgrade database`) before restarting the container again.** Each blind restart
costs 1-9+ minutes; the log almost always names the actual blocked call directly.
The running scraper also saves a filtered copy automatically after any device failure
(`local/data/debug/logcat_<time>.txt`), so check there first.
`scripts/diagnose.sh` runs this triage (plus a host `dmesg` check, for failures early enough that
adb isn't even up yet) and prints the matching fix in one command.

**`docker logs ig-redroid` stays almost empty even during a real startup failure, by design**:
redroid's `ENTRYPOINT` is Android's `/init`, which (privileged) writes to the _host's_ kernel ring
buffer, so a binder-level or pre-`adb` crash only shows up in host `dmesg`/`journalctl -k`. No
`androidboot.*` flag changes this. `scripts/diagnose.sh` checks both sources.

## Host GPU mode: don't

**Do not use host GPU mode for redroid** (`androidboot.redroid_gpu_mode=host`). Tried on 2026-09-10:
it did engage the host Mesa renderer, but boot went from ~35s to ~340s with a `BOOT TIMEOUT`, and the
user stopped the investigation — the reasoning given was that FreshRSS/other clients consuming this
feed may not support whatever that mode changes. `docker-compose.yml` stays on
`androidboot.redroid_gpu_mode=guest`, the validated default. Don't retry `=host` without the user
raising it again.

## Memory

This container's Android never reclaims memory on its own: it sees the host's ~64GiB, so `lmkd`
never trips, and cached apps and a closed-but-running Instagram stay resident. So it's done
explicitly:

- `scripts/tune-android.sh` `pm disable-user`s 16 unused AOSP/Google apps (idle 1018MiB → ~746MiB).
  Deliberately left enabled: `com.android.settings`, `com.android.provision`/
  `com.android.managedprovisioning` (may need to run after a `/data/system` reset), and anything
  telephony/Bluetooth/secure-element-related.
- **`com.android.packageinstaller` must never be disabled**: `PackageManagerService` requires exactly
  one enabled installer and crash-loops on the next cold boot without it.
- **Test `pm disable-user` changes against a full cold restart, not just the already-booted instance
  you disabled them on.** Some AOSP roles (installer here) are only validated during
  `PackageManagerService` startup, so a live test can look completely fine and still crash-loop the
  very next boot.
- `scrape_once()` (`app/instadroid/scrape.py`) force-stops Instagram (~820MiB resident: 702MiB app
  plus a 116MiB `:fbns` subprocess) and the cached apps at the start and end of every run, the end in
  a `finally`; `scraper.py login` force-stops Instagram when it finishes. After a run redroid settles
  around ~1.03GiB.
- Don't interleave manual `adb`/`am` commands with a scraper run: rapid-fire manual commands once
  produced a `DeviceNotReady: could not bring com.instagram.android to the foreground` that looked
  like a cold-start timing bug and wasn't. Verify a suspected device-timing problem with a clean,
  real run.

Measured: redroid idles ~500-750MiB; a live-account scrape hit 1.98GiB of the old 2g limit, and
capture-mode baselines at 3g have peaked at 1.9-2.4GiB (`docs/RUNLOG.md`). The app container idles
~67-97MiB and peaks ~97-125MiB.

**On `mem_limit`: do not lower it.** Current settings in `docker-compose.yml`: redroid `mem_limit` 3g
(`REDROID_MEM_LIMIT`) with `memswap_limit` equal to it (no container swap, the thrashing mode that
stalls a host) and `cpus: 4` (`REDROID_CPUS`, half this host's 8 cores); the app container 256m with
`memswap_limit: 256m`. redroid's `shm_size: 1g` is deliberately generous, since under-provisioning it risks
screenshot/graphics-buffer failures that are much harder to diagnose than a plain OOM kill. Worth
remeasuring peak after more real-world runs before ever considering lowering anything.

The scraper's memory guard (`device.MemoryGuard`, `MEMORY_GUARD_PERCENT`, default 85) reads
redroid's cgroup v2 files through adb, counting usage like `docker stats` does (excluding
`inactive_file`), checks before stories and every screen, and stops a run early with a warning past
the threshold. Each run records `runs.mem_peak_mb` and `runs.oom_kills` (shown on `/status`); any
OOM kill is a run warning.

## Before any device-driving run

On 2026-09-14 Claude started a scrape right after `scraper.py login` (which left Instagram open)
without checking headroom; redroid sat at 1.7-1.99GiB of its then-2g limit, the memcg OOM killer
killed 7 Android processes, and the user's whole desktop froze until they stopped the container
(`docs/INCIDENTS.md` has the timeline).

**Before any device-driving run (login, once, a manual scrape, and `new-profile baseline` and
`new-profile restore`): check `docker stats` headroom, force-stop Instagram, and ask the user
first.** `new-profile baseline` checks headroom and refuses while the `app` service is running,
but that doesn't replace asking; its other subcommands (`check`, `promote`, `validate`, `fork`)
never touch the device. Optional host-side mitigation, the user's call since
it's host-wide: `sysctl vm.oom_dump_tasks=0` stops each OOM kill from dumping every process to the
kernel log.

Also from that incident:

- A Bash tool call that comes back "rejected" may already have started the container. Check
  `docker ps` / `docker events --since ...` before assuming nothing ran.
- The app container showing "unhealthy" during `scraper.py once` is noise: the healthcheck probes the
  feed server, which `scraper.py once` doesn't start.
- The partial 445 run from that incident is **not** evidence that the 445 selectors are broken: its
  dumps showed only an empty feed-switcher `context_menu` popup holding focus (the guest-GPU switcher
  quirk, possibly worsened by the OOM kills), so every screen parsed zero posts. A clean 445 baseline
  was taken later that day.
