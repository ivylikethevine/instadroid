#!/usr/bin/env bash
# Appends a resource-id section to the new-builds issue body for each build check-new-builds lists:
# the selector ids its base APK no longer declares, and the ids added and removed since the newest
# validated build (resource-id-report, app/devtools/resource_ids.py).
#
#   .github/scripts/resource_id_report.sh <apkeep> <versions-file> <issue-body>
#
# Each build is downloaded from APKPure into a directory of its own under the temp directory, read
# with aapt2 and deleted again before the next one: nothing downloaded is left behind for a cache or an
# artifact. A build that can't be downloaded or read gets a line saying so and the script still exits 0,
# so the issue is opened either way. Needs aapt2, on PATH or in $ANDROID_HOME/build-tools.
set -euo pipefail

apkeep="${1:?usage: $0 <apkeep> <versions-file> <issue-body>}"
versions="${2:?usage: $0 <apkeep> <versions-file> <issue-body>}"
body="${3:?usage: $0 <apkeep> <versions-file> <issue-body>}"
package=com.instagram.android
tmp="${RUNNER_TEMP:-${TMPDIR:-/tmp}}"

aapt2="$(command -v aapt2 || true)"
if [ -z "$aapt2" ] && [ -d "${ANDROID_HOME:-}/build-tools" ]; then
  aapt2="$(find "$ANDROID_HOME/build-tools" -name aapt2 -type f | sort -V | tail -n 1)"
fi
if [ -z "$aapt2" ]; then
  printf '\nNo resource-id report: this runner has no aapt2.\n' >>"$body"
  exit 0
fi

# dump <build> - aapt2's resource listing of that build's base APK, in $tmp/resources-<build>.txt
dump() {
  local build="$1" work base
  work="$(mktemp -d "$tmp/apk.XXXXXX")"
  if timeout 600 "$apkeep" -a "$package@$build" -d apk-pure "$work" >/dev/null 2>&1; then
    # apkeep hands back an .xapk bundle holding the base APK, or the APK itself
    if compgen -G "$work/*.xapk" >/dev/null; then
      unzip -q -o "$work"/*.xapk "$package.apk" -d "$work" || true
    fi
    base="$(find "$work" -maxdepth 1 -name "$package*.apk" | head -n 1)"
    if [ -n "$base" ]; then
      "$aapt2" dump resources "$base" >"$tmp/resources-$build.txt" 2>/dev/null || true
    fi
  fi
  rm -f "$work"/*.xapk "$work"/*.apk
  rmdir "$work" 2>/dev/null || true
  [ -s "$tmp/resources-$build.txt" ]
}

validated=""
new=()
while read -r kind build; do
  case "$kind" in
  validated) validated="$build" ;;
  new) new+=("$build") ;;
  esac
done < <(check-new-builds --versions-file "$versions" --builds)

old=()
if [ -n "$validated" ] && dump "$validated"; then
  old=(--old-build "$validated" --old-dump "$tmp/resources-$validated.txt")
fi

for build in "${new[@]}"; do
  echo >>"$body"
  if dump "$build" &&
    resource-id-report --build "$build" --dump "$tmp/resources-$build.txt" "${old[@]}" >"$tmp/report-$build.md"; then
    cat "$tmp/report-$build.md" >>"$body"
  else
    printf '### Resource ids in `%s`\n\nNo report: the build could not be downloaded from APKPure or read.\n\n' "$build" >>"$body"
  fi
done
