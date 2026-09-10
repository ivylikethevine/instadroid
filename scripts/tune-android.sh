#!/usr/bin/env bash
# One-time (idempotent) device tuning: turn off animations and disable Google apps the scraper
# never uses, to cut CPU/RAM inside the container. Keeps GMS + WebView (Instagram wants them).
# Usage: ./scripts/tune-android.sh [device-serial]   (defaults to the first `adb devices` entry,
# e.g. redroid's 127.0.0.1:5555)
set -uo pipefail
S="${1:-$(adb devices | awk 'NR==2 {print $1}')}"
A="adb -s $S shell"
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
