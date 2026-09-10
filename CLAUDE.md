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

See `README.md`'s "Which Android?" section for the fuller compatibility history.
