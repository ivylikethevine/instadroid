# OpenSSF improvements

A maintainer worksheet: where the OpenSSF Scorecard number is capped for a one-maintainer project and
what could still move it, and the OpenSSF Best Practices passing-level answer sheet with evidence, to
enter at [bestpractices.dev](https://www.bestpractices.dev/) once the project is registered. Work
already shipped isn't repeated here; [SECURITY.md](SECURITY.md#what-the-project-already-does) lists
it. Anything marked **to check** hasn't been confirmed against the live Scorecard report or the
repository settings yet.

## Contents

- [Scorecard: where the ceiling is](#scorecard-where-the-ceiling-is)
- [Best Practices: passing-level answer sheet](#best-practices-passing-level-answer-sheet)
  - [Basics](#basics)
  - [Change control](#change-control)
  - [Reporting](#reporting)
  - [Quality](#quality)
  - [Security](#security)
  - [Analysis](#analysis)

## Scorecard: where the ceiling is

`.github/workflows/scorecard.yml` publishes the score after every green CI run on a push to `main`,
weekly, and when a branch protection rule changes; the README badge reads it. The checks that are capped or open:

| Check               | Status                     | Why, and what would move it                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| ------------------- | -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Code-Review         | Capped                     | One maintainer ([GOVERNANCE.md](GOVERNANCE.md)), so nobody else can approve a pull request. A `Reviewed-by:` trailer would satisfy the scanner without a review having happened, and won't be added. Not fixable without a second person.                                                                                                                                                                                                                                                                           |
| Contributors        | Capped                     | The check wants contributors from at least two organizations; there's one.                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| Fuzzing             | Open, candidate            | No fuzzer runs. Python is covered by the check (atheris, via OSS-Fuzz or ClusterFuzzLite), so this isn't not-applicable: `instadroid.parsing.parse_hierarchy()` is pure and parses device-supplied XML, the natural first target ([TESTING.md](TESTING.md#fuzzing)).                                                                                                                                                                                                                                                |
| Branch-Protection   | To check                   | Depends on the rulesets for `main` and `dev` (required checks, required pull request, who can bypass), which live in the repository settings, not in this tree. `ci.yml`'s `CI result` roll-up is meant to be the one required check. Two required approving reviews, the top tier, needs a second person regardless.                                                                                                                                                                                               |
| CII-Best-Practices  | Open, needs the maintainer | Scores 0 until the project is registered at bestpractices.dev. The answer sheet below is ready for that.                                                                                                                                                                                                                                                                                                                                                                                                            |
| Signed-Releases     | To check, likely low       | Release images carry signed build-provenance and SBOM attestations, and the tags are signed, but the attestations are pushed to the registry, not uploaded as GitHub Release assets, and the check reads release assets only.                                                                                                                                                                                                                                                                                       |
| Pinned-Dependencies | To check                   | Third-party actions are SHA-pinned, the CI tools are SHA-256-pinned in `tools.txt`, pip installs use hashed locks (the setuptools build dependency included), the Markdown tools install with `npm ci` from `.github/package-lock.json` (transitive dependencies included), and every image is pinned by digest. Scorecard reads the `uses: $/.github/actions/...` self-repository references as unpinned third-party actions, which may be most of any "unpinned" count; count by hand before assuming a real gap. |
| Token-Permissions   | To check                   | Every workflow defaults to `contents: read` (Scorecard's own workflow to `read-all`, as `publish_results` requires) and grants writes per job.                                                                                                                                                                                                                                                                                                                                                                      |
| Packaging           | To check                   | `publish.yml` publishes to GHCR from a tag; confirm the check detects it.                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| Vulnerabilities     | To check                   | OSV reads both hashed locks; `advisories.yml` runs `pip-audit` on the same locks and `npm audit` on the Markdown tools' lock, weekly and after each merge to `main`, into one tracking issue.                                                                                                                                                                                                                                                                                                                       |

Expected to be at or near 10 with nothing to do: Binary-Artifacts (no binaries tracked), Dangerous-Workflow
(no `pull_request_target`; the `workflow_run` workflows, `advisories.yml`, `coverage.yml`, `image-scan.yml`,
`link-check.yml`, `pages.yml`, `scorecard.yml` and `tool-versions.yml`, act only on a green run from `main` and check
out no pull request code),
Dependency-Update-Tool (`.github/dependabot.yml`),
License (`LICENSE.md`, MIT), Maintained, SAST (CodeQL on every push and pull request) and
Security-Policy (`docs/SECURITY.md`). **To check** against the live report.

## Best Practices: passing-level answer sheet

**M** = Met, **N/A** = not applicable, **U** = Unmet. Evidence links point at the repository docs.

### Basics

| Criterion                             | Answer | Evidence                                                                                                                           |
| ------------------------------------- | ------ | ---------------------------------------------------------------------------------------------------------------------------------- |
| `description_good`                    | M      | [README](../README.md#what-it-does).                                                                                               |
| `interact`                            | M      | [SUPPORT.md](SUPPORT.md), [CONTRIBUTING.md](CONTRIBUTING.md).                                                                      |
| `contribution`                        | M      | [CONTRIBUTING.md](CONTRIBUTING.md#pull-requests).                                                                                  |
| `contribution_requirements`           | M      | [CONTRIBUTING.md](CONTRIBUTING.md): the checks to run, typing and coverage rules, which docs change with what.                     |
| `floss_license` / `floss_license_osi` | M      | MIT, [LICENSE.md](../LICENSE.md).                                                                                                  |
| `license_location`                    | M      | `LICENSE.md` at the repository root.                                                                                               |
| `documentation_basics`                | M      | [README](../README.md#first-time-setup), [docs/README.md](README.md).                                                              |
| `documentation_interface`             | M      | [OpenAPI spec](openapi.json) for the feed server; `.env.example` for every setting; `app/scraper.py`'s docstring for the commands. |
| `sites_https`                         | M      | GitHub and GitHub Pages, both HTTPS.                                                                                               |
| `discussion`                          | M      | GitHub issues and pull requests.                                                                                                   |
| `english`                             | M      | All docs and issue templates are in English.                                                                                       |
| `maintained`                          | M      | Commit and release history.                                                                                                        |

### Change control

| Criterion                           | Answer | Evidence                                                                                                                                                                      |
| ----------------------------------- | ------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `repo_public` / `repo_track`        | M      | Public git repository on GitHub.                                                                                                                                              |
| `repo_interim`                      | M      | Work lands on `dev` between releases ([GOVERNANCE.md](GOVERNANCE.md#how-a-change-gets-in)).                                                                                   |
| `repo_distributed`                  | M      | git.                                                                                                                                                                          |
| `version_unique` / `version_semver` | M      | `vX.Y.Z` tags ([RELEASING.md](RELEASING.md#the-tag-scheme)).                                                                                                                  |
| `version_tags`                      | M      | Every release is a signed, annotated git tag; `publish.yml`'s gate refuses one that doesn't verify against `.github/allowed_signers`.                                         |
| `release_notes`                     | M      | Each GitHub Release lists what changed, from each merged pull request's `## Release note` section, and the image digest ([RELEASING.md](RELEASING.md#what-the-tag-triggers)). |
| `release_notes_vulns`               | N/A    | No vulnerability has been fixed in a release yet. **To check** before answering.                                                                                              |

### Reporting

| Criterion                                    | Answer | Evidence                                                                                                           |
| -------------------------------------------- | ------ | ------------------------------------------------------------------------------------------------------------------ |
| `report_process` / `report_tracker`          | M      | GitHub issues with forms ([SUPPORT.md](SUPPORT.md)).                                                               |
| `report_responses` / `enhancement_responses` | M      | **To check**: needs a majority of bug reports and enhancement requests in the last 2-12 months to have a response. |
| `report_archive`                             | M      | GitHub issues are public and searchable.                                                                           |
| `vulnerability_report_process`               | M      | [SECURITY.md](SECURITY.md#reporting-a-vulnerability).                                                              |
| `vulnerability_report_private`               | M      | GitHub private vulnerability reporting. **To check** that it's enabled in the repository settings.                 |
| `vulnerability_report_response`              | M      | 14-day acknowledgement target ([SECURITY.md](SECURITY.md#what-happens-to-a-report)); no reports received yet.      |

### Quality

| Criterion                                            | Answer | Evidence                                                                                                                                                            |
| ---------------------------------------------------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `build` / `build_common_tools` / `build_floss_tools` | M      | `docker compose build` (Dockerfile), and pip-compile locks for the dev environment ([CONTRIBUTING.md](CONTRIBUTING.md#development-setup)).                          |
| `test`                                               | M      | pytest suite in `tests/` ([TESTING.md](TESTING.md)).                                                                                                                |
| `test_invocation`                                    | M      | `scripts/check.sh test`, or `pytest -q --cov` ([TESTING.md](TESTING.md#tier-1-unit-tests-and-coverage)).                                                            |
| `test_most`                                          | M      | 90% coverage floor enforced (`fail_under` in `pyproject.toml`); the README's coverage badge.                                                                        |
| `test_continuous_integration`                        | M      | `.github/workflows/ci.yml` on every pull request and push to `main`.                                                                                                |
| `test_policy` / `tests_are_added`                    | M      | [CONTRIBUTING.md](CONTRIBUTING.md#typing-and-coverage): new behavior comes with tests; CI fails under the floor.                                                    |
| `tests_documented_added`                             | M      | `.github/pull_request_template.md` checklist.                                                                                                                       |
| `warnings` / `warnings_fixed` / `warnings_strict`    | M      | ruff, basedpyright strict with `reportAny`, and shellcheck, all failing CI; hadolint runs as an advisory warning ([CONTRIBUTING.md](CONTRIBUTING.md#what-ci-runs)). |

### Security

| Criterion                                                          | Answer | Evidence                                                                                                                                                                                                                     |
| ------------------------------------------------------------------ | ------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `know_secure_design` / `know_common_errors`                        | M      | [SECURITY.md](SECURITY.md#assurance-case).                                                                                                                                                                                   |
| `crypto_published` / `crypto_call` / `crypto_floss`                | M      | The only cryptography is HMAC-SHA256 from Python's standard library (`feedserver.auth.media_sig`) and `hmac.compare_digest`.                                                                                                 |
| `crypto_keylength`                                                 | M      | The feed token is operator-chosen; `.env.example` generates one with `secrets.token_urlsafe(32)`. Media signatures keep 128 bits of the HMAC. **To check** the wording.                                                      |
| `crypto_working` / `crypto_weaknesses`                             | M      | No broken algorithms; HMAC-SHA256 only.                                                                                                                                                                                      |
| `crypto_pfs`                                                       | N/A    | The software terminates no TLS; a reverse proxy does, if any.                                                                                                                                                                |
| `crypto_password_storage`                                          | N/A    | The project stores no user passwords of its own; the Instagram password is the operator's, passed to the device.                                                                                                             |
| `crypto_random`                                                    | N/A    | The project generates no keys or nonces; the token is the operator's.                                                                                                                                                        |
| `delivery_mitm` / `delivery_unsigned`                              | M      | Images from GHCR over HTTPS, from a signed tag, with signed build-provenance and SBOM attestations ([RELEASING.md](RELEASING.md#verifying-a-published-image)).                                                               |
| `vulnerabilities_fixed_60_days` / `vulnerabilities_critical_fixed` | M      | No known unpatched vulnerabilities; a release is blocked on a fixable HIGH or CRITICAL finding in its image. **To check** the Security tab (Dependabot, Trivy, CodeQL) and the `advisories` tracking issue before answering. |
| `no_leaked_credentials`                                            | M      | gitleaks in CI; `.gitignore` excludes `.env` and key files.                                                                                                                                                                  |

### Analysis

| Criterion                                   | Answer | Evidence                                                                                                                   |
| ------------------------------------------- | ------ | -------------------------------------------------------------------------------------------------------------------------- |
| `static_analysis` / `static_analysis_often` | M      | CodeQL (Python and Actions), ruff and basedpyright on every push and pull request.                                         |
| `static_analysis_common_vulnerabilities`    | M      | CodeQL's security queries; zizmor for the workflows.                                                                       |
| `static_analysis_fixed`                     | M      | **To check** the Security tab for open medium-or-higher findings.                                                          |
| `dynamic_analysis`                          | U      | No fuzzer or sanitizer yet (see [Fuzzing](#scorecard-where-the-ceiling-is) above). SUGGESTED, so it doesn't block passing. |
| `dynamic_analysis_unsafe`                   | N/A    | Python only; no memory-unsafe language in the project's own code.                                                          |
| `dynamic_analysis_enable_assertions`        | M      | The test suite asserts invariants directly, and basedpyright strict rules out untyped values before they run.              |
| `dynamic_analysis_fixed`                    | N/A    | No dynamic analysis findings to fix.                                                                                       |
