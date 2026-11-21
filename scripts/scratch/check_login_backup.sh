#!/usr/bin/env bash
# Confirm `scraper.py login-backup` and `login-restore` on a real device: snapshot /data, back the
# login up, restore it onto the same install, then `scraper.py login` to see the session held.
# Device-driving: run it on the deployment host, from the repository root, with no scrape running:
#
#   bash scripts/scratch/check_login_backup.sh
#
# Stops at the first failed step and leaves the manual lock held, so no scheduled run starts over a
# half-restored app; `docker compose exec app python scraper.py unlock` releases it. Each step's output
# goes to local/login-check/<step>.log, and the summary to local/login-check/summary.log.
set -euo pipefail

out=local/login-check
mkdir -p "$out"
summary="$out/summary.log"
: >"$summary"

step() {
  local name="$1"
  shift
  printf '%s %-14s ' "$(date +%H:%M:%S)" "$name" | tee -a "$summary"
  if "$@" >"$out/$name.log" 2>&1; then
    echo ok | tee -a "$summary"
  else
    echo "FAILED (see $out/$name.log); the manual lock stays held" | tee -a "$summary"
    exit 1
  fi
}

wait_boot() {
  local i
  for i in $(seq 1 120); do
    if [ "$(docker compose exec -T app adb -s 127.0.0.1:5555 shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = 1 ]; then
      echo "booted after ~$((i * 5))s"
      return 0
    fi
    docker compose exec -T app adb connect 127.0.0.1:5555 >/dev/null 2>&1 || true
    sleep 5
  done
  echo "Android did not finish booting in 10 minutes"
  return 1
}

# The lock first, as a file, so the poll loop of the app started below never begins a run.
step lock touch local/data/db/manual.lock
step build docker compose build app
step stop docker compose stop redroid app
step snapshot scripts/snapshot-android-data.sh
step start docker compose up -d redroid app
step boot wait_boot
step headroom docker stats --no-stream ig-redroid ig-app
step backup docker compose exec -T app python scraper.py login-backup
step restore docker compose exec -T app python scraper.py login-restore
step login docker compose exec -T app python scraper.py login
step unlock docker compose exec -T app python scraper.py unlock
echo "done: the copied session held" | tee -a "$summary"
