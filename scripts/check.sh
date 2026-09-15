#!/usr/bin/env bash
# Runs CI's blocking checks locally, the same commands ci.yml's jobs run: each job calls this script
# with its own subcommands, so the two can't drift. Never touches the device or starts a container.
#
#   scripts/check.sh [--all]            every check (the default)
#   scripts/check.sh --fast             python + test: the inner loop for a Python change
#   scripts/check.sh --lint             every static check: python, shell, docs, workflows, docker
#   scripts/check.sh <subcommand>...    only those; `test -- <pytest args>` passes arguments on to pytest
#   scripts/check.sh --install [...]    first fetch tools.txt's pinned binaries into local/ci-tools
#
# Subcommands, grouped as ci.yml's jobs are:
#   python     ruff (check + format), basedpyright, lint-imports                      (lint job)
#   test       pytest with coverage; the floor is pyproject.toml's fail_under          (test job)
#   audit      pip-audit over both hashed locks (needs the network)                    (audit job)
#   shell      shellcheck, shfmt                                                        (shell job)
#   docs       markdownlint, prettier, lychee --offline, typos, docs-drift              (docs job)
#   workflows  actionlint, lint-workflows, zizmor                          (actionlint + zizmor jobs)
#   docker     hadolint (advisory), compose (`docker compose config`)                  (docker job)
# Each name on the right of a group is a subcommand too. The image build, smoke test and Trivy scan
# stay CI-only, as do gitleaks and dependency review.
#
# Tools come from the dev venv (local/.venv, activated or not; CONTRIBUTING's development setup),
# .github/node_modules (`npm ci`, run here when the lock is newer), and PATH, with local/ci-tools
# first once --install has filled it (CHECK_TOOLS_DIR overrides the directory). Locally a missing
# tool is reported as SKIP; under CI (CI=true) it fails the run. PIP_AUDIT_ARGS adds arguments to
# pip-audit, split on whitespace (advisories.yml's Markdown report flags).
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

tools_dir="${CHECK_TOOLS_DIR:-$root/local/ci-tools}"
roster_tools=(actionlint shellcheck hadolint lychee)
all_groups=(python test audit shell docs workflows docker)

usage() {
  sed -n '2,/^set -uo/{/^set -uo/d;s/^# \{0,1\}//;p}' "$0"
  exit "${1:-0}"
}

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  bold=$'\e[1m' red=$'\e[31m' green=$'\e[32m' yellow=$'\e[33m' reset=$'\e[0m'
else
  bold="" red="" green="" yellow="" reset=""
fi

# --- arguments -----------------------------------------------------------------------------------

install=""
selected=()
pytest_args=()
while [ $# -gt 0 ]; do
  case "$1" in
  -h | --help) usage ;;
  --install) install=1 ;;
  --all) selected+=("${all_groups[@]}") ;;
  --fast) selected+=(python test) ;;
  --lint) selected+=(python shell docs workflows docker) ;;
  --)
    shift
    pytest_args=("$@")
    break
    ;;
  -*)
    echo "check.sh: unknown option $1" >&2
    usage 2 >&2
    ;;
  *) selected+=("$1") ;;
  esac
  shift
done

# --- tools ---------------------------------------------------------------------------------------

# tools.txt's pin for a roster tool, through the same reader setup-tool uses
roster_pin() {
  (
    # shellcheck source=../.github/actions/setup-tool/lib.sh
    source .github/actions/setup-tool/lib.sh
    _ci_tool_row "$1" 2>/dev/null | cut -d'|' -f2
  )
}

if [ -n "$install" ]; then
  mkdir -p "$tools_dir"
  for tool in "${roster_tools[@]}"; do
    pin="$(roster_pin "$tool")"
    if [ -x "$tools_dir/$tool" ] && "$tools_dir/$tool" --version 2>&1 | grep -qF "$pin"; then
      echo "$tool $pin already in $tools_dir"
      continue
    fi
    echo "installing $tool $pin into $tools_dir"
    CI_TOOL="$tool" CI_TOOL_BIN_DIR="$tools_dir" .github/actions/setup-tool/install.sh install || exit 1
  done
  [ "${#selected[@]}" -gt 0 ] || exit 0
fi
[ "${#selected[@]}" -gt 0 ] || selected=("${all_groups[@]}")

[ -d "$tools_dir" ] && PATH="$tools_dir:$PATH"
if [ -z "${VIRTUAL_ENV:-}" ] && [ -x local/.venv/bin/python ]; then
  PATH="$root/local/.venv/bin:$PATH"
fi
export PATH

# --- bookkeeping ---------------------------------------------------------------------------------

