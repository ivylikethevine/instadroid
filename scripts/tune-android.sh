#!/usr/bin/env bash
# One-time (idempotent) device tuning: turn off animations and disable Google apps the scraper
# never uses, to cut CPU/RAM inside the container. Keeps GMS + WebView (Instagram wants them).
# Also sets the device's timezone from DEVICE_TIMEZONE (read from .env if present, or already
# exported) — see scraper.py's DEVICE_TIMEZONE for why this isn't set by default.
# Usage: ./scripts/tune-android.sh [device-serial]   (defaults to the first `adb devices` entry,
# e.g. redroid's 127.0.0.1:5555)
set -uo pipefail
S="${1:-$(adb devices | awk 'NR==2 {print $1}')}"
A="adb -s $S shell"
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi
if [ -n "${DEVICE_TIMEZONE:-}" ]; then
  $A service call alarm 3 s16 "$DEVICE_TIMEZONE" >/dev/null 2>&1 && echo "timezone set to $DEVICE_TIMEZONE"
fi
$A settings put global window_animation_scale 0
$A settings put global transition_animation_scale 0
$A settings put global animator_duration_scale 0
$A settings put global auto_sync 0            # no Google account syncing
$A settings put system screen_off_timeout 2147483647
$A settings put secure location_mode 0
for p in com.android.vending com.android.chrome com.google.android.youtube \
         com.google.android.apps.maps com.google.android.gm com.google.android.apps.photos \
         com.google.android.apps.messaging com.google.android.googlequicksearchbox \
         com.google.android.apps.wellbeing com.google.android.calendar com.google.android.videos \
         com.google.android.apps.docs com.google.android.music com.google.android.apps.youtube.music \
         com.google.android.apps.podcasts com.google.android.apps.nbu.files com.google.android.tts \
         com.google.android.apps.safetyhub com.google.android.apps.tachyon com.google.android.contacts \
         com.google.android.dialer com.google.android.apps.wallpaper com.google.android.apps.pixel.weather \
         com.google.android.projection.gearhead com.google.android.apps.walletnfcrel; do
  $A pm disable-user --user 0 "$p" >/dev/null 2>&1 && echo "disabled $p"
done
# Built-in AOSP apps the scraper's UI automation never touches. Measured on
# erstt/redroid:13.0.0_ndk_ChromeOS (a fresh /data, nothing installed yet): these sit in Android's
# "Cached" process tier — normally reclaimed under memory pressure, but this container reports the
# *host's* full RAM to the guest (see `dumpsys meminfo`'s "Total RAM"), so lmkd's cached-app killer
# never triggers and they just accumulate for the container's actual lifetime. Disabling them here
# stops them from ever launching, cutting ~145MiB / ~14% off idle container memory (docker stats:
# 1018MiB -> 873MiB). Left alone on purpose: com.android.settings (core app, large but too risky to
# disable), com.android.provision / com.android.managedprovisioning (setup-wizard flows this repo's
# troubleshooting sometimes has to re-run after a `/data/system` reset — see CLAUDE.md), and anything
# telephony/Bluetooth/secure-element-related (com.android.phone, com.android.se, rild, bluetooth*) —
# this repo has hit real crash loops in that area before and it's not where the memory is anyway.
# DO NOT add com.android.packageinstaller: PackageManagerService requires exactly one enabled
# "installer" app system-wide and hard-crashes system_server at boot without it ("There must be
# exactly one installer; found []") — see CLAUDE.md, this took down a whole boot.
for p in com.android.documentsui com.android.printspooler com.android.bips \
         com.android.printservice.recommendation com.android.gallery3d com.android.camera2 \
         com.android.cameraextensions com.android.deskclock com.android.calendar \
         com.android.providers.calendar com.android.contacts com.android.quicksearchbox \
         com.android.dynsystem com.android.statementservice com.android.onetimeinitializer \
         com.android.localtransport com.android.traceur; do
  $A pm disable-user --user 0 "$p" >/dev/null 2>&1 && echo "disabled $p"
done
