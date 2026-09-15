#!/usr/bin/env bash
# Triage a slow/failed redroid boot. Codifies CLAUDE.md's "when redroid boot is slow, adb is stuck
# offline, or automation is flaky, read logcat before restarting" advice as a single command.
#
# Two sources, because the failures this repo has actually hit split across both:
#   - `adb logcat -d`: userspace crashes (system_server, providers, package manager) once logd is
#     up. This is where the appops.xml / idmap / telephony.db / packageinstaller incidents all
#     showed up (see docs/INCIDENTS.md).
#   - host `dmesg`: redroid's Android `/init` runs as the container's PID 1 and, being privileged,
#     writes straight to the host's kernel ring buffer rather than the container's own stdout — so
#     `docker logs ig-redroid` stays almost empty even during a real startup failure (confirmed:
#     erstt/redroid's ENTRYPOINT is literally `/init`, no wrapper that pipes logcat to stdout).
#     Binder-level failures (the 2026-09-10 kernel panic, the Android-15 hwservicemanager crash
#     within ~3s of boot, before adb is even reachable) only ever show up here.
#
# Usage: ./scripts/diagnose.sh [device-serial]   (defaults to the first `adb devices` entry)
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
S="${1:-$(adb devices | awk 'NR==2 {print $1}')}"

OUT_DIR="local/data/debug"
mkdir -p "$OUT_DIR"
STAMP="$(date +%Y%m%d%H%M%S)"
LOGCAT_FILE="$OUT_DIR/diagnose-logcat-$STAMP.txt"
DMESG_FILE="$OUT_DIR/diagnose-dmesg-$STAMP.txt"

echo "== host dmesg (last 200 lines, redroid/binder-relevant) =="
if command -v dmesg >/dev/null 2>&1 && dmesg_out="$( (dmesg -T 2>/dev/null || dmesg 2>/dev/null) )"; then
  echo "$dmesg_out" | tail -n 200 | tee "$DMESG_FILE" \
    | grep -iE 'binder|redroid|panic|oops|hwservicemanager|servicemanager' || echo "(no matching lines in the tail; full dump saved to $DMESG_FILE)"
else
  echo "can't read the kernel ring buffer (dmesg missing, or this user lacks permission — try" \
    "sudo, or 'sudo sysctl kernel.dmesg_restrict=0'); skipping. This is where an early" \
    "binder/pre-adb crash would show up, so a real startup failure may need it."
fi

echo
echo "== adb device state =="
adb devices

if ! adb -s "$S" shell true >/dev/null 2>&1; then
  echo "adb can't reach $S (still booting, or a binder/servicemanager-level crash before adb comes" \
    "up — see the dmesg output above, this is the case logcat below can't see)."
  exit 1
fi

echo
echo "== adb logcat -d (saved in full to $LOGCAT_FILE) =="
adb -s "$S" logcat -d >"$LOGCAT_FILE" 2>&1

declare -a patterns=(
  'WATCHDOG KILLING'
  'FATAL EXCEPTION'
  'Version mismatch in Idmap'
  "Can't downgrade database"
  'Bad operation #'
  'There must be exactly one installer'
)
declare -a fixes=(
  'system_server watchdog timeout (e.g. PermissionPolicyService hang) -> full /data/system(_ce|_de) reset; see docs/INCIDENTS.md (idmap/telephony.db section). /data/data (login session) is untouched by this.'
  'a crash loop, not necessarily the cause -> read the surrounding lines for which process/exception.'
  'stale resource-cache idmap from a prior boot -> ./scripts/reset-resource-cache.sh'
  "corrupt telephony.db (cross-version /data reuse) -> adb root; am force-stop com.android.phone; mv aside telephony.db/mmssms.db/carrierIdentification.db (+ -journal) under /data/user_de/0/com.android.providers.telephony/databases/, restart. See docs/INCIDENTS.md."
  'corrupt appops.xml (cross-version /data reuse) -> adb root; mv /data/system/appops.xml aside, restart. See docs/INCIDENTS.md.'
  'package-restrictions.xml corrupted (e.g. after disabling com.android.packageinstaller, which must never be disabled) -> adb root; mv /data/system/users/0/package-restrictions.xml aside, restart, then re-run scripts/tune-android.sh.'
)

found=0
for i in "${!patterns[@]}"; do
  matches="$(grep -c -- "${patterns[$i]}" "$LOGCAT_FILE" || true)"
  if [ "${matches:-0}" -gt 0 ]; then
    found=1
    echo
    echo "-- ${matches}x \"${patterns[$i]}\" --"
    grep -- "${patterns[$i]}" "$LOGCAT_FILE" | tail -n 5
    echo "   fix: ${fixes[$i]}"
  fi
done

if [ "$found" -eq 0 ]; then
  echo
  echo "none of the known crash-loop patterns matched; inspect $LOGCAT_FILE directly."
fi
