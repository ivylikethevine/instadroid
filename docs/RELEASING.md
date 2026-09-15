# Releasing

How a release is cut: the tag scheme, what a `v*` tag triggers, the gates it has to pass, and how to
verify what it published. Only the maintainer cuts releases ([GOVERNANCE.md](GOVERNANCE.md)); a
contributor's part is the pull request's `## Release note` section
([CONTRIBUTING.md](CONTRIBUTING.md#pull-requests)).

## Contents

- [What ships](#what-ships)
- [The tag scheme](#the-tag-scheme)
- [Cutting a release](#cutting-a-release)
- [What the tag triggers](#what-the-tag-triggers)
  - [Rehearsing the pipeline](#rehearsing-the-pipeline)
- [Verifying a published image](#verifying-a-published-image)
- [Prereleases](#prereleases)
- [No CHANGELOG](#no-changelog)

## What ships

One container image, `ghcr.io/ivylikethevine/instadroid`, built from `app/`: the scraper and the
feed server. Nothing is published to PyPI; `pyproject.toml` only exists for the dev venv. The redroid
image and FreshRSS are third-party images pulled by `docker-compose.yml`, not part of a release.
`docker-compose.yml` builds the app image locally by default; each GitHub Release's notes show how to
pin the published one instead.

## The tag scheme

Releases are `vX.Y.Z` tags on a commit that is on `main`, or `vX.Y.Z-<prerelease>` for a
[prerelease](#prereleases); build metadata (`+...`) isn't accepted. **The version is the tag**:
`pyproject.toml`'s `version = "0.0.0.dev0"` is a fixed placeholder that is never bumped, since nothing
reads it. The image tags come from the git tag alone (`docker/metadata-action`'s `semver` patterns in
`publish.yml`).

Release tags are **signed, annotated** tags, signed with an SSH key listed in
`.github/allowed_signers`: the maintainer's signing keys, one per machine. The gate reads that file
from the tagged commit, which has to be on `main`, so a key only gets there through a merged pull
request. To rotate a key, add the new one through a pull request and merge it into `main` before
tagging with it, and remove a retired key the same way.

## Cutting a release

1. Merge `dev` into `main` through a pull request, squash-merged
   ([GOVERNANCE.md](GOVERNANCE.md#how-a-change-gets-in)). Its `## Release note` section collects the
   notes of everything it brings ([CONTRIBUTING.md](CONTRIBUTING.md#pull-requests)): it's what the
   release page will say.
2. Wait for `ci.yml` to go green on that `main` commit. (Tagging right away is fine too: the gate
   below waits for it.)
3. Tag that commit with a signed tag and push it (git configured to sign with an SSH key in
   `.github/allowed_signers`, `gpg.format=ssh`):

   ```bash
   git tag -s vX.Y.Z -m "instadroid vX.Y.Z"   # signed, on main's tip
   git push origin vX.Y.Z
   ```

Nothing else is manual: the pipeline below runs unattended, with no approval step.

## What the tag triggers

Pushing a `v*` tag runs `.github/workflows/publish.yml`: **gate → build → scan → publish → release**.
Each job needs every job before it, so nothing is tagged or released unless the gate, the build and
the scan all passed. Releases run **unattended**; the checks are release tag protection, so only the
maintainer can push one, and the jobs themselves:

1. **`gate`** refuses to go on unless the tag is `vX.Y.Z` or `vX.Y.Z-<prerelease>`; it's a signed,
   annotated tag that verifies (`git tag -v`) against `.github/allowed_signers`, and still points at
   the commit the run started for; that commit is on `main` (compared with `main` through the API);
   and `ci.yml` concluded successfully on that exact commit from a push, the weekly schedule or a
   dispatch. A draft pull request's run skips every job and still reports success, so pull request
   runs don't count. If CI is still running, the gate waits for it, up to 20 minutes.
2. **`build`** builds the app image with no build cache, so a stale `apt-get upgrade` layer can't
   ship, and pushes it to GHCR **by digest only**, with no tag. BuildKit stores an SBOM and full
   (`mode=max`) build provenance in the pushed index. It then smoke-tests that exact digest
   (`.github/scripts/smoke-test.sh`). A broken build fails here, before anything a user could pull by
   tag exists.
3. **`scan`** runs Trivy over that digest and **blocks the release on any fixable HIGH or CRITICAL
   vulnerability**. The findings go to the Security tab under the `release-image` category, and the
   step summary. The fix is usually a rebuild or a base-image repin. A finding that has to be
   accepted goes in the root `.trivyignore`, with a comment giving the reason and an `exp:` date, so
   the acceptance lapses on its own.
4. **`publish`**, named "promote and tag release image", runs in the `publish` GitHub Environment,
   which has no required reviewer. It tags the already-scanned digest with `vX.Y.Z`, `vX.Y` and
   `latest` (no rebuild), and pushes two signed attestations to the registry beside it: build
   provenance, and the SBOM from the build (SPDX), attested as the image already carries it.
5. **`release`** creates the GitHub Release with `gh release`. Its "What changed" is the
   `## Release note` section of each pull request merged since the previous non-prerelease tag
   (`.github/scripts/release_notes.sh`), skipping `none`; only when no pull request wrote a note does
   GitHub's generated list of merged pull requests stand in. Below that: the image digest and tags,
   the pull command, a compose snippet that pins the published image, and the commands to verify the
   provenance and the SBOM. A re-run edits the release an earlier attempt created.

Every job starts with `step-security/harden-runner`. In `publish` and `release`, which hold the
package-write, signing and contents-write tokens, it **blocks** all network egress but an allowlist:
GHCR, the GitHub API and public-good Sigstore for `publish`, and github.com and the GitHub API for
`release`. The other jobs only record egress.

Releases share one `release` concurrency group that never cancels, so two tags pushed close
together publish one after the other rather than interleaving.

### Rehearsing the pipeline

Dispatching `publish.yml` by hand rehearses it on the dispatched commit: the gate (green CI only;
there's no tag to check), the build, the smoke test and the scan. `publish` and `release` run only for
a pushed tag, so a rehearsal **never promotes or releases anything**. It runs in its own concurrency
group, so it can't displace a queued release. Its image is still pushed by digest, with no tag, and
stays in GHCR as an untagged package version until it's deleted.

## Verifying a published image

Every release image carries a build-provenance attestation and an SBOM attestation. Verify them
against the digest from the release notes before running it:

```bash
gh attestation verify oci://ghcr.io/ivylikethevine/instadroid@<digest> --repo ivylikethevine/instadroid
gh attestation verify oci://ghcr.io/ivylikethevine/instadroid@<digest> --repo ivylikethevine/instadroid \
  --predicate-type https://spdx.dev/Document/v2.3
```

## Prereleases

A tag with a hyphen, such as `v1.0.0-rc1`, is published as a GitHub prerelease that is never marked
the latest release. Its image gets only its own tag, `v1.0.0-rc1`: no `vX.Y` and no `latest`
(`docker/metadata-action`'s `latest=auto` skips a semver prerelease). It passes through the same gate
and scan. The final release's notes cover its release candidates' pull requests too, since they're
counted from the previous non-prerelease tag.

## No CHANGELOG

There's deliberately **no CHANGELOG**. What changed in a release is the pull requests merged since the
last one, each with its `## Release note` section, and the GitHub Release lists them; a
hand-maintained file would only duplicate them, and drift from what actually shipped.