results=()
failed=0

record() { # <status> <name> [detail]
  local color="$green"
  case "$1" in
  FAIL) color="$red" failed=1 ;;
  SKIP | WARN) color="$yellow" ;;
  esac
  results+=("$(printf '%s%-5s%s %s%s' "$color" "$1" "$reset" "$2" "${3:+ ($3)}")")
}

# missing <name> <detail> [local hint] - a check that can't run: FAIL under CI, SKIP locally (with the hint)
missing() {
  if [ "${CI:-}" = true ]; then
    record FAIL "$1" "$2"
  else
    record SKIP "$1" "$2${3:-}"
  fi
}

# need <name> <tool>... - true when every tool is on PATH; otherwise `missing`
need() {
  local name="$1" tool
  shift
  for tool in "$@"; do
    command -v "$tool" >/dev/null 2>&1 && continue
    missing "$name" "$tool not installed" "${install:-; roster tools: scripts/check.sh --install}"
    return 1
  done
}

# a roster tool on PATH at another version than tools.txt pins gets a note, not a failure
pin_note() {
  local pin
  pin="$(roster_pin "$1")"
  [ -n "$pin" ] || return 0
  "$1" --version 2>&1 | grep -qF "$pin" ||
    echo "${yellow}note:${reset} $1 on PATH isn't tools.txt's $pin; scripts/check.sh --install fetches it"
}

# run <name> <command>... - run one check and record it
run() {
  local name="$1"
  shift
  printf '\n%s==> %s%s\n' "$bold" "$name" "$reset"
  if "$@"; then
    record OK "$name"
  else
    record FAIL "$name"
  fi
}

# advisory <name> <command>... - like run, but findings are a WARN, and under GitHub Actions the output
# goes to $RUNNER_TEMP/check-<name>.txt with `<name>-findings=true` in $GITHUB_OUTPUT for an
# advisory-note step to pick up
advisory() {
  local name="$1" report rc=0
  shift
  printf '\n%s==> %s (advisory)%s\n' "$bold" "$name" "$reset"
  report="${RUNNER_TEMP:-${TMPDIR:-/tmp}}/check-$name.txt"
  "$@" 2>&1 | tee "$report" || rc=$?
  if [ "$rc" -eq 0 ]; then
    record OK "$name"
  else
    record WARN "$name" "advisory, exit $rc"
    [ -z "${GITHUB_OUTPUT:-}" ] || echo "$name-findings=true" >>"$GITHUB_OUTPUT"
  fi
}

# repo_files <pathspec>... - NUL-separated tracked and untracked-but-not-ignored files that exist: what
# a CI checkout of this tree would hold once committed
repo_files() {
  git ls-files -z --cached --others --exclude-standard -- "$@" |
    while IFS= read -r -d '' f; do [ -f "$f" ] && printf '%s\0' "$f"; done
}

# md_files - fill $markdown with the Markdown files, listed once however many docs checks ask
markdown=()
md_files() { [ "${#markdown[@]}" -gt 0 ] || mapfile -d '' markdown < <(repo_files '*.md'); }

node_tools() {
  local bin=.github/node_modules/.bin
  if [ ! -x "$bin/markdownlint-cli2" ] || [ ! -x "$bin/prettier" ] ||
    [ .github/package-lock.json -nt .github/node_modules/.package-lock.json ]; then
    need "npm ci" npm || return 1
    echo "npm ci in .github (markdownlint-cli2 + prettier, from package-lock.json)"
    npm ci --prefix .github --ignore-scripts --no-audit --no-fund >/dev/null || {
      record FAIL "npm ci"
      return 1
    }
  fi
}

# --- checks --------------------------------------------------------------------------------------

check_ruff() {
  need ruff ruff || return
  run "ruff check" ruff check .
  run "ruff format" ruff format --check .
}

check_basedpyright() {
  need basedpyright basedpyright || return
  run basedpyright basedpyright
  # basedpyright always skips dot-directories, so the Python CI helpers are named explicitly
  local -a scripts
  mapfile -d '' scripts < <(repo_files '.github/scripts/*.py')
  [ "${#scripts[@]}" -eq 0 ] || run "basedpyright (.github/scripts)" basedpyright "${scripts[@]}"
}

check_lint_imports() {
  need lint-imports lint-imports || return
  run lint-imports lint-imports
}

check_test() {
  need test python || return
  mkdir -p local # pyproject.toml keeps coverage's data file there
  # pytest-cov enforces, and reports, pyproject.toml's fail_under
  run "pytest + coverage" python -m pytest -q --cov --cov-report=term "${pytest_args[@]}"
}

