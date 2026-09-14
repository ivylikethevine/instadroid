#!/bin/sh
# Refuse to start redroid on a /data volume written by a different Android major version. Mixing
# them corrupted appops.xml, the idmap cache and telephony.db, and deadlocked system_server
# (CLAUDE.md). Run by the one-shot `android-data-guard` compose service before redroid starts.
#
#   guard-android-data.sh <redroid image> <marker file>
#
# The marker records the image that last used the volume. With no marker yet (a fresh volume, or one
# from before this guard existed) the current image is recorded and redroid starts. A different
# Android major version fails with instructions; a different image of the same major (a new build
# of Android 13, say) just updates the marker.
set -eu

image="${1:?usage: $0 <redroid image> <marker file>}"
marker="${2:?usage: $0 <redroid image> <marker file>}"

# The Android major version in a redroid tag: "erstt/redroid:13.0.0_ndk_ChromeOS" -> 13,
# "abing7k/redroid:a11_ndk_amd" -> 11. Empty when the tag doesn't say.
android_major() {
  printf '%s\n' "${1##*:}" | sed -n 's/^[^0-9]*\([0-9][0-9]*\).*/\1/p'
}

current=$(android_major "$image")
if [ ! -s "$marker" ]; then
  printf '%s\n' "$image" >"$marker"
  echo "android-data-guard: recorded $image for this /data volume"
  exit 0
fi

recorded_image=$(head -n 1 "$marker")
recorded=$(android_major "$recorded_image")
if [ -n "$current" ] && [ -n "$recorded" ] && [ "$current" != "$recorded" ]; then
  cat >&2 <<EOF
android-data-guard: refusing to start redroid.
  This /data volume was last used by $recorded_image (Android $recorded),
  but the configured image is $image (Android $current).
  Mixing Android versions on one /data corrupts system state (see CLAUDE.md).
  Either switch the image back, or give the new version its own volume. To reuse this one anyway,
  move local/data/android aside (never delete it: it holds the logged-in session), then delete
  $marker.
EOF
  exit 1
fi
if [ "$recorded_image" != "$image" ]; then
  printf '%s\n' "$image" >"$marker"
  echo "android-data-guard: $recorded_image -> $image (same Android $current), marker updated"
fi
exit 0
