# redroid on this host

## History

On 2026-09-10, starting the privileged `redroid` compose service on this host caused a full kernel
panic. Root cause: this host's kernel ships Android's Rust binder driver built in
(`CONFIG_ANDROID_BINDER_IPC_RUST=y`), and a classic out-of-tree binder driver (`binder_linux-dkms`)
was also installed the same day — two binder IPC implementations stacked on one kernel, with the
in-kernel Rust one separately carrying a disclosed race-condition bug (CVE-2025-68260) that panics
under binder IPC load. The user removed `binder_linux-dkms` and rebooted.

Since then, `docker compose up` (which starts redroid) has been run repeatedly on this host — by
the user and by Claude directly — with zero host impact every time, including container crashes
(see below). Running `docker compose`, including redroid, is normal, permitted work here, not
something to ask permission for each time.

## What's validated

- `erstt/redroid:13.0.0_ndk_ChromeOS` (Android 13, ChromeOS's ARC++ NDK translation) — **works**.
  Instagram installs, logs in, and scrapes successfully end-to-end. This is the image in
  `docker-compose.yml`. Known quirk: the Following-feed switcher's bottom sheet doesn't open under
  `androidboot.redroid_gpu_mode=guest`; `=host` was tried and rejected (see below) — stay on `guest`.
- `erstt/redroid:15.0.0_ndk_AVD` (Android 15) — **does not work** on this host. Tried twice,
  identical failure both times: `hwservicemanager`/`servicemanager` fatal within ~3s of boot, host
  completely unaffected. A generous `mem_limit`/`shm_size` at the time made no difference, ruling
  out memory as the cause — this is a binder ABI mismatch between that image and this host's kernel
  binder driver. Not worth retrying without a new hypothesis. (The service's `mem_limit`/`shm_size`
  have since been re-tuned down for Android 13's actual measured footprint — see "Memory limits"
  below — so don't read today's values as evidence either way for a future Android-15 retry.)
- No Android 14 NDK build exists upstream (`erstt/redroid` only publishes 11/12/13/15).
  `aureliolo/redroid:14.0.0_amd64_with_gapps` (the only Android-14 redroid image found) boots fine
  and is host-safe, but ships **no ARM translation at all** — confirmed by both a device-side check
  (no `libndk_translation.so`/`libhoudini.so`/native-bridge property anywhere) and empirically:
  launching Instagram crashes the linker outright (`dlopen failed: ... EM_AARCH64 ... instead of
  EM_X86_64`). Not fixable by config; this image class just can't run arm64 apps. `erstt/redroid`
  is the only source found with confirmed, working ARM translation.
- `abing7k/redroid:a11_ndk_amd` (Android 11, the original image) crashed Instagram at native
  startup across 3 tested APK versions — separate from and predating the kernel-panic incident.

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

## appops.xml corruption from switching redroid image versions

On 2026-09-10, starting redroid (any gpu_mode) hit a system_server crash loop: `Zygote failed to
write to system_server FD`, `AndroidRuntime: *** FATAL EXCEPTION IN SYSTEM PROCESS`, caused by
`AppOpsService.readUidOps` throwing `IllegalArgumentException: Bad operation #123` while parsing
the persisted `/data/system/appops.xml`. Root cause: `docker-compose.yml`'s redroid service mounts
one shared host path (`./local/data/android`) as `/data` regardless of which image tag is running,
and this host previously pointed that same service at `erstt/redroid:15.0.0_ndk_AVD` to test
Android 15 (see above). Android 15 wrote an app-op id into `appops.xml` that Android 13's
`AppOpsService` (the image now in `docker-compose.yml`) doesn't recognize, so system_server has
crashed on every boot since, in a tight ~5s respawn loop — container itself stays up and
host-safe, so this was easy to miss as "redroid still works, just slow."

Fix applied: `adb root` (this image's adbd runs unauthenticated, `ro.adb.secure=0`), then
`adb shell mv /data/system/appops.xml /data/system/appops.xml.corrupt-bak` and restart the
container; Android regenerates a fresh `appops.xml` on next boot with no data loss to app state
(the login session, installed APK, etc. live elsewhere in `/data`). Confirmed fixed: system_server
now runs cleanly through boot with zero `Bad operation` errors.

**Takeaway: never point two different Android major-version redroid images at the same `/data`
volume.** If a different Android version needs testing again, give it its own volume path (e.g.
`./local/data/android-15`) rather than reusing `./local/data/android`.

## More cross-version `/data` corruption: idmap cache and telephony.db (2026-09-11)

The appops.xml bug above turned out to be one instance of a recurring class, not a one-off. On
2026-09-11, restarting redroid hit two more incidents from the same root cause (Android-15 leftovers
mixed into the Android-13 `/data` volume):

- **Stale idmap cache**: `system_server: Version mismatch in Idmap (was 0x9, expected 0x8)`,
  `idmap2d` repeatedly killed/restarted, boot stretched from the normal ~35s to several minutes.
  `/data/resource-cache` had two mtime generations of the same SystemUI overlay `.frro`/`@idmap`
  files. Fixed with the same move-aside pattern; now scripted as `scripts/reset-resource-cache.sh`
  — run that first if boot is slow and `idmap` shows up in logcat.
- **`com.android.phone` crash loop**: `SQLiteException: Can't downgrade database from version
  4063240 to 3735560` in `com.android.providers.telephony`'s `telephony.db` (at
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
a corrupted-file issue — resetting `/data/system/users/0/package-restrictions.xml` alone did *not*
fix it. Recovery required a full reset of `/data/system`, `/data/system_ce`, and `/data/system_de`
(moved aside, not deleted) — but **`/data/data` was left untouched, and Instagram's login session
survived** (`scraper.py login` returned "no login screen; assume session is live" with zero
manual steps), so this was much lower-cost than it looked going in. Instagram itself did need
reinstalling (`local/xapk/*.apk` was still on disk from the original setup, no network fetch
needed) since wiping the package database orphaned its `/data/app` registration.

**Takeaway: when redroid boot is slow, adb is stuck `offline`, or automation is flaky, read
`adb -s 127.0.0.1:5555 logcat -d` (grep for `WATCHDOG KILLING`, `FATAL EXCEPTION`, `Version
mismatch`, `Can't downgrade database`) before restarting the container again.** Each blind restart
costs 1-9+ minutes; the log almost always names the actual blocked call directly.

## Host GPU mode tried and rejected

Also on 2026-09-10, tried `androidboot.redroid_gpu_mode=host` (with `/dev/dri` passed through to
the container) to see if it would fix the Following-feed switcher's bottom sheet not opening under
guest mode. It technically worked at the graphics level — `ro.hardware.egl` became `mesa` and
`dumpsys SurfaceFlinger` showed the real host Intel Mesa GLES renderer instead of ANGLE/guest — but
boot took much longer (~340s vs ~35s) and hit a `WindowManager: BOOT TIMEOUT: forcing display
enabled` path. The user stopped this line of investigation before it was evaluated further:
**do not use host GPU mode for redroid** — the reasoning given was that FreshRSS/other clients
consuming this feed may not support whatever that mode changes. `docker-compose.yml` is back to
`androidboot.redroid_gpu_mode=guest`, the validated default. Don't retry `=host` without the user
raising it again.

## Memory limits (measured 2026-09-11)

Measured with `docker stats` across several boots and a live scrape: redroid idles around
500-525MiB and peaks around 1.1-1.26GiB with Instagram actually open and scrolling; the app
container idles around 67-97MiB and peaks around 97-125MiB while actively scraping. `mem_limit`/
`shm_size` were re-tuned from the original 4g/2g (redroid) and 768m (app) down to `2g`/`1g` and
`256m` respectively — roughly 60% and 2x headroom over the observed peaks. `shm_size` was cut less
aggressively than the raw numbers might suggest, since under-provisioning it risks screenshot/
graphics-buffer failures that are much harder to diagnose than a plain OOM kill.

**Update:** the 1.1-1.26GiB peak above turned out to be an underestimate — see "The bigger find:
Instagram itself never gets reclaimed between polls" further down for a live-account measurement
that hit 1.98GiB, and why `mem_limit` should not be lowered based on the numbers in this section.

## Reducing idle memory: disabling unused AOSP apps (2026-09-11)

`dumpsys meminfo`'s "Total PSS by OOM adjustment" on a fresh boot (nothing installed yet) showed
~350MiB sitting in Android's "Cached" process tier — apps like the camera, gallery, contacts,
calendar, clock, print spooler, and file picker, none of which the scraper's UI automation ever
opens. On a real device these get reclaimed by `lmkd` under memory pressure; here they don't,
because the container reports the *host's* full RAM to the guest (`dumpsys meminfo`'s "Total RAM"
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
`PackageManagerService` startup, i.e. on a *cold* boot — an already-running instance never hits it,
which is exactly why the live test missed it. The next cold restart hit a tight ~5s crash loop:
`Zygote failed to write to system_server FD`, `*** FATAL EXCEPTION IN SYSTEM PROCESS`,
`java.lang.RuntimeException: There must be exactly one installer; found []` at
`PackageManagerService.getRequiredInstallerLPr`, on every respawn, never reaching
`sys.boot_completed`. Same recovery pattern as the appops.xml/idmap/telephony.db incidents above:
`adb root`, then `adb shell mv /data/system/users/0/package-restrictions.xml
/data/system/users/0/package-restrictions.xml.bak` (this is where `pm disable-user` state lives)
and restart the container — Android regenerates a clean one on next boot, which re-enables
*everything*, so the (corrected, `packageinstaller`-free) disable list has to be re-run afterward.
Confirmed fixed and confirmed to survive a subsequent cold restart cleanly.

**Takeaway that generalizes beyond this one package: test `pm disable-user` changes against a full
cold restart, not just the already-booted instance you disabled them on.** Some AOSP roles
(installer here) are only validated during `PackageManagerService` startup, so a live test can look
completely fine and still crash-loop the very next boot.

With the corrected list (16 apps, `com.android.packageinstaller` excluded) applied from first boot
and verified across a cold restart: `docker stats` idle usage dropped from 1018MiB to 745.7MiB
(~272MiB / ~27%), PIDs dropped from 1009 to 758, `sys.boot_completed` reached normally, and a full
`logcat` check showed zero `FATAL EXCEPTION IN SYSTEM PROCESS` lines.

## The bigger find: Instagram itself never gets reclaimed between polls (2026-09-11)

Ran a real login + `scraper.py once` against this host's actual account to get a live peak
measurement (previously only estimated). `docker stats` hit **1.98GiB of the 2GiB `mem_limit`** —
far above the 1.1-1.26GiB figure in "Memory limits" above, which was evidently measured under a
lighter run. `dumpsys meminfo` explained why: `com.instagram.android` alone was 702MiB resident,
plus a 116MiB `:fbns` (push-notification) subprocess the scraper has no use for (it polls, it's
never woken by a push) — ~820MiB neither `tune-android.sh` nor the app-sweep above ever touched,
because both only look at *other* apps. Same root cause as the "Reducing idle memory" section:
this container's `lmkd` never reclaims anything, so once Instagram is opened by a run it just sits
there fully resident for the entire ~2.5-4.5h gap until the next one — every run before this fix
was paying that ~820MiB tax continuously, not just while actually scraping.

Fix: `scraper.py`'s `scrape_once()` now force-stops `com.instagram.android` itself at the very end
of a run (`_stop_instagram()`, alongside the `_sweep_cached_apps()` app-sweep) — same reasoning as
that sweep: this container can't rely on `lmkd` to do it, so the scraper does it explicitly instead.
Confirmed safe **the hard way**: the very first live test of this (a rushed manual `am force-stop`
+ immediate `scraper.py once`, run back-to-back with other manual `adb`/`am` commands in between)
hit `DeviceNotReady: could not bring com.instagram.android to the foreground` — looked at first
like cold boot being slower than `ensure_logged_in()`'s retry budget assumes. Timed it in isolation
and it wasn't: `app_current()` reported Instagram foregrounded in ~1.1s from a genuine cold start,
well inside the existing retry budget. Re-ran the real flow cleanly (`am force-stop`, then only
`scraper.py once`, no manual commands in between) and it worked — twice more, through the actual
`scrape_once()` code path after rebuilding the image with the fix, each time confirmed by `pidof
com.instagram.android` finding nothing right after a run and the next run's log showing no
foreground warnings at all. Conclusion: the one failure was this session's own rapid-fire manual
`adb`/`am` commands stepping on each other, not a real cold-start timing problem — but this is
exactly the kind of thing the `packageinstaller` incident above says to verify with a real run
rather than assume, so it was.

Effect measured over two consecutive real runs: memory after each run settled around ~1.03GiB
(down from Instagram's own ~1.7-1.9GiB while it's actually open) and stayed there until the next
run relaunches it. This does **not** lower the peak *during* an active scrape — Instagram is still
open and using its ~820MiB then, same as always — it only stops paying that cost through the idle
gap between runs, which is most of the container's time.

**On `mem_limit`: do not lower it.** The 1.98GiB peak measured here (99% of the current `2g`) is
real, live-account data under a genuinely heavy run (many carousels and Reels, several permalink
retries) — a materially worse case than whatever produced the older 1.1-1.26GiB figure. `2g` held
without an OOM kill, but with far less headroom than previously believed. Worth remeasuring peak
again after some real-world runs settle, before ever considering it as a candidate to lower.

See `README.md`'s "Which Android?" section for the fuller compatibility history.
