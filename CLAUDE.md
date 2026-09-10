# redroid on this host

## History

On 2026-09-10, starting the privileged `redroid` compose service on this host caused a full kernel
panic. Root cause: this host's kernel ships Android's Rust binder driver built in
(`CONFIG_ANDROID_BINDER_IPC_RUST=y`), and a classic out-of-tree binder driver (`binder_linux-dkms`)
was also installed the same day — two binder IPC implementations stacked on one kernel, with the
in-kernel Rust one separately carrying a disclosed race-condition bug (CVE-2025-68260) that panics
under binder IPC load. The user removed `binder_linux-dkms` and rebooted.

Since then, `docker compose --profile redroid up` has been run repeatedly on this host — by the
user and by Claude directly — with zero host impact every time, including container crashes (see
below). Running `docker compose`, including the `redroid` profile, is normal, permitted work here,
not something to ask permission for each time.

## What's validated

- `erstt/redroid:13.0.0_ndk_ChromeOS` (Android 13, ChromeOS's ARC++ NDK translation) — **works**.
  Instagram installs, logs in, and scrapes successfully end-to-end. This is the image in
  `docker-compose.yml`. Known quirk: the Following-feed switcher's bottom sheet doesn't open under
  `androidboot.redroid_gpu_mode=guest`; trying `=host` is the untried next step.
- `erstt/redroid:15.0.0_ndk_AVD` (Android 15) — **does not work** on this host. Tried twice,
  identical failure both times: `hwservicemanager`/`servicemanager` fatal within ~3s of boot, host
  completely unaffected. A generous `mem_limit`/`shm_size` (now permanently set on the service)
  made no difference, ruling out memory as the cause — this is a binder ABI mismatch between that
  image and this host's kernel binder driver. Not worth retrying without a new hypothesis.
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

Pull the image before starting it (`docker compose --profile redroid pull redroid`) so a bad tag
fails cheaply, and prefer starting detached (`up -d`) with a quick look at logs/host responsiveness
after, over walking away mid-boot unattended. Tear a test container down when done rather than
leaving it running. None of this requires checking in first.

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

See `README.md`'s "Which Android?" section for the fuller compatibility history.
