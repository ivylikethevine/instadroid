#!/usr/bin/env bash
# CI smoke test for a built app image (ci.yml and publish.yml): the scraper imports, and the feed
# server starts and answers /health. Usage: scripts/ci/smoke-test.sh <image>
set -euo pipefail

image="${1:?usage: $0 <image>}"
docker run --rm "$image" python -c "import scraper; print('import ok')"
# network=host: the feed binds 127.0.0.1 by default, same as the compose service.
docker run --rm -d --name app --network host "$image"
trap 'docker rm -f app >/dev/null' EXIT
for _ in $(seq 1 20); do
  curl -fs localhost:8000/health && break
  sleep 1
done
curl -fs localhost:8000/health | grep -q '"ok":true'
