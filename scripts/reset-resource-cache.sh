#!/usr/bin/env bash
# Recovers from stale idmap resource-cache entries left behind in redroid's /data volume by an
# earlier boot (symptom: system_server logs "Version mismatch in Idmap", idmap2d gets repeatedly
# killed and restarted, boot takes minutes instead of ~35s and sys.boot_completed never gets set).
# /data/resource-cache is a pure build cache for overlay idmaps: idmap2d regenerates it from the
# overlay APKs on next boot, so clearing it loses no app/login state. See CLAUDE.md's "appops.xml
# corruption" section for the same class of bug in a different cache file.
#
# Stops the compose stack (editing this live while idmap2d is writing to it would just recreate
# the race), moves the cache aside instead of deleting it, then restarts.
#
# Usage: ./scripts/reset-resource-cache.sh [--no-restart]
set -euo pipefail
cd "$(dirname "$0")/.."

CACHE_DIR="local/data/android/resource-cache"
BACKUP_DIR="${CACHE_DIR}.corrupt-bak-$(date +%Y%m%d%H%M%S)"

if [ ! -d "$CACHE_DIR" ] || [ -z "$(ls -A "$CACHE_DIR" 2>/dev/null)" ]; then
  echo "$CACHE_DIR is already empty; nothing to do."
  exit 0
fi

echo "stopping the compose stack..."
docker compose down

mkdir -p "$BACKUP_DIR"
mv "$CACHE_DIR"/* "$BACKUP_DIR"/
echo "moved $(ls -A "$BACKUP_DIR" | wc -l) stale cache file(s) to $BACKUP_DIR"

if [ "${1:-}" = "--no-restart" ]; then
  echo "not restarting (--no-restart); run 'docker compose up -d' when ready."
else
  echo "starting the compose stack..."
  docker compose up -d
fi
