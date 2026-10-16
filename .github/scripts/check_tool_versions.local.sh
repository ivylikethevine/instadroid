# shellcheck shell=bash
# SPDX-License-Identifier: MIT
# The instadroid specifics for ./check_tool_versions.sh, which sources this file (the hook contract is
# in that script's header). Nothing here runs on its own.

# Inline pins the script can't find by itself: name|file-glob|sed-BRE-with-one-\(capture\)|check|tag-prefix
# apkeep: app/Dockerfile's ADD URL (the checksum next to it changes with the version).
# just-the-docs: the Pages theme, `_config.yml`'s `remote_theme:`.
# shellcheck disable=SC2034 # read by check_tool_versions.sh
CI_WORKFLOW_ROSTER='apkeep|app/Dockerfile|/EFForg/apkeep/releases/download/\([0-9][0-9.]*\)/|github:EFForg/apkeep|
just-the-docs|_config.yml|remote_theme: just-the-docs/just-the-docs@v\([0-9][0-9.]*\)|github:just-the-docs/just-the-docs|'

# The digest-pinned base image and the compose services' images.
# shellcheck disable=SC2034 # read by check_tool_versions.sh
CI_IMAGE_GLOBS='app/Dockerfile docker-compose.yml'

# The Markdown tools' npm manifest and the two pip-compile inputs, each read at its hashed lock's versions.
# shellcheck disable=SC2034 # read by check_tool_versions.sh
CI_MANIFESTS='npm|.github/package.json
pip|app/requirements.in
pip|requirements-dev.in'

# _id_first <sed-BRE> <file> - the first capture in <file>, or nothing. The BRE is matched as written
# (unlike _ci_extract, no leading .* is added), so every pattern here starts with ^.
function _id_first() {
  [ -f "$2" ] || return 0
  sed -n "s|$1.*|\1|p" "$2" | head -n1
}

# _python_matches <want> <label> <version> <where> - one Python version line, a problem unless it's <want>
function _python_matches() {
  if [ "$3" = "$1" ]; then
    printf '%-32s %-14s matches (%s)\n' "$2" "$3" "$4"
  else
    printf '%-32s %-14s MISMATCH (%s; requires-python says %s)\n' "$2" "${3:--}" "$4" "$1"
    _ci_problem "python version" "$2 in $4 is '${3:-missing}', requires-python is $1 - move them together"
  fi
}

function ci_local_checks() {
  local want v f

  echo "## Local checks (check_tool_versions.local.sh)"
  echo

  # The Python version is written in several places that nothing else keeps in step. requires-python is
  # the reference; every other one must name the same X.Y.
  want="$(_id_first '^requires-python = ">=\([0-9][0-9.]*\)"' pyproject.toml)"
  if [ -z "$want" ]; then
    _ci_problem "python version" "no requires-python = \">=X.Y\" in pyproject.toml"
  else
    printf '%-32s %-14s reference (pyproject.toml requires-python)\n' "python" "$want"
    v="$(_id_first '^target-version = "py\([0-9][0-9]*\)"' pyproject.toml)"
    _python_matches "$want" "ruff target-version" "${v:+${v:0:1}.${v:1}}" "pyproject.toml [tool.ruff]"
    v="$(_id_first '^pythonVersion = "\([0-9][0-9.]*\)"' pyproject.toml)"
    _python_matches "$want" "basedpyright pythonVersion" "$v" "pyproject.toml [tool.basedpyright]"
    v="$(_id_first '^FROM python:\([0-9][0-9.]*\)-' app/Dockerfile)"
    _python_matches "$want" "app/Dockerfile FROM python" "$v" app/Dockerfile
    for f in .github/workflows/*.yml; do
      while IFS= read -r v; do
        _python_matches "$want" "setup-python python-version" "$v" "$f"
      done < <(sed -n 's|.*python-version: "\([0-9][0-9.]*\)".*|\1|p' "$f" | sort -u)
    done
  fi

  # docker-compose.yml names redroid twice: the tag in the x-redroid-image anchor (init's guard, the app's
  # run records) and tag@digest on the redroid service. A bump to one must move the other.
  want="$(_id_first '^x-redroid-image: &redroid_image \([^ ]*\)' docker-compose.yml)"
  v="$(_id_first '^ *image: \([^ @]*/redroid:[^ @]*\)@sha256:[0-9a-f]\{64\}' docker-compose.yml)"
  if [ -n "$want" ] && [ "$want" = "$v" ]; then
    printf '%-32s %-14s matches the digest-pinned redroid service\n' "redroid image anchor" "${want##*:}"
  else
    printf '%-32s %-14s MISMATCH (anchor %s, service %s)\n' "redroid image anchor" "-" "${want:-missing}" "${v:-missing or not digest-pinned}"
    _ci_problem "redroid image" "docker-compose.yml's x-redroid-image anchor (${want:-missing}) and the redroid service's digest-pinned image (${v:-missing}) must name the same tag"
  fi
}
