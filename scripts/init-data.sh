#!/bin/sh
# One-shot setup before redroid and the app start: the compose `init` service runs it as root, with
# local/data mounted at /data.
#
#   init-data.sh <redroid image>
#
# 1. guard-android-data.sh: refuse a /data volume last used by a different Android major version.
# 2. Create the app's bind-mount directories owned by the app user (uid 1000). Docker would otherwise
#    create a missing one as root, and the app then fails with "unable to open database file". Only a
#    directory still owned by root is chowned, never recursively. local/data/android is left alone:
#    redroid runs as root and Android manages its own permissions inside it.
set -eu

sh "$(dirname "$0")/guard-android-data.sh" "${1:?usage: $0 <redroid image>}" /data/android.image

for d in db media debug apk; do
  mkdir -p "/data/$d"
  if [ "$(stat -c %u "/data/$d")" = 0 ]; then
    chown 1000:1000 "/data/$d"
  fi
done
