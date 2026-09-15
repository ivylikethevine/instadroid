#!/usr/bin/env bash
# Writes the README's two shields.io endpoint badges from a finished `pytest --cov --junitxml` run:
# <out-dir>/coverage.json (the total from coverage's data file) and <out-dir>/tests.json (tests passed,
# from the JUnit report). coverage.yml uploads them as the `coverage-badge` artifact, which pages.yml
# serves as badges/*.json. Either figure missing is written as "not measured" rather than failing, so
# a partial run still publishes what it has.
#
# Usage: .github/scripts/coverage_badges.sh <junit.xml> <out-dir>
# Run from the repository root in the dev venv (it reads pyproject.toml and coverage's data file).
set -euo pipefail

junit="${1:?usage: $0 <junit.xml> <out-dir>}"
out="${2:?usage: $0 <junit.xml> <out-dir>}"
mkdir -p "$out"

# badge <file> <label> <message> <color>
badge() {
  printf '{"schemaVersion":1,"label":"%s","message":"%s","color":"%s","cacheSeconds":300}\n' \
    "$2" "$3" "$4" >"$out/$1"
  echo "$2 badge: $3"
}

# Bright green from the project's floor, pyproject.toml's [tool.coverage.report] fail_under, the same
# number pytest-cov enforces; the lower bands are only shading.
floor="$(python -c "import tomllib; print(int(tomllib.load(open('pyproject.toml', 'rb'))['tool']['coverage']['report']['fail_under']))")"
percent="$(python -m coverage report --format=total 2>/dev/null || true)"
case "$percent" in
'' | *[!0-9]*) badge coverage.json coverage "not measured" lightgrey ;;
*)
  if [ "$percent" -ge "$floor" ]; then
    color=brightgreen
  elif [ "$percent" -ge 75 ]; then
    color=green
  elif [ "$percent" -ge 60 ]; then
    color=yellow
  else
    color=red
  fi
  badge coverage.json coverage "$percent%" "$color"
  ;;
esac

# Passed = tests minus failures, errors and skips, summed over every <testsuite>.
count_passed='
import sys
import xml.etree.ElementTree as ET

root = ET.parse(sys.argv[1]).getroot()
suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
keys = ("failures", "errors", "skipped")
print(sum(int(s.get("tests", "0")) - sum(int(s.get(k, "0")) for k in keys) for s in suites))
'
passed="$(python -c "$count_passed" "$junit" 2>/dev/null || true)"
case "$passed" in
'' | *[!0-9]*) badge tests.json tests "not measured" lightgrey ;;
*) badge tests.json tests "$passed passed" blue ;;
esac
