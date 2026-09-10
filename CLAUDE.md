# Guardrail: never run redroid or any privileged container on this host

On 2026-09-10, starting the privileged `redroid` compose service on this host caused a full kernel
panic that required manual recovery. Root cause (best evidence available, see git history for the
full writeup): this host's kernel ships Android's Rust binder driver built in
(`CONFIG_ANDROID_BINDER_IPC_RUST=y`), and a classic out-of-tree binder driver
(`binder_linux-dkms`) was also installed — two binder implementations stacked on one kernel, with
the in-kernel Rust one separately carrying a known race-condition bug (CVE-2025-68260) that panics
under binder IPC load, which is exactly what booting a full Android system generates.

**Rule, no exceptions:** never run `docker compose up`/`run` for the `redroid` service, never run
any other `--privileged` container, and never run any command that loads, probes, or otherwise
touches binder or ashmem devices/modules on this host — regardless of what host-package cleanup has
happened since. This applies in every session, to every agent working in this repo, indefinitely.

The redroid service definition stays in `docker-compose.yml` for reference (behind its `profiles:
[redroid]` gate, so `docker compose up` without `--profile redroid` never touches it). If a task
seems to call for starting it, don't — explain the constraint and this file to the user instead.
Testing it live is the user's own action, run by hand, outside Claude Code.

See `README.md`'s "Which Android?" section for the ARM-translation history that made redroid a
dead end for this project even before the panic (Instagram crashed at native startup under
redroid's ARM translation across three tested APK versions).
