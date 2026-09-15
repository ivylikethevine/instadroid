---
title: Compatibility
---

# Compatibility: redroid image × Instagram build

Which Android image runs which Instagram build on an x86_64 host. Instagram ships arm64 native code
only, so the image needs working ARM translation; see the README's "Which Android?" for the full
story behind each row.

## Tested pairs

| redroid image | Android | Instagram build | Profile | Result | Last tested | Notes |
|---|---|---|---|---|---|---|
| `erstt/redroid:13.0.0_ndk_ChromeOS` | 13 | 446.0.0.49.77 | `v424` | ⚠️ crashes on launch since 2026-09-15 | 2026-09-15 | Worked on 2026-09-14 (photos, carousels, Reels, stories, permalinks), then crashed natively in `RenderThread` on every launch after a 424 test; 445 on the same data works. See NEXT.md's run log. The image in `docker-compose.yml`. |
| `erstt/redroid:13.0.0_ndk_ChromeOS` | 13 | 445.0.0.45.83 | `v424` | ✅ works | 2026-09-14 | Baseline run: stories, a Reel and a carousel with permalinks and captions. |
| `erstt/redroid:13.0.0_ndk_ChromeOS` | 13 | 444.0.0.46.85 | `v424` | ✅ works | 2026-09-14 | Capture-mode baseline: stories, a Reel and a photo with permalinks and full captions; every required selector key on every captured screen. |
| `erstt/redroid:13.0.0_ndk_ChromeOS` | 13 | 443.0.0.48.82 | `v424` | ✅ works | 2026-09-14 | Same. |
| `erstt/redroid:13.0.0_ndk_ChromeOS` | 13 | 442.0.0.46.79 | `v424` | ✅ works | 2026-09-14 | Same. |
| `erstt/redroid:13.0.0_ndk_ChromeOS` | 13 | 441.0.0.43.81 | `v424` | ✅ works | 2026-09-14 | Same. |
| `erstt/redroid:13.0.0_ndk_ChromeOS` | 13 | 440.1.0.46.86 | `v424` | ✅ works | 2026-09-14 | Same. |
| `erstt/redroid:13.0.0_ndk_ChromeOS` | 13 | 430.0.0.36.80 | `v424` | ✅ launches | 2026-09-14 | Install-and-launch test only (no scrape; not validated, so it runs with a warning): stays open, keeps the login, reaches the Home feed. |
| `erstt/redroid:13.0.0_ndk_ChromeOS` | 13 | 425.0.0.47.61 | `v424` | ✅ launches | 2026-09-14 | Same as 430. |
| `erstt/redroid:13.0.0_ndk_ChromeOS` | 13 | 424.0.0.49.64 | `v424` | ✅ works | 2026-09-15 | The oldest version that runs on this image, and the supported floor. Capture-mode baseline: stories, a carousel and a Reel with permalinks, every required selector key matched. |
| `erstt/redroid:13.0.0_ndk_ChromeOS` | 13 | 423.0.0.47.66 | — | ❌ Instagram crashes at native startup | 2026-09-14 | Same crash as 400 and 420; 421 and 422 weren't tested, being below it. |
| `erstt/redroid:13.0.0_ndk_ChromeOS` | 13 | 420.0.0.55.74 | — | ❌ Instagram crashes at native startup | 2026-09-14 | Same crash as 400: `could not hook fn signal: mprotect: errno: 13, Permission denied` in `libstartup.so`. |
| `erstt/redroid:13.0.0_ndk_ChromeOS` | 13 | 400.0.0.49.68 | — | ❌ Instagram crashes at native startup | 2026-09-14 | Installs, then every launch dies in `libstartup.so`: `could not hook fn signal: mprotect: errno: 13, Permission denied`. Below the supported floor anyway. |
| `erstt/redroid:13.0.0_ndk_ChromeOS` | 13 | (build not recorded) | — | ✅ works | 2026-09-10 | First end-to-end install, login and scrape, before builds were recorded per run. |
| `erstt/redroid:15.0.0_ndk_AVD` | 15 | any | — | ❌ image doesn't boot | 2026-09-10 | `hwservicemanager`/`servicemanager` fatal within ~3s, twice: a binder ABI mismatch with the host kernel. Instagram never ran. |
| `aureliolo/redroid:14.0.0_amd64_with_gapps` | 14 | (build not recorded) | — | ❌ Instagram crashes on launch | 2026-09-10 | No ARM translation at all: `dlopen failed: ... EM_AARCH64 ... instead of EM_X86_64`. No Instagram build can work. |
| `abing7k/redroid:a11_ndk_amd` | 11 | 3 builds (not recorded) | — | ❌ Instagram crashes at native startup | before 2026-09-10 | Same crash on all three builds. |

No Android 14 image with ARM translation was found (`erstt/redroid` publishes 11, 12, 13 and 15
only). Instagram builds older than 424 aren't supported by any profile (`igprofiles.MIN_MAJOR`), and don't
launch on this image anyway. The profile column shows today's names: the root profile was `v440`
until 2026-09-15. A fresh install gets 445.0.0.45.83 (`igprofiles.DEFAULT_BUILD`) while 446 crashes.

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
When a new Instagram major version gets a profile (see [Version profiles](NEXT.html)), add its row
once the validation run is in.
