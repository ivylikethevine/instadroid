# redroid incidents and findings

Dated write-ups of what went wrong running redroid and the scraper on the maintainer's host, and what
was measured or learned along the way: symptoms, log lines, root causes and recovery steps. The rules
that came out of them are kept short in [CONTRIBUTING.md](CONTRIBUTING.md#running-against-a-real-device)
and, for agents, [CLAUDE.md](https://github.com/ivylikethevine/instadroid/blob/main/CLAUDE.md); the
image compatibility history is in [COMPATIBILITY.md](COMPATIBILITY.md).

## Contents

- [Kernel panic from two binder drivers (2026-09-10)](#kernel-panic-from-two-binder-drivers-2026-09-10)
- [Image compatibility notes](#image-compatibility-notes)
- [appops.xml corruption from switching redroid image versions (2026-09-10)](#appopsxml-corruption-from-switching-redroid-image-versions-2026-09-10)
- [More cross-version `/data` corruption: idmap cache and telephony.db (2026-09-11)](#more-cross-version-data-corruption-idmap-cache-and-telephonydb-2026-09-11)
- [Why `docker logs ig-redroid` stays empty](#why-docker-logs-ig-redroid-stays-empty)
- [Host GPU mode tried and rejected (2026-09-10)](#host-gpu-mode-tried-and-rejected-2026-09-10)
- [Memory limits (measured 2026-09-11)](#memory-limits-measured-2026-09-11)
- [Reducing idle memory: disabling unused AOSP apps (2026-09-11)](#reducing-idle-memory-disabling-unused-aosp-apps-2026-09-11)
- [Instagram itself never gets reclaimed between polls (2026-09-11)](#instagram-itself-never-gets-reclaimed-between-polls-2026-09-11)
- [Host freeze during a scrape at the 2g limit (2026-09-14)](#host-freeze-during-a-scrape-at-the-2g-limit-2026-09-14)

## Kernel panic from two binder drivers (2026-09-10)

On 2026-09-10, starting the privileged `redroid` compose service on this host caused a full kernel
panic. Root cause: this host's kernel ships Android's Rust binder driver built in
(`CONFIG_ANDROID_BINDER_IPC_RUST=y`), and a classic out-of-tree binder driver (`binder_linux-dkms`)
was also installed the same day — two binder IPC implementations stacked on one kernel, with the
in-kernel Rust one separately carrying a disclosed race-condition bug (CVE-2025-68260) that panics
under binder IPC load. The user removed `binder_linux-dkms` and rebooted.

Since then, `docker compose up` (which starts redroid) has been run repeatedly on this host with zero
host impact every time, including container crashes (see below).

## Image compatibility notes

The Android 11, 14 and 15 image findings (why each fails, and how that was confirmed) moved to
[COMPATIBILITY.md](COMPATIBILITY.md#why-android-13), their one home. None of them affected the host.

## appops.xml corruption from switching redroid image versions (2026-09-10)

On 2026-09-10, starting redroid (any gpu_mode) hit a system_server crash loop: `Zygote failed to
write to system_server FD`, `AndroidRuntime: *** FATAL EXCEPTION IN SYSTEM PROCESS`, caused by
`AppOpsService.readUidOps` throwing `IllegalArgumentException: Bad operation #123` while parsing
the persisted `/data/system/appops.xml`. Root cause: `docker-compose.yml`'s redroid service mounts
one shared host path (`./local/data/android`) as `/data` regardless of which image tag is running,
and this host previously pointed that same service at `erstt/redroid:15.0.0_ndk_AVD` to test
Android 15 (see above). Android 15 wrote an app-op id into `appops.xml` that Android 13's
`AppOpsService` (the image now in `docker-compose.yml`) doesn't recognize, so system_server had
crashed on every boot since, in a tight ~5s respawn loop — container itself stays up and
host-safe, so this was easy to miss as "redroid still works, just slow."

Fix applied: `adb root` (this image's adbd runs unauthenticated, `ro.adb.secure=0`), then
`adb shell mv /data/system/appops.xml /data/system/appops.xml.corrupt-bak` and restart the
container; Android regenerates a fresh `appops.xml` on next boot with no data loss to app state
(the login session, installed APK, etc. live elsewhere in `/data`). Confirmed fixed: system_server
then ran cleanly through boot with zero `Bad operation` errors.

This is why two different Android major-version redroid images must never share one `/data` volume,
and why, since 2026-09-14, the one-shot `init` compose service
(`scripts/guard-android-data.sh`) records the image that last used the volume in
`local/data/android.image` and refuses to start redroid on a different Android major version.

## More cross-version `/data` corruption: idmap cache and telephony.db (2026-09-11)

The appops.xml bug above turned out to be one instance of a recurring class, not a one-off. On
2026-09-11, restarting redroid hit two more incidents from the same root cause (Android-15 leftovers
mixed into the Android-13 `/data` volume):

- **Stale idmap cache**: `system_server: Version mismatch in Idmap (was 0x9, expected 0x8)`,
  `idmap2d` repeatedly killed/restarted, boot stretched from the normal ~35s to several minutes.
  `/data/resource-cache` had two mtime generations of the same SystemUI overlay `.frro`/`@idmap`
  files. Fixed with the same move-aside pattern; now scripted as `scripts/reset-resource-cache.sh`.
- **`com.android.phone` crash loop**:
  `SQLiteException: Can't downgrade database from version 4063240 to 3735560` in
  `com.android.providers.telephony`'s `telephony.db` (at
  `/data/user_de/0/com.android.providers.telephony/databases/`, not `/data/data` — telephony uses
  device-encrypted storage). Logcat showed this firing thousands of times in the ring buffer — a
  tight crash loop, not an occasional error — and was destabilizing Instagram's own UI automation
  (the app would get pushed back to the launcher mid-scrape). Fixed via `adb root` +
  `am force-stop com.android.phone` + moving `telephony.db`, `.db-journal`, `mmssms.db`,
  `carrierIdentification.db` (and their journals) aside, same pattern as appops.xml.

A third, unrelated issue also surfaced in the same session: `system_server` deadlocked on **every**
boot inside `PermissionPolicyService.grantOrUpgradeDefaultRuntimePermissionsIfNeeded` (a
`CompletableFuture.get()` that never completed), got Watchdog-killed every few minutes, and
retried forever without ever reaching `sys.boot_completed`. This was a genuine code-level hang, not
a corrupted-file issue — resetting `/data/system/users/0/package-restrictions.xml` alone did _not_
fix it. Recovery required a full reset of `/data/system`, `/data/system_ce`, and `/data/system_de`
(moved aside, not deleted) — but **`/data/data` was left untouched, and Instagram's login session
survived** (`scraper.py login` returned "no login screen; assume session is live" with zero
manual steps), so this was much lower-cost than it looked going in. Instagram itself did need
reinstalling (`local/xapk/*.apk` was still on disk from the original setup, no network fetch
needed) since wiping the package database orphaned its `/data/app` registration.

**Update (2026-09-14):** this reinstall step is now automatic — `ensure_logged_in()` in
`app/instadroid/navigation.py` detects a missing `com.instagram.android` and fetches/installs it itself (via
`apkeep`, cached under `local/data/apk`), so a package-database reset like this one no longer
needs a manual `adb install` afterward. See the README's "First-time setup" for the flow and
`IG_AUTO_INSTALL` to opt back out.

Each blind container restart during these incidents cost 1-9+ minutes, while
`adb -s 127.0.0.1:5555 logcat -d` almost always named the actual blocked call directly. That's where
the "read logcat before restarting" rule, the scraper's automatic filtered logcat copy
(`local/data/debug/logcat_<time>.txt`) and `scripts/diagnose.sh` come from.

## Why `docker logs ig-redroid` stays empty

`docker logs ig-redroid` stays almost empty even during a real startup failure, by design. redroid's
image `ENTRYPOINT` is Android's `/init` directly (confirmed via `docker image inspect`) — there's no
wrapper piping `logcat` to the container's stdout. Being privileged, `/init` and the kernel binder
driver write straight to the _host's_ kernel ring buffer instead, so a binder-level or pre-`adb`
crash (like the Android-15 hwservicemanager crash within ~3s of boot, or the 2026-09-10 kernel panic)
only ever shows up in host `dmesg`/`journalctl -k`, never in `docker logs`. There is no supported
`androidboot.*` flag to change this — redroid's documented options cover display/network/GPU tuning
only, nothing log-level-related. `scripts/diagnose.sh` checks both sources.

## Host GPU mode tried and rejected (2026-09-10)

Also on 2026-09-10, tried `androidboot.redroid_gpu_mode=host` (with `/dev/dri` passed through to
the container) to see if it would fix the Following-feed switcher's bottom sheet not opening under
guest mode. It technically worked at the graphics level — `ro.hardware.egl` became `mesa` and
`dumpsys SurfaceFlinger` showed the real host Intel Mesa GLES renderer instead of ANGLE/guest — but
boot took much longer (~340s vs ~35s) and hit a `WindowManager: BOOT TIMEOUT: forcing display
enabled` path. The user stopped this line of investigation before it was evaluated further; the
reasoning given was that FreshRSS/other clients consuming this feed may not support whatever that
mode changes. `docker-compose.yml` is back to `androidboot.redroid_gpu_mode=guest`, the validated
default.

## Memory limits (measured 2026-09-11)

Measured with `docker stats` across several boots and a live scrape: redroid idled around
500-525MiB and peaked around 1.1-1.26GiB with Instagram actually open and scrolling; the app
container idled around 67-97MiB and peaked around 97-125MiB while actively scraping. `mem_limit`/
`shm_size` were re-tuned from the original 4g/2g (redroid) and 768m (app) down to `2g`/`1g` and
`256m` respectively — roughly 60% and 2x headroom over the observed peaks. `shm_size` was cut less
aggressively than the raw numbers might suggest, since under-provisioning it risks screenshot/
graphics-buffer failures that are much harder to diagnose than a plain OOM kill.

**Update:** the 1.1-1.26GiB peak above turned out to be an underestimate — see "Instagram itself
never gets reclaimed between polls" below for a live-account measurement that hit 1.98GiB, and the
host freeze further down for why `mem_limit` is now 3g.

## Reducing idle memory: disabling unused AOSP apps (2026-09-11)

`dumpsys meminfo`'s "Total PSS by OOM adjustment" on a fresh boot (nothing installed yet) showed
~350MiB sitting in Android's "Cached" process tier — apps like the camera, gallery, contacts,
calendar, clock, print spooler, and file picker, none of which the scraper's UI automation ever
opens. On a real device these get reclaimed by `lmkd` under memory pressure; here they don't,
because the container reports the _host's_ full RAM to the guest (`dumpsys meminfo`'s "Total RAM"
line shows the host's real ~64GiB, not the `mem_limit` cgroup ceiling), so lmkd's minfree
thresholds — calibrated for a 64GiB device — never trip. Cached apps just accumulate for the
container's lifetime instead of being evicted.

`scripts/tune-android.sh` now `pm disable-user`s these apps (extending the existing Google-app
disable list), which stops them from ever launching rather than relying on a reclaim that doesn't
happen here.

Deliberately left alone: `com.android.settings` (the single largest cached entry at ~78MiB, but a
core app — too risky to disable), `com.android.provision`/`com.android.managedprovisioning`
(setup-wizard flows that may need to run again after a `/data/system` reset, per the corruption
incidents above), and anything telephony/Bluetooth/secure-element-related
(`com.android.phone`, `com.android.se`, `rild`, `bluetooth*`) — this project has already hit real
crash loops in that area (see above) and none of it showed up as a memory cost anyway. Also noted
in passing: a repeating `bluetooth@1.1-service.sim` crash loop during the first ~15s of every fresh
boot, self-resolving and stable afterward — pre-existing (present before any of these changes,
logcat-confirmed), not a memory driver, and out of scope here.

**`com.android.packageinstaller` crashed every cold boot.** The first pass at this list included
it — it looked like just another idle UI app the scraper's `adb install`-based flow never opens.
It was disabled live against an already-booted instance, measured (looked fine), and written up as
safe. It isn't: `PackageManagerService`'s constructor requires exactly one enabled app matching the
system installer role, and hard-crashes if it finds zero. That check only runs during
`PackageManagerService` startup, i.e. on a _cold_ boot — an already-running instance never hits it,
which is exactly why the live test missed it. The next cold restart hit a tight ~5s crash loop:
`Zygote failed to write to system_server FD`, `*** FATAL EXCEPTION IN SYSTEM PROCESS`,
`java.lang.RuntimeException: There must be exactly one installer; found []` at
`PackageManagerService.getRequiredInstallerLPr`, on every respawn, never reaching
`sys.boot_completed`. Same recovery pattern as the appops.xml/idmap/telephony.db incidents above:
`adb root`, then `adb shell mv /data/system/users/0/package-restrictions.xml
/data/system/users/0/package-restrictions.xml.bak` (this is where `pm disable-user` state lives)
and restart the container — Android regenerates a clean one on next boot, which re-enables
_everything_, so the (corrected, `packageinstaller`-free) disable list has to be re-run afterward.
Confirmed fixed and confirmed to survive a subsequent cold restart cleanly.

With the corrected list (16 apps, `com.android.packageinstaller` excluded) applied from first boot
and verified across a cold restart: `docker stats` idle usage dropped from 1018MiB to 745.7MiB
(~272MiB / ~27%), PIDs dropped from 1009 to 758, `sys.boot_completed` reached normally, and a full
`logcat` check showed zero `FATAL EXCEPTION IN SYSTEM PROCESS` lines.

## Instagram itself never gets reclaimed between polls (2026-09-11)

Ran a real login + `scraper.py once` against this host's actual account to get a live peak
measurement (previously only estimated). `docker stats` hit **1.98GiB of the 2GiB `mem_limit`** —
far above the 1.1-1.26GiB figure in "Memory limits" above, which was evidently measured under a
lighter run. `dumpsys meminfo` explained why: `com.instagram.android` alone was 702MiB resident,
plus a 116MiB `:fbns` (push-notification) subprocess the scraper has no use for (it polls, it's
never woken by a push) — ~820MiB neither `tune-android.sh` nor the app-sweep above ever touched,
because both only look at _other_ apps. Same root cause as "Reducing idle memory": this container's
`lmkd` never reclaims anything, so once Instagram is opened by a run it just sits there fully
resident for the entire ~2.5-4.5h gap until the next one — every run before this fix was paying that
~820MiB tax continuously, not just while actually scraping.

Fix: `scrape_once()` (now in `app/instadroid/scrape.py`) force-stops `com.instagram.android` itself
at the very end of a run (`device.free_device_memory()`, which force-stops it along with the
cached-app sweep) — same reasoning as that sweep: this container can't rely on `lmkd` to do it, so
the scraper does it explicitly instead. Confirmed safe **the hard way**: the very first live test of
this (a rushed manual `am force-stop` and immediate `scraper.py once`, run back-to-back with other
manual `adb`/`am` commands in between) hit `DeviceNotReady: could not bring com.instagram.android to
the foreground` — looked at first like cold boot being slower than `ensure_logged_in()`'s retry
budget assumes. Timed it in isolation and it wasn't: `app_current()` reported Instagram foregrounded
in ~1.1s from a genuine cold start, well inside the existing retry budget. Re-ran the real flow
cleanly (`am force-stop`, then only `scraper.py once`, no manual commands in between) and it worked —
twice more, through the actual `scrape_once()` code path after rebuilding the image with the fix,
each time confirmed by `pidof com.instagram.android` finding nothing right after a run and the next
run's log showing no foreground warnings at all. Conclusion: the one failure was that session's own
rapid-fire manual `adb`/`am` commands stepping on each other, not a real cold-start timing problem —
but this is exactly the kind of thing the `packageinstaller` incident above says to verify with a
real run rather than assume, so it was.

Effect measured over two consecutive real runs: memory after each run settled around ~1.03GiB
(down from Instagram's own ~1.7-1.9GiB while it's actually open) and stayed there until the next
run relaunches it. This does **not** lower the peak _during_ an active scrape — Instagram is still
open and using its ~820MiB then, same as always — it only stops paying that cost through the idle
gap between runs, which is most of the container's time.

The 1.98GiB peak measured here (99% of the then-current `2g`) is real, live-account data under a
genuinely heavy run (many carousels and Reels, several permalink retries) — a materially worse case
than whatever produced the older 1.1-1.26GiB figure. `2g` held without an OOM kill that time, but
with far less headroom than previously believed; see the next section for what happened next.

## Host freeze during a scrape at the 2g limit (2026-09-14)

**This incident was caused by Claude, not by redroid alone.** Right after `scraper.py login` (which
left Instagram open), Claude started `docker compose run --rm --no-deps app python scraper.py once`
without checking memory headroom. redroid sat at 1.7-1.99GiB of its 2GiB `mem_limit` for the whole
run (sampled every ~4s), and between 10:16:39 and 10:17:16 PDT the kernel memcg OOM killer killed 7
Android processes inside the container (`journalctl -k`: `Memory cgroup out of memory: Killed process
... (d.process.media)`, `ackageinstaller`, `ndroid.keychain`, `d.process.acore`, ...). Each kill dumped
the container's ~875-process table to the kernel log (~1,400 lines in a minute). The user's whole
desktop froze until they stopped the scrape container. No hung-task/lockup lines were logged and an
unrelated `rustc` build was running at the same time, so the freeze isn't _proven_ to be the OOM
storm, but the timing matches and nothing else in the logs does.

Also learned: a Bash tool call that came back "rejected" had in fact already started that container
and ran for ~8 minutes. `docker events --since ...` is what reconstructed the timeline. And the
container's "unhealthy" status was noise: the healthcheck probes the feed server, which
`scraper.py once` doesn't start.

What changed as a result:

- **`docker-compose.yml`**: redroid `mem_limit` 2g → **3g** (`REDROID_MEM_LIMIT`), `memswap_limit`
  equal to it (no container swap, which is the thrashing mode that stalls a host), and a CPU cap
  `cpus: 4` (`REDROID_CPUS`, half this host's 8 cores). The app container also got
  `memswap_limit: 256m`.
- **Memory guard** (`device.MemoryGuard` in `app/instadroid/`, `MEMORY_GUARD_PERCENT`, default 85): the scraper
  reads redroid's own cgroup v2 files through adb (`/sys/fs/cgroup/memory.current`, `memory.max`,
  `memory.events`, `memory.stat`; readable as the adb shell user), and counts usage the way
  `docker stats` does, excluding `inactive_file` cache. The first live 446 run showed why: counting
  that cache, the guard stopped a run at "2756 of 3072 MiB" while `docker stats` peaked at ~2.3GiB, and
  the kernel reclaims that cache before it would ever OOM-kill. It checks before stories and before every
  screen, and stops the run early with a warning once usage crosses the threshold. Each run records
  `runs.mem_peak_mb` and `runs.oom_kills` (the `oom_kill` delta over the run), shown in `/status`'s
  "Peak mem" column; any OOM kill also becomes a run warning.
- **`scrape_once()` force-stops Instagram and the cached apps at the start of a run as well as the
  end**, the end now in a `finally`, so a run never begins on top of a still-open Instagram and a
  run that raises doesn't leave ~800MiB resident. `scraper.py login` force-stops Instagram when it
  finishes, too.
- **A rule in CLAUDE.md**: before any device-driving run, check `docker stats` headroom, force-stop
  Instagram, and ask the user first.

The partial 445 run from this incident is **not** evidence that the 445 selectors are broken. It
captured 2 stories (10:16:42/47, exactly when the OOM kills began), then its `feed_switch` and `last`
debug dumps (10:18:40-41) contain nothing but a 22px-tall, empty Instagram `context_menu` popup:
the feed switcher's menu opened a window that never filled in (the known guest-GPU-mode switcher
quirk above, possibly worsened by the OOM kills). While that popup had focus, `dump_hierarchy()` saw
only the popup, even though the screenshot shows a normal Home feed, so every screen parsed zero
posts. A clean 445 baseline was taken later that day at the 3g limit (see the [run log](RUNLOG.md)).
