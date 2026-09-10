#!/usr/bin/env bash
# Runs the scraper loop and the feed server as two processes in one container. If either dies,
# both are torn down and the container exits with its status, so a restart policy actually
# restarts a clean pair instead of leaving one half running alone.
set -euo pipefail

uvicorn app:app --host "${FEED_HOST:-127.0.0.1}" --port "${FEED_PORT:-8000}" &
feed_pid=$!
python -u scraper.py &
scraper_pid=$!

trap 'kill "$feed_pid" "$scraper_pid" 2>/dev/null || true' TERM INT

wait -n "$feed_pid" "$scraper_pid"
code=$?
kill "$feed_pid" "$scraper_pid" 2>/dev/null || true
exit "$code"
