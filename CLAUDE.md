# redroid on this host: validated, but treat every container start with care

## History

On 2026-09-10, starting the privileged `redroid` compose service on this host caused a full kernel
panic requiring manual recovery. Best-evidence root cause: this host's kernel ships Android's Rust
binder driver built in (`CONFIG_ANDROID_BINDER_IPC_RUST=y`), and a classic out-of-tree binder driver
(`binder_linux-dkms`) was also installed the same day — two binder IPC implementations stacked on
one kernel, with the in-kernel Rust one separately carrying a disclosed race-condition bug
(CVE-2025-68260) that panics under binder IPC load, exactly what booting a full Android system
generates.

The user removed `binder_linux-dkms` and rebooted. Later that same day, with the user's explicit,
informed go-ahead (after being told the DKMS conflict was gone but the kernel's own CVE patch
status was unconfirmed), the `redroid` service — bumped to `erstt/redroid:13.0.0_ndk_ChromeOS`
(Android 13, Google's NDK translation as shipped in ChromeOS's ARC++) — was started, pulled and
run carefully (image pulled separately first, started detached, logs and host responsiveness
polled in short bursts rather than left unattended), and this time it worked cleanly: the container
booted, Instagram installed and logged in via the existing unmodified `scraper.py`, and a full
scratch-DB scrape captured real posts with permalinks — all with **zero** impact on host stability
throughout. It has since been torn down.

## Current rule

Redroid, specifically `erstt/redroid:13.0.0_ndk_ChromeOS`, is now a **validated-safe** part of this
project on this host — the original crash trigger is understood and gone, and a full real-world
Android boot + IPC load has run clean. It's no longer something to categorically refuse to start.

That said, starting any privileged container is still consequence-heavy if something's wrong, so
extend the same care that worked here to any future start, and *especially* to anything not yet
validated (a different Android version, a different translation source, a changed
`redroid_gpu_mode`, or any image other than the one above):
1. Pull the image separately first (`docker compose --profile redroid pull redroid`) — a pull can't
   crash the kernel, so it isolates "does this image exist" from the actual risky step.
2. Start detached (`up -d`), then watch `docker compose logs redroid` and basic host responsiveness
   (e.g. `date`) in short polling bursts rather than leaving it unattended.
3. At the first bad sign — host slowness, anything resembling the original incident — stop
   immediately: `docker compose --profile redroid down`.
4. Tear the container down when the test is done rather than leaving a privileged container running
   indefinitely.
5. For genuinely new/untested images or configs, check in with the user before starting, the way
   the first post-fix attempt was confirmed explicitly rather than assumed.

Separately, and independent of this file: the harness's own permission classifier has, on this
host, blocked `docker compose up`/`run` for `redroid` outright at least twice, regardless of this
policy. If that happens, don't try to route around it — tell the user and let them either run the
command themselves or grant a Bash permission rule.

See `README.md`'s "Which Android?" section for the fuller compatibility history.
