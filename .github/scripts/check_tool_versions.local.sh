# shellcheck shell=bash
# The instadroid specifics for ./check_tool_versions.sh, which sources this file (the hook contract is
# in that script's header). Nothing here runs on its own.

# Inline pins the script can't find by itself: name|file-glob|sed-BRE-with-one-\(capture\)|check|tag-prefix
# apkeep: app/Dockerfile's ADD URL (the checksum next to it changes with the version).
# shellcheck disable=SC2034 # read by check_tool_versions.sh
CI_WORKFLOW_ROSTER='apkeep|app/Dockerfile|/EFForg/apkeep/releases/download/\([0-9][0-9.]*\)/|github:EFForg/apkeep|'

# The digest-pinned base image and the compose services' images.
# shellcheck disable=SC2034 # read by check_tool_versions.sh
CI_IMAGE_GLOBS='app/Dockerfile docker-compose.yml'

# _id_first <sed-BRE> <file> - the first capture in <file>, or nothing. The BRE is matched as written
# (unlike _ci_extract, no leading .* is added), so every pattern here starts with ^.
function _id_first() {
  [ -f "$2" ] || return 0
  sed -n "s|$1.*|\1|p" "$2" | head -n1
}

function ci_local_checks() {
  local want v f label where rows="" theme repo

  echo "## Local checks (check_tool_versions.local.sh)"
  echo

  # The Python version is written in several places that nothing else keeps in step. requires-python is
  # the reference; every other one must name the same X.Y.
  want="$(_id_first '^requires-python = ">=\([0-9][0-9.]*\)"' pyproject.toml)"
  if [ -z "$want" ]; then
    _ci_problem "python version" "no requires-python = \">=X.Y\" in pyproject.toml"
  else
    v="$(_id_first '^target-version = "py\([0-9][0-9]*\)"' pyproject.toml)"
    rows+="ruff target-version|${v:+${v:0:1}.${v:1}}|pyproject.toml [tool.ruff]"$'\n'
    v="$(_id_first '^pythonVersion = "\([0-9][0-9.]*\)"' pyproject.toml)"
    rows+="basedpyright pythonVersion|$v|pyproject.toml [tool.basedpyright]"$'\n'
    v="$(_id_first '^FROM python:\([0-9][0-9.]*\)-' app/Dockerfile)"
    rows+="app/Dockerfile FROM python|$v|app/Dockerfile"$'\n'
    for f in .github/workflows/*.yml; do
      while IFS= read -r v; do
        rows+="setup-python python-version|$v|$f"$'\n'
      done < <(sed -n 's|.*python-version: "\([0-9][0-9.]*\)".*|\1|p' "$f" | sort -u)
    done

    printf '%-32s %-14s reference (pyproject.toml requires-python)\n' "python" "$want"
    while IFS='|' read -r label v where; do
      [ -n "$label" ] || continue
      if [ "$v" = "$want" ]; then
        printf '%-32s %-14s matches (%s)\n' "$label" "$v" "$where"
      else
        printf '%-32s %-14s MISMATCH (%s; requires-python says %s)\n' "$label" "${v:--}" "$where" "$want"
        _ci_problem "python version" "$label in $where is '${v:-missing}', requires-python is $want - move them together"
      fi
    done <<<"$rows"
  fi

  # The docs site's remote theme, in the root _config.yml (pages.yml builds the site from the root).
  f=_config.yml
  theme="$(_id_first '^remote_theme: *\([^ @]*/[^ @]*@v[0-9][0-9.]*\)' "$f")"
  if [ -z "$theme" ]; then
    printf '%-32s %-14s ERROR (no remote_theme: owner/repo@vX.Y.Z in _config.yml)\n' "jekyll theme" "-"
    _ci_problem "jekyll theme" "no remote_theme: owner/repo@vX.Y.Z pin found in _config.yml"
  else
    repo="${theme%@*}"
    _ci_report_pin "jekyll theme ($repo)" "${theme#*@v}" "github:$repo" "" "$f remote_theme"
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
