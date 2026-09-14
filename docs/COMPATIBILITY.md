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
| `erstt/redroid:13.0.0_ndk_ChromeOS` | 13 | 446.0.0.49.77 | `v446` | ✅ works | 2026-09-14 | Photos, carousels, Reels, stories and permalinks captured live. The image in `docker-compose.yml`. |
| `erstt/redroid:13.0.0_ndk_ChromeOS` | 13 | 445.0.0.45.83 | `v445` | ✅ works | 2026-09-14 | Baseline run: stories, a Reel and a carousel with permalinks and captions. |
| `erstt/redroid:13.0.0_ndk_ChromeOS` | 13 | (build not recorded) | — | ✅ works | 2026-09-10 | First end-to-end install, login and scrape, before builds were recorded per run. |
| `erstt/redroid:15.0.0_ndk_AVD` | 15 | any | — | ❌ image doesn't boot | 2026-09-10 | `hwservicemanager`/`servicemanager` fatal within ~3s, twice: a binder ABI mismatch with the host kernel. Instagram never ran. |
| `aureliolo/redroid:14.0.0_amd64_with_gapps` | 14 | (build not recorded) | — | ❌ Instagram crashes on launch | 2026-09-10 | No ARM translation at all: `dlopen failed: ... EM_AARCH64 ... instead of EM_X86_64`. No Instagram build can work. |
| `abing7k/redroid:a11_ndk_amd` | 11 | 3 builds (not recorded) | — | ❌ Instagram crashes at native startup | before 2026-09-10 | Same crash on all three builds. |

No Android 14 image with ARM translation was found (`erstt/redroid` publishes 11, 12, 13 and 15
only). Instagram builds older than 440 aren't supported by any profile (`igprofiles.MIN_MAJOR`).

## Keeping this current

Every scrape run records the Instagram `versionName`, the redroid image and the active profile
(`runs.ig_version`, `runs.redroid_image`, `runs.selector_profile`). To see every pair a database
has actually run:

```bash
docker compose exec app python scraper.py compat
```

```text
redroid image | Instagram | profile | runs (ok, clean) | new posts | last run
erstt/redroid:13.0.0_ndk_ChromeOS | 446.0.0.49.77 | v446 | 1 (1, 1) | 6 | 2026-09-14
```

`ok` runs finished without an error; `clean` runs also had no warning (a memory-guard stop, selector
drift, a mismatch between the installed build and the profile, ...). A pair with clean runs that
stored posts is worth a ✅ row here; a pair whose runs all fail belongs here too, with the error.
When a new Instagram major version gets a profile (see [Version profiles](NEXT.html)), add its row
once the validation run is in.
