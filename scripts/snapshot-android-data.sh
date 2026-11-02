#!/usr/bin/env bash
# Snapshot redroid's /data volume (local/data/android): the logged-in Instagram session, installed
# apps and Android's own state, i.e. the expensive thing to lose. Take one before changing the redroid
# image or replacing Instagram, or before a risky recovery step from CLAUDE.md or docs/INCIDENTS.md.
#
#   scripts/snapshot-android-data.sh            # writes local/data/backups/android-<utc>.tar.gz
#   scripts/snapshot-android-data.sh --keep 3   # and keeps only the 3 newest snapshots
#
# redroid must be stopped (`docker compose stop redroid app`): copying a live /data would capture
# half-written databases. The files are owned by Android's uids, so the archive is written from a
# throwaway root container of the app image rather than by the host user. Restore by stopping
# redroid, moving local/data/android aside, and extracting the archive into local/data.
set -euo pipefail

keep=0
if [ "${1:-}" = "--keep" ]; then
  keep="${2:?--keep needs a number}"
fi

root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"

if [ "$(docker inspect -f '{{.State.Running}}' ig-redroid 2>/dev/null || true)" = "true" ]; then
  echo "redroid is running; stop it first: docker compose stop redroid app" >&2
  exit 1
fi
if [ ! -d local/data/android ]; then
  echo "no local/data/android to snapshot" >&2
  exit 1
fi

name="android-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
mkdir -p local/data/backups
echo "writing local/data/backups/$name (this can take a few minutes)"
parts=(android)
[ ! -e local/data/android.image ] || parts+=(android.image)
docker compose run --rm --no-deps --user 0:0 --entrypoint tar \
  -v "$root/local/data:/data" init -czf "/data/backups/$name" -C /data "${parts[@]}"
ls -lh "local/data/backups/$name"

if [ "$keep" -gt 0 ]; then
  # Newest first; everything past the first $keep is removed.
  mapfile -t old < <(ls -1t local/data/backups/android-*.tar.gz | tail -n +"$((keep + 1))")
  if [ "${#old[@]}" -gt 0 ]; then
    printf 'removing old snapshot %s\n' "${old[@]}"
    docker compose run --rm --no-deps --user 0:0 --entrypoint rm -v "$root/local/data:/data" init \
      -f "${old[@]/#local\/data//data}"
  fi
fi
