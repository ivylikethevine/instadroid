#!/usr/bin/env bash
# Installs a few packages from a hashed pip-compile lock without the rest of it, for CI jobs that need
# one standalone tool (typos, shfmt-py, zizmor). Only for packages with no dependencies of their own:
# it installs with --no-deps. Usage: .github/scripts/pip-install-locked.sh <lock> <package>...
set -euo pipefail

lock="${1:?usage: $0 <lock> <package>...}"
shift
[ $# -gt 0 ] || {
  echo "usage: $0 <lock> <package>..." >&2
  exit 2
}

subset="$(mktemp)"
trap 'rm -f "$subset"' EXIT
for package in "$@"; do
  # pip-compile writes normalized names; each entry is "name==version \" plus its --hash lines.
  name="$(echo "$package" | tr '[:upper:]_.' '[:lower:]--')"
  block="$(awk -v name="$name" '
    index($0, name "==") == 1 { found = 1 }
    found { print; if ($0 !~ /\\$/) exit }
  ' "$lock")"
  [ -n "$block" ] || {
    echo "$package isn't in $lock" >&2
    exit 1
  }
  echo "$block" >>"$subset"
done
pip install --require-hashes --no-deps -r "$subset"
