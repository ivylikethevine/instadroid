# Compatibility: redroid image × Instagram build

Which Android image runs which Instagram build on an x86_64 host, and why the stack uses the image it
does. This page is the one home for that history; the README and the agent notes link here. What went
wrong along the way (symptoms, log lines, recovery) is in [INCIDENTS.md](INCIDENTS.md).

## Contents

- [Why Android 13](#why-android-13)
- [One Android version per `/data` volume](#one-android-version-per-data-volume)
- [Tested pairs](#tested-pairs)
- [Weighed and not shipped](#weighed-and-not-shipped)
- [Keeping this current](#keeping-this-current)

## Why Android 13

Instagram ships arm64 native code only, so an x86_64 host needs a redroid image with working ARM
translation. Of the images tried, one works:

- **`erstt/redroid:13.0.0_ndk_ChromeOS` (Android 13, Google's NDK translation as shipped in
  ChromeOS's ARC++) works.** Instagram installs, logs in, and scrapes successfully, tested end to end
  (login, a full scrape run, permalink capture, cropped media) with zero issues. This is the image in
  `docker-compose.yml`.
- **`abing7k/redroid:a11_ndk_amd` (Android 11, the original image) crashed Instagram at native
  startup** across 3 tested APK versions, separate from and predating the kernel panic in
  [INCIDENTS.md](INCIDENTS.md#kernel-panic-from-two-binder-drivers-2026-09-10).
- **`erstt/redroid:15.0.0_ndk_AVD` (Android 15, a different NDK translation build; the `AVD` in the
  tag is upstream's own naming, unrelated to this project) doesn't boot.** `hwservicemanager` and
  `servicemanager`, Android's core binder-registration daemons, crashed fatally within ~3 seconds of
  boot, identically across two attempts, the second with `mem_limit`/`shm_size` raised well past
  redroid's usual recommendations, ruling out memory as the cause. It's a binder ABI mismatch between
  this image and the maintainer's host kernel binder driver, not an Instagram compatibility issue or a
  resource one; the container exited cleanly and the host was unaffected both times. The service's
  `mem_limit`/`shm_size` have since been re-tuned for Android 13's measured footprint, so today's
  values aren't evidence either way for a future Android 15 retry.
- **No Android 14 NDK build exists upstream** (`erstt/redroid` only publishes 11, 12, 13 and 15).
  `aureliolo/redroid:14.0.0_amd64_with_gapps`, the only Android 14 redroid image found, boots cleanly,
  is host-safe, and Instagram installs, but the image ships **no ARM translation at all**:
  `ro.product.cpu.abilist` claims `arm64-v8a` support, yet no `libndk_translation.so`,
  `libhoudini.so`, or native-bridge property exists on the device. Confirmed empirically: launching
  Instagram crashes the dynamic linker outright:
  `dlopen failed: "libsuperpack-jni.so" is for EM_AARCH64 (183) instead of EM_X86_64 (62)`. A clean,
  host-safe app crash, and not something a config change fixes.

`erstt/redroid` is the only source found with confirmed, working ARM translation, and it doesn't
publish an Android 14 build, so Android 13/ChromeOS remains the image.

**One known rendering quirk**: under this image's default `androidboot.redroid_gpu_mode=guest`, the
Following-feed switcher's bottom sheet doesn't always open, so the scraper falls back to the Home
feed (the README's [Followed-accounts allowlist](../README.md#followed-accounts-allowlist) filters
that fallback). Host GPU mode was tried as a fix and not shipped; see
[Weighed and not shipped](#weighed-and-not-shipped).

## One Android version per `/data` volume

Switching `docker-compose.yml` to a different Android major version against the same
`local/data/android` volume is what corrupted system state repeatedly
([INCIDENTS.md](INCIDENTS.md#appopsxml-corruption-from-switching-redroid-image-versions-2026-09-10)),
so compose now refuses: the one-shot `init` service (`scripts/guard-android-data.sh`) runs before
redroid, records the image in `local/data/android.image`, and fails with instructions when the
configured image's Android version differs. Give another Android version its own volume instead
(e.g. `./local/data/android-15`).

## Tested pairs

| redroid image                               | Android | Instagram build         | Profile | Result                                 | Last tested       | Notes                                                                                                                                                                                                                         |
| ------------------------------------------- | ------- | ----------------------- | ------- | -------------------------------------- | ----------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `erstt/redroid:13.0.0_ndk_ChromeOS`         | 13      | 446.0.0.49.77           | `v424`  | ⚠️ crashes on launch since 2026-09-15  | 2026-09-15        | Worked on 2026-09-14 (photos, carousels, Reels, stories, permalinks), then crashed natively in `RenderThread` on every launch after a 424 test; 445 on the same data works. See RUNLOG.md. The image in `docker-compose.yml`. |
| `erstt/redroid:13.0.0_ndk_ChromeOS`         | 13      | 445.0.0.45.83           | `v424`  | ✅ works                               | 2026-09-14        | Baseline run: stories, a Reel and a carousel with permalinks and captions.                                                                                                                                                    |
| `erstt/redroid:13.0.0_ndk_ChromeOS`         | 13      | 444.0.0.46.85           | `v424`  | ✅ works                               | 2026-09-14        | Capture-mode baseline: stories, a Reel and a photo with permalinks and full captions; every required selector key on every captured screen.                                                                                   |
| `erstt/redroid:13.0.0_ndk_ChromeOS`         | 13      | 443.0.0.48.82           | `v424`  | ✅ works                               | 2026-09-14        | Same.                                                                                                                                                                                                                         |
| `erstt/redroid:13.0.0_ndk_ChromeOS`         | 13      | 442.0.0.46.79           | `v424`  | ✅ works                               | 2026-09-14        | Same.                                                                                                                                                                                                                         |
| `erstt/redroid:13.0.0_ndk_ChromeOS`         | 13      | 441.0.0.43.81           | `v424`  | ✅ works                               | 2026-09-14        | Same.                                                                                                                                                                                                                         |
| `erstt/redroid:13.0.0_ndk_ChromeOS`         | 13      | 440.1.0.46.86           | `v424`  | ✅ works                               | 2026-09-14        | Same.                                                                                                                                                                                                                         |
| `erstt/redroid:13.0.0_ndk_ChromeOS`         | 13      | 430.0.0.36.80           | `v424`  | ✅ launches                            | 2026-09-14        | Install-and-launch test only (no scrape; not validated, so it runs with a warning): stays open, keeps the login, reaches the Home feed.                                                                                       |
| `erstt/redroid:13.0.0_ndk_ChromeOS`         | 13      | 425.0.0.47.61           | `v424`  | ✅ launches                            | 2026-09-14        | Same as 430.                                                                                                                                                                                                                  |
| `erstt/redroid:13.0.0_ndk_ChromeOS`         | 13      | 424.0.0.49.64           | `v424`  | ✅ works                               | 2026-09-15        | The oldest version that runs on this image, and the supported floor. Capture-mode baseline: stories, a carousel and a Reel with permalinks, every required selector key matched.                                              |
| `erstt/redroid:13.0.0_ndk_ChromeOS`         | 13      | 423.0.0.47.66           | —       | ❌ Instagram crashes at native startup | 2026-09-14        | Same crash as 400 and 420; 421 and 422 weren't tested, being below it.                                                                                                                                                        |
| `erstt/redroid:13.0.0_ndk_ChromeOS`         | 13      | 420.0.0.55.74           | —       | ❌ Instagram crashes at native startup | 2026-09-14        | Same crash as 400: `could not hook fn signal: mprotect: errno: 13, Permission denied` in `libstartup.so`.                                                                                                                     |
| `erstt/redroid:13.0.0_ndk_ChromeOS`         | 13      | 400.0.0.49.68           | —       | ❌ Instagram crashes at native startup | 2026-09-14        | Installs, then every launch dies in `libstartup.so`: `could not hook fn signal: mprotect: errno: 13, Permission denied`. Below the supported floor anyway.                                                                    |
| `erstt/redroid:13.0.0_ndk_ChromeOS`         | 13      | (build not recorded)    | —       | ✅ works                               | 2026-09-10        | First end-to-end install, login and scrape, before builds were recorded per run.                                                                                                                                              |
| `erstt/redroid:15.0.0_ndk_AVD`              | 15      | any                     | —       | ❌ image doesn't boot                  | 2026-09-10        | `hwservicemanager`/`servicemanager` fatal within ~3s, twice: a binder ABI mismatch with the host kernel. Instagram never ran.                                                                                                 |
| `aureliolo/redroid:14.0.0_amd64_with_gapps` | 14      | (build not recorded)    | —       | ❌ Instagram crashes on launch         | 2026-09-10        | No ARM translation at all: `dlopen failed: ... EM_AARCH64 ... instead of EM_X86_64`. No Instagram build can work.                                                                                                             |
| `abing7k/redroid:a11_ndk_amd`               | 11      | 3 builds (not recorded) | —       | ❌ Instagram crashes at native startup | before 2026-09-10 | Same crash on all three builds.                                                                                                                                                                                               |

No Android 14 image with ARM translation was found (`erstt/redroid` publishes 11, 12, 13 and 15
only). Instagram builds older than 424 aren't supported by any profile (`igprofiles.MIN_MAJOR`), and don't
launch on this image anyway. The profile column shows today's names: the root profile was `v440`
until 2026-09-15. A fresh install gets 445.0.0.45.83 (`igprofiles.DEFAULT_BUILD`) while 446 crashes.

## Weighed and not shipped

Options that were tried or considered and deliberately left out, with the reason, so they aren't
re-proposed without something new:

- **Host GPU mode** (`androidboot.redroid_gpu_mode=host`, with `/dev/dri` passed through), tried as a
  fix for the switcher quirk above. It did engage the real host GLES renderer, but boot went from ~35s
  to ~340s with a `BOOT TIMEOUT`, and the maintainer stopped the investigation: FreshRSS and other
  clients consuming this feed may not support whatever that mode changes. `docker-compose.yml` stays
  on `guest`; don't retry `=host` unless the maintainer raises it again. Write-up:
  [INCIDENTS.md](INCIDENTS.md#host-gpu-mode-tried-and-rejected-2026-09-10).
- **Android 15** (`erstt/redroid:15.0.0_ndk_AVD`): doesn't boot on the maintainer's host (see
  [Why Android 13](#why-android-13)). Not worth retrying without a new hypothesis.
- **Android 14**: no image with ARM translation exists, so no Instagram build can work on one.
- **Instagram builds older than 424**: they crash at native startup on this image, so no profile
  covers them ([RUNLOG.md](RUNLOG.md)).

## Keeping this current

Every scrape run records the Instagram `versionName`, the redroid image and the active profile
(`runs.ig_version`, `runs.redroid_image`, `runs.selector_profile`). To see every pair a database
has actually run:

```bash
docker compose exec app python scraper.py compat
```

```text
redroid image | Instagram | profile | runs (ok, clean) | new posts | last run
erstt/redroid:13.0.0_ndk_ChromeOS | 445.0.0.45.83 | v424 | 1 (1, 1) | 6 | 2026-09-14
```

`ok` runs finished without an error; `clean` runs also had no warning (a memory-guard stop, selector
drift, a mismatch between the installed build and the profile, ...). A pair with clean runs that
stored posts is worth a ✅ row here; a pair whose runs all fail belongs here too, with the error.
When a new Instagram major version gets a profile (see [Version profiles](PROFILES.md)), add its row
once the validation run is in.
