---
title: Security policy
---

# Security policy

## Supported versions

instadroid is **experimental until v1.0.0**, so there is no supported-version table yet. Fixes land
on `main` and ship in the next release; older releases are not patched. Run the latest release
image, or `main`, before reporting.

## Reporting a vulnerability

Please report privately through GitHub: the repository's **Security** tab → **Report a
vulnerability**. Don't open a public issue, discussion or pull request for a security problem.

Include what you can of:

- what an attacker can do, and from where (the LAN, the same host, a malicious feed reader, ...)
- steps to reproduce, and the version or commit you tested
- any relevant settings (`FEED_HOST`, `PUBLIC_URL`, how the stack is exposed)

This is a small project maintained on a best-effort basis. You'll get an acknowledgement, and a
fix or an explanation of why something is out of scope, but there's no fixed response time.

## What's in scope

The reports that matter most here aren't a CVE in a dependency (Dependabot and `pip-audit` already
track those). They are:

- **Credential handling.** `IG_USERNAME` and `IG_PASSWORD` are read from `.env` as plain environment
  variables, or from files (`IG_PASSWORD_FILE`, e.g. a Docker secret). Any way they can leak (into
  logs, `/status`, debug dumps, the feeds, or the published image) is in scope.
- **The feed server** (`app.py`: `/instagram.xml`, `/stories.xml`, `/opml`, `/media`, `/status`,
  `/health`). It's unauthenticated unless `FEED_TOKEN` is set. Anything that reaches beyond what it's
  meant to serve is in scope: getting past the token, a media signature that opens a file it wasn't
  issued for, path traversal out of the media directory, injection through captions or usernames
  into the Atom/HTML output, or reading the database or `.env`.
- **Secrets in logs.** `FRESHRSS_REFRESH_URL` carries an API token and is logged with its query
  string stripped, and a `?token=` feed token is blanked in the access log. A way to get either token
  into a log is in scope.
- **The release pipeline and image**: the GitHub Actions workflows, the published
  `ghcr.io/ivylikethevine/instadroid` image and its build-provenance attestation, and the pinned
  `apkeep` binary baked into it.

## Known risks and design limits

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
- **Upstream issues** belong upstream: redroid, the host kernel's binder driver (see `CLAUDE.md`),
  FreshRSS, and Python dependencies. Report them here only if instadroid makes them exploitable in a
  way they otherwise wouldn't be.

## Hardening checklist for operators

- Keep `FEED_HOST=127.0.0.1` unless `FEED_TOKEN` is set, ideally behind TLS.
- `chmod 600 .env`, and never commit it (`.gitignore` already excludes it). Or move the password out
  of it with `IG_PASSWORD_FILE`.
- Don't publish ADB (`5555`) or the feed port (`8000`) beyond the host.
- Keep `local/data/` out of backups or sync folders you wouldn't trust with your Instagram session.
- Verify a release image before running it:
  `gh attestation verify oci://ghcr.io/ivylikethevine/instadroid@<digest> --repo ivylikethevine/instadroid`

## What the project already does

- Every GitHub Action is pinned to a commit SHA, and checkouts use `persist-credentials: false`.
- CI runs CodeQL, gitleaks (secret scanning), `pip-audit` and shellcheck; Dependabot tracks
  dependency updates.
- Release images are pushed by digest and smoke-tested, then tagged in a separate job gated by the
  `publish` deployment environment, and carry a build-provenance attestation.
- The `apkeep` binary in the image is pinned to a release and verified against a SHA-256 checksum.