check_audit() {
  need pip-audit pip-audit || return
  local -a extra=()
  read -ra extra <<<"${PIP_AUDIT_ARGS:-}" # whitespace-split, unglobbed
  # Both locks are fully pinned and hashed, so pip-audit checks exactly those versions without resolving.
  run pip-audit pip-audit --disable-pip --require-hashes -r app/requirements.txt -r requirements-dev.txt --strict \
    "${extra[@]}"
}

check_shellcheck() {
  need shellcheck shellcheck || return
  pin_note shellcheck
  local -a files
  mapfile -d '' files < <(repo_files '*.sh')
  run shellcheck shellcheck -S warning "${files[@]}"
}

check_shfmt() {
  need shfmt shfmt || return
  # style from .editorconfig
  run shfmt shfmt -d scripts/ app/entrypoint.sh .github/scripts/ .github/actions/
}

check_markdownlint() {
  node_tools || return
  md_files
  run markdownlint .github/node_modules/.bin/markdownlint-cli2 "${markdown[@]}"
}

check_prettier() {
  node_tools || return
  md_files
  run prettier .github/node_modules/.bin/prettier --check "${markdown[@]}"
}

check_lychee() {
  need lychee lychee || return
  pin_note lychee
  md_files
  # relative links and #fragments only; link-check.yml checks external links after merge. Settings both
  # share (accepted codes, excluded paths, retries) are lychee.toml's.
  run "lychee (offline)" lychee --config lychee.toml --offline --include-fragments --no-progress "${markdown[@]}"
}

check_typos() {
  need typos typos || return
  run typos typos
}

check_docs_drift() {
  need docs-drift python || return
  run docs-drift python .github/scripts/docs_drift.py
}

check_actionlint() {
  need actionlint actionlint shellcheck || return
  pin_note actionlint
  run actionlint actionlint
}

check_lint_workflows() {
  need lint-workflows yq jq || return
  run lint-workflows .github/scripts/lint_workflows.sh
}

check_zizmor() {
  need zizmor zizmor || return
  local -a offline=()
  # with no token zizmor can't reach the API for its online audits, so say so rather than half-try
  [ -n "${GH_TOKEN:-}" ] || offline=(--offline)
  # workflows, composite actions and dependabot.yml (which has audits of its own) by name, not all of
  # .github/: zizmor would also collect the workflows inside .github/node_modules
  local -a inputs=()
  local input
  for input in .github/workflows .github/actions .github/dependabot.yml; do
    [ -e "$input" ] && inputs+=("$input")
  done
  run zizmor zizmor --min-severity=medium "${offline[@]}" "${inputs[@]}"
}

check_hadolint() {
  need hadolint hadolint || return
  pin_note hadolint
  advisory hadolint hadolint --failure-threshold warning app/Dockerfile
}

check_compose() {
  if ! docker compose version >/dev/null 2>&1; then
    missing "docker compose config" "docker compose not available"
    return
  fi
  # config only reads files; nothing starts. Interpolation uses .env.example, like CI; a missing .env
  # (which env_file: requires) is stood in for by a copy, removed afterwards.
  local made=""
  if [ ! -e .env ]; then
    cp .env.example .env && made=1
  fi
  # shellcheck disable=SC2329 # called through run
  compose_config() {
    docker compose --env-file .env.example config -q &&
      docker compose --env-file .env.example --profile freshrss config -q
  }
  run "docker compose config" compose_config
  [ -z "$made" ] || rm -f .env
}

# --- dispatch ------------------------------------------------------------------------------------

for sub in "${selected[@]}"; do
  case "$sub" in
  # every check records its own result, so a group runs all of its checks whatever the earlier ones did
  python)
    check_ruff
    check_basedpyright
    check_lint_imports
    ;;
  test) check_test ;;
  audit) check_audit ;;
  shell)
    check_shellcheck
    check_shfmt
    ;;
  docs)
    check_markdownlint
    check_prettier
    check_lychee
    check_typos
    check_docs_drift
    ;;
  workflows)
    check_actionlint
    check_lint_workflows
    check_zizmor
    ;;
  docker)
    check_hadolint
    check_compose
    ;;
  # a single check by name: check_<name>, hyphens as underscores
  *)
    if declare -F "check_${sub//-/_}" >/dev/null; then
      "check_${sub//-/_}"
    else
      echo "check.sh: unknown subcommand $sub" >&2
      usage 2 >&2
    fi
    ;;
  esac
done

printf '\n%sSummary%s\n' "$bold" "$reset"
printf '  %s\n' "${results[@]}"
exit "$failed"
