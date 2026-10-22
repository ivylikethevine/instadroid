#!/usr/bin/env bash
# The deploy bundle a release attaches: everything a host needs to run that release without a clone or
# an image build. publish.yml's release job builds it from the tagged tree; `scripts/check.sh compose`
# builds one with a placeholder digest and runs `docker compose config` on it, so a change to
# docker-compose.yml that breaks the rewrite below fails CI rather than a release.
#
#   release_bundle.sh <image>@sha256:<digest> <version> <out-dir>
#
# Writes <out-dir>/instadroid-<version>.tar.gz holding instadroid-<version>/ with:
#   docker-compose.yml   the app image pulled by digest instead of built from ./app
#   .env.example         the settings, to copy to .env
#   scripts/             init's /data guard, and the host scripts (preflight, diagnose, snapshot, ...)
#   app/instadroid/      the two data files diagnose.sh and tune-android.sh read
# and prints the archive's path. Run from the repository root.
set -euo pipefail

ref="${1:?usage: $0 <image>@sha256:<digest> <version> <out-dir>}"
version="${2:?usage: $0 <image>@sha256:<digest> <version> <out-dir>}"
out="${3:?usage: $0 <image>@sha256:<digest> <version> <out-dir>}"

if ! grep -Eq '^[a-z0-9./_-]+@sha256:[0-9a-f]{64}$' <<<"$ref"; then
  echo "release_bundle: '$ref' is not <image>@sha256:<64 hex digits>" >&2
  exit 1
fi

name="instadroid-$version"
stage="$(mktemp -d "${TMPDIR:-/tmp}/bundle.XXXXXX")"
case "$stage" in
*/bundle.??????) ;;
*)
  echo "release_bundle: unexpected staging path $stage" >&2
  exit 1
  ;;
esac
trap 'rm -r -- "$stage"' EXIT
dir="$stage/$name"
mkdir -p "$dir/scripts" "$dir/app/instadroid"

# The one block that builds the image locally, matched whole so a reworded compose file fails here.
build_block='# init and app run the same locally built image, so it'"'"'s built once.
x-app-image: &app_image
  build: ./app
  image: instadroid-app'
pull_block="# init and app run the published release image, pinned by digest.
x-app-image: &app_image
  image: $ref"
compose="$(cat docker-compose.yml)"
case "$compose" in
*"$build_block"*) ;;
*)
  echo "release_bundle: docker-compose.yml no longer has the x-app-image block this rewrites:" >&2
  printf '%s\n' "$build_block" >&2
  exit 1
  ;;
esac
printf '%s\n' "${compose/"$build_block"/"$pull_block"}" >"$dir/docker-compose.yml"

cp .env.example "$dir/.env.example"
cp scripts/init-data.sh scripts/guard-android-data.sh scripts/preflight.sh scripts/diagnose.sh \
  scripts/snapshot-android-data.sh scripts/tune-android.sh scripts/reset-resource-cache.sh "$dir/scripts/"
cp app/instadroid/crash_signatures.tsv app/instadroid/tune_packages.txt "$dir/app/instadroid/"

mkdir -p "$out"
archive="$(cd "$out" && pwd)/$name.tar.gz"
# Reproducible: sorted, no owners, one fixed mtime, and gzip without a name or timestamp.
tar -C "$stage" --sort=name --owner=0 --group=0 --numeric-owner --mtime="@${SOURCE_DATE_EPOCH:-0}" \
  -cf - "$name" | gzip -n >"$archive"
echo "$archive"
