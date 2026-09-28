#!/usr/bin/env bash
# Deletes the versions of a GHCR container package that no tag needs: what a publish.yml rehearsal
# pushes by digest and never tags. A version stays when
#   - it has a tag;
#   - a version that stays names it: an index's platform image and BuildKit attestation are untagged
#     versions of their own, as are the manifests under an attestation's `sha256-<digest>` tag;
#   - its manifest's `subject` is a version that stays (an attestation stored as a referrer);
#   - its manifest can't be read;
#   - it changed in the last <min-age-days>: a release's image is untagged from `build` to `publish`.
# A tagged version whose manifest can't be read fails the run before anything is deleted.
#
# Lists what it would delete; --delete deletes it, and exits 1 if a deletion failed. Needs jq, gh with
# GH_TOKEN (packages: read to list, write to delete) and a docker logged in to ghcr.io.
#
# Usage: .github/scripts/prune_untagged_images.sh [--delete] <owner/package> [min-age-days, default 7]
set -euo pipefail

usage="usage: $0 [--delete] <owner/package> [min-age-days]"
delete=""
if [ "${1:-}" = --delete ]; then
  delete=1
  shift
fi
image="${1:?$usage}"
min_age="${2:-7}"
image="${image,,}"
owner="${image%%/*}"
package="${image#*/}"

case "$(gh api "users/$owner" --jq .type)" in
Organization) scope="orgs/$owner" ;;
*) scope="users/$owner" ;;
esac
api="$scope/packages/container/${package//\//%2F}/versions"

manifest() { docker buildx imagetools inspect --raw "ghcr.io/$image@$1"; }

# one version per line: id, digest, updated_at, its tags joined by commas
versions="$(gh api --paginate "$api?per_page=100" \
  --jq '.[] | [.id, .name, .updated_at, (.metadata.container.tags | join(","))] | @tsv')"

declare -A keep=()
queue=()
while IFS=$'\t' read -r _ digest _ tags; do
  [ -z "$tags" ] || queue+=("$digest")
done <<<"$versions"
tagged="${#queue[@]}"

# every version a tagged one reaches, through indexes nested to any depth
while [ "${#queue[@]}" -gt 0 ]; do
  digest="${queue[0]}"
  queue=("${queue[@]:1}")
  [ -z "${keep[$digest]:-}" ] || continue
  keep[$digest]=1
  body="$(manifest "$digest")"
  while IFS=$'\t' read -r child type; do
    case "$type" in
    '') ;;
    *index* | *manifest.list*) queue+=("$child") ;;
    *) keep[$child]=1 ;;
    esac
  done < <(jq -r '.manifests[]? | [.digest, .mediaType] | @tsv' <<<"$body")
done

cutoff="$(date -u -d "$min_age days ago" +%s)"
listed=0 failed=0
while IFS=$'\t' read -r id digest updated tags; do
  [ -n "$id" ] && [ -z "$tags" ] && [ -z "${keep[$digest]:-}" ] || continue
  if [ "$(date -u -d "$updated" +%s)" -ge "$cutoff" ]; then
    echo "kept     $digest  $updated  newer than $min_age days"
    continue
  fi
  if ! body="$(manifest "$digest" 2>/dev/null)"; then
    echo "kept     $digest  $updated  manifest unreadable"
    continue
  fi
  subject="$(jq -r '.subject.digest // empty' <<<"$body")"
  if [ -n "$subject" ] && [ -n "${keep[$subject]:-}" ]; then
    echo "kept     $digest  $updated  refers to $subject"
    continue
  fi
  listed=$((listed + 1))
  if [ -z "$delete" ]; then
    echo "unused   $digest  $updated"
  elif gh api -X DELETE "$api/$id" --silent; then
    echo "deleted  $digest  $updated"
  else
    echo "FAILED   $digest  $updated"
    failed=$((failed + 1))
  fi
done <<<"$versions"

echo "$tagged tagged version(s), ${#keep[@]} in use; $listed unused${delete:+, $failed not deleted}"
[ "$failed" -eq 0 ]
