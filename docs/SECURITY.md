# Security policy

What instadroid defends against and why the existing controls fit that, which versions get fixes, and
how to report a vulnerability. instadroid is **experimental until v1.0.0**; see
[Supported versions](#supported-versions).

## Contents

- [Threat model](#threat-model)
  - [What's in scope](#whats-in-scope)
  - [Known risks and design limits](#known-risks-and-design-limits)
  - [Hardening checklist for operators](#hardening-checklist-for-operators)
- [Trust boundaries](#trust-boundaries)
- [Assurance case](#assurance-case)
- [What the project already does](#what-the-project-already-does)
- [Supported versions](#supported-versions)
- [Reporting a vulnerability](#reporting-a-vulnerability)
  - [What happens to a report](#what-happens-to-a-report)

## Threat model

instadroid runs on one operator's own host, for that operator: no hosted service, no multi-user
accounts, no telemetry. What's worth protecting is the operator's **Instagram credentials and
logged-in session**, the **scraped content** (which includes posts and media from private accounts
the operator follows), and **the host itself**, since redroid runs privileged. The attackers that
matter are someone on the same LAN or the same host, a malicious or compromised feed reader, a web
page the operator's browser visits (cross-site requests to a loopback server), and a compromised
dependency or build step.

### What's in scope

The reports that matter most here aren't a CVE in a dependency (Dependabot and `pip-audit` already
track those). They are:

- **Credential handling.** `IG_USERNAME` and `IG_PASSWORD` are read from `.env` as plain environment
  variables, or from files (`IG_PASSWORD_FILE`, e.g. a Docker secret). Any way they can leak (into
  logs, `/status`, debug dumps, the feeds, or the published image) is in scope.
- **The feed server** (`app/feedserver/`: `/instagram.xml`, `/stories.xml`, `/opml`, `/media`, `/status`,
  `/health`). It's unauthenticated unless `FEED_TOKEN` is set. Anything that reaches beyond what it's
  meant to serve is in scope: getting past the token, triggering `/control` changes from another
  site (cross-site POST/DELETE requests are refused), a media signature that opens a file it wasn't
  issued for, path traversal out of the media directory, injection through captions or usernames
  into the Atom/HTML output, or reading the database or `.env`.
- **Secrets in logs.** `FRESHRSS_REFRESH_URL` and `ALERT_URL` can carry tokens and are logged without
  their query string or credentials, and a `?token=` feed token is blanked in the access log. A way to
  get either token into a log is in scope.
- **The release pipeline and image**: the GitHub Actions workflows, the release tag signing check,
  the published `ghcr.io/ivylikethevine/instadroid` image and its build-provenance and SBOM
  attestations, and the pinned `apkeep` binary baked into it.

### Known risks and design limits

These are known and documented, not vulnerabilities in themselves:

- **The feed server is unauthenticated by default.** It binds `127.0.0.1` by default (`FEED_HOST`).
  Setting `FEED_HOST=0.0.0.0` without `FEED_TOKEN` serves everything scraped, including media from
  private accounts you follow, to anyone who can reach the port. With `FEED_TOKEN`, the token travels
  in plain HTTP unless a TLS reverse proxy sits in front, and it's embedded in `/opml`'s feed URLs
  (so in whatever reader imports them). `/health` stays open and reveals the post count.
- **ADB is unauthenticated.** redroid's adbd runs with `ro.adb.secure=0`, and compose publishes it
  on `127.0.0.1:5555`. Anyone who can reach that port can fully control the device, including the
  logged-in Instagram session. Never publish it beyond loopback.
- **redroid runs privileged**, as redroid requires. A compromise of the Android container should be
  treated as a compromise of the host.
- **The Instagram APK comes from APKPure**, a third-party mirror, via `apkeep`. Android checks an
  update's signature against the installed app, but a fresh install trusts whatever APKPure served.
- **`local/data/` is sensitive.** `local/data/android` holds the logged-in Instagram session, and
  `db/`, `media/` and `debug/` hold scraped content and screenshots of your feed.
- **Instagram account enforcement** (challenges, locks, bans) is a terms-of-service risk of
  automating an account, not a security issue in this project.
- **Upstream issues** belong upstream: redroid, the host kernel's binder driver (see [INCIDENTS.md](INCIDENTS.md)),
  FreshRSS, and Python dependencies. Report them here only if instadroid makes them exploitable in a
  way they otherwise wouldn't be.

### Hardening checklist for operators

- Keep `FEED_HOST=127.0.0.1` unless `FEED_TOKEN` is set, ideally behind TLS.
- `chmod 600 .env`, and never commit it (`.gitignore` already excludes it). Or move the password out
  of it with `IG_PASSWORD_FILE`.
- Don't publish ADB (`5555`) or the feed port (`8000`) beyond the host.
- Keep `local/data/` out of backups or sync folders you wouldn't trust with your Instagram session.
- Verify a release image before running it ([RELEASING.md](RELEASING.md#verifying-a-published-image)):
  `gh attestation verify oci://ghcr.io/ivylikethevine/instadroid@<digest> --repo ivylikethevine/instadroid`

## Trust boundaries

The boundaries are drawn in full, with the component each sits between, in
[ARCHITECTURE.md](ARCHITECTURE.md#trust-boundaries): the operator and the Instagram credentials, the
host and the unauthenticated, privileged device, the app and APKPure, the feed server and its
readers, and the app and the outbound webhooks. Where state lives, and so what a leaked directory
exposes, is in [ARCHITECTURE.md](ARCHITECTURE.md#where-state-lives).

## Assurance case

The argument that secure design principles were applied against the threat model and the trust
boundaries above; not a claim that the project is free of bugs.

| Principle                                 | How it holds                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| ----------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Minimal exposure by default               | The feed server binds loopback unless `FEED_HOST` says otherwise (`feedserver.settings`), and warns at startup when a non-loopback host has no token (`feedserver/__init__.py`); compose publishes ADB on `127.0.0.1` only, and FreshRSS listens on loopback (`FRESHRSS_LISTEN`).                                                                                                                                                                                  |
| Authenticate every path but liveness      | With `FEED_TOKEN` set, the `feedserver.auth.require_token` middleware sends every request through `authorized()`, which accepts a bearer token, basic-auth password or `?token=` compared with `hmac.compare_digest`; only `OPEN_PATHS` (`/health`) is exempt.                                                                                                                                                                                                     |
| Least privilege for readers               | Media URLs carry `media_sig()`, an HMAC-SHA256 of that one media path under the token, so a reader that loads images never holds the token and a signature opens only the file it was issued for.                                                                                                                                                                                                                                                                  |
| Refuse cross-site state changes           | `feedserver.auth.cross_site()` refuses a POST or DELETE whose `Origin` isn't the server's own, so a web page can't lock the scraper or trigger runs through a loopback server with no token.                                                                                                                                                                                                                                                                       |
| Untrusted text is escaped, not trusted    | Captions come from other people's posts; `feedserver.render.caption_html()` escapes the text and every generated @mention and #hashtag link before it reaches the Atom/HTML output.                                                                                                                                                                                                                                                                                |
| Secrets stay out of logs, dumps and files | `feedserver.auth.SkipHealthcheck` blanks `?token=` in the access log; `instadroid.common.redact_url` strips credentials and query strings from `ALERT_URL`/`FRESHRSS_REFRESH_URL`; `ensure_logged_in()` takes its login screenshot before typing credentials; `shared.fileenv.env_secret` reads any secret from a `*_FILE` instead of `.env`.                                                                                                                      |
| Least privilege in the image              | The app runs as uid 1000 (`USER 1000` in `app/Dockerfile`), with pip removed after installing dependencies; `init` is the only service that runs as root, and only to create directories.                                                                                                                                                                                                                                                                          |
| Pinned, verified supply chain             | Python dependencies install from hashed locks with `--require-hashes`; every image is pinned by digest and `apkeep` by checksum (`ADD --checksum`); every action is pinned to a commit SHA and every CI tool by SHA-256 (`tools.txt`); a release needs a tag signed by a key in `.github/allowed_signers`, and its image is built once, smoke-tested and scanned by digest before it's tagged, then attested ([RELEASING.md](RELEASING.md#what-the-tag-triggers)). |
| Fail closed on unsafe device state        | `scripts/guard-android-data.sh` refuses to start redroid on a `/data` volume from a different Android major version, and `instadroid.device.MemoryGuard` stops a run before the container's memory limit.                                                                                                                                                                                                                                                          |

**What is not countered, by design**: the unauthenticated ADB port and the privileged redroid
container (both required by redroid), plain-HTTP token transport without a reverse proxy, and a
fresh APK install trusting APKPure. They're listed under
[Known risks and design limits](#known-risks-and-design-limits).

## What the project already does

- **Workflows.** Every GitHub Action is pinned to a commit SHA, checkouts use
  `persist-credentials: false`, and workflow tokens default to read-only, with writes granted per
  job. Every job starts with `step-security/harden-runner`, which records the runner's network
  egress, and blocks everything but an allowlist in the release jobs that hold write and signing
  tokens. actionlint, zizmor and `.github/scripts/lint_workflows.sh` lint the workflows themselves,
  and CodeQL analyses them.
- **Pinned tools.** The CI tools (actionlint, shellcheck, hadolint, Trivy, lychee) are installed
  from release assets pinned by SHA-256 in `.github/actions/setup-tool/tools.txt`, and
  `scripts/check.sh --install` fetches the same pins locally. The Markdown tools come from
  `.github/package-lock.json`, which pins their whole dependency tree. `tool-versions.yml` tracks the
  drift of the pins Dependabot can't see in one tracking issue.
- **Dependencies.** Python dependencies are installed from pip-compile locks with
  `--require-hashes`, in CI and in the image, and the project's own setuptools build dependency comes
  hashed from the dev lock. `pip-audit` checks both Python locks on every pull request, GitHub's
  dependency review fails a pull request that adds a dependency with a high-severity advisory, and
  `advisories.yml` re-audits the Python locks (`pip-audit`) and the Markdown tools' lock
  (`npm audit`) weekly and after each merge to `main`, keeping one tracking issue open while
  anything is found.
- **Dependabot** tracks pip, npm, the Dockerfile's base image, compose's images and GitHub Actions,
  in one weekly pull request into `dev` with a 7-day cooldown, so a compromised release has a week
  to be caught before it's proposed.
- **Images.** Every image is pinned by digest: the Dockerfile's Python base, and compose's redroid
  and FreshRSS. `image-scan.yml` scans those pins after every green CI run on `main` and weekly, and
  opens a tracking issue when a repin would close fixable HIGH or CRITICAL findings; it also scans
  the published image's `latest`, which goes stale between releases, into the Security tab. The
  image's Python base takes Debian's security updates at build time, runs as a non-root user, and
  has pip removed after installing dependencies. The `apkeep` binary in it is pinned to a release and
  verified against a SHA-256 checksum.
- **Scanning.** CI also runs CodeQL (Python and GitHub Actions), gitleaks (secret scanning),
  shellcheck, typos, and hadolint and Trivy over the built image as advisory checks, with Trivy's
  findings in the Security tab. `link-check.yml` checks the docs' external links as an advisory
  tracking issue. OpenSSF Scorecard runs after every green CI run on `main`, weekly, and when branch
  protection changes.
- **Releases** run unattended from a protected `vX.Y.Z` tag, only when the tag is signed by a key in
  `.github/allowed_signers`, its commit is on `main`, and CI has passed on that commit. The image is
  built without a cache, pushed by digest, smoke-tested, and scanned with Trivy, which **blocks** the
  release on a fixable HIGH or CRITICAL vulnerability; only then is it tagged, in a separate job
  without a rebuild, with signed build-provenance and SBOM attestations
  ([RELEASING.md](RELEASING.md#what-the-tag-triggers)).
- **Scrubbed fixtures.** Fixtures from real dumps go through `promote-dump`'s scrubbing. Older
  commits still carry real identifiers from before that
  ([PROFILES.md](PROFILES.md#leak-scan-2026-09-14)); the maintainer keeps an offline runbook for
  rewriting that history, outside the repository, not yet run ([ROADMAP.md](ROADMAP.md)).

[OPENSSF-IMPROVEMENTS.md](OPENSSF-IMPROVEMENTS.md) tracks what's still open against OpenSSF Scorecard
and the Best Practices criteria.

## Supported versions

instadroid is **experimental until v1.0.0**, so there is no supported-version table yet. Fixes land
on `main` and ship in the next release; older releases are not patched. Run the latest release
image, or `main`, before reporting.

## Reporting a vulnerability

Please report privately through GitHub: the repository's **Security** tab → **Report a
vulnerability**, or directly at
<https://github.com/ivylikethevine/instadroid/security/advisories/new>. Don't open a public issue,
discussion or pull request for a security problem.

Include what you can of:

- what an attacker can do, and from where (the LAN, the same host, a malicious feed reader, ...)
- steps to reproduce, and the version or commit you tested
- any relevant settings (`FEED_HOST`, `PUBLIC_URL`, how the stack is exposed)

### What happens to a report

- **Acknowledgement within 14 days** of the advisory being filed. This is a one-maintainer project
  maintained on a best-effort basis, so that's a target, not a contractual SLA.
- **Triage**: you'll hear whether it's confirmed and being fixed, confirmed but out of scope (with the
  reason; see [What's in scope](#whats-in-scope)), or needs more detail to reproduce.
- **Coordinated disclosure.** A confirmed vulnerability is fixed before it's discussed publicly,
  unless the reporter and maintainer agree otherwise; the aim is a fix within 60 days of the report.
  The fix ships in a release, and the advisory is published with it.
- **Credit.** Reporters are credited in the advisory and the release notes unless they ask not to be.

Last reviewed 2026-09.
