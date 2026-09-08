#!/usr/bin/env bash
# Starts the Android Studio emulator headless with a lean configuration for the scraper.
# Prereqs: an AVD with Play Store image (Instagram needs Google's ARM translation on x86_64).
set -euo pipefail
AVD="${AVD:-Pixel_10}"
EMU="${ANDROID_HOME:-$HOME/Android/Sdk}/emulator/emulator"
exec "$EMU" -avd "$AVD" \
  -no-window -no-audio -no-boot-anim -no-snapshot -no-metrics \
  -gpu "${EMU_GPU:-host}" \
  -memory "${EMU_MEMORY:-2048}" -cores "${EMU_CORES:-2}" \
  -camera-back none -camera-front none \
  -netdelay none -netspeed full \
  "$@"
