# Contributing

Thanks for looking at instadroid. It's experimental until v1.0.0, so expect things to move. Bug
reports, selector fixes for a new Instagram version, and focused pull requests are all welcome.

Please read the [code of conduct](CODE_OF_CONDUCT.md) first. For a security problem, don't open an
issue: follow [SECURITY.md](SECURITY.md). For help with your own setup, see [SUPPORT.md](SUPPORT.md);
for who decides what, [GOVERNANCE.md](GOVERNANCE.md).

## Contents

- [Reporting a bug](#reporting-a-bug)
- [Development setup](#development-setup)
- [Typing and coverage](#typing-and-coverage)
- [Supporting an Instagram version](#supporting-an-instagram-version)
- [Running against a real device](#running-against-a-real-device)
- [What CI runs](#what-ci-runs)
- [Which docs change with what](#which-docs-change-with-what)
- [Pull requests](#pull-requests)
- [Releases](#releases)
- [AI-assisted contributions](#ai-assisted-contributions)

## Reporting a bug

Use the [bug report form](https://github.com/ivylikethevine/instadroid/issues/new?template=bug_report.yml),
which asks for the output of `scripts/diagnose.sh` along with the rest of this. Include:

- the release or commit you're on, and the `IG_PROFILE` in use
- the installed Instagram version and redroid image (both shown on `/status`)
- the relevant scraper log lines
- for a parsing or navigation problem, the debug dump from `local/data/debug` (`last_hierarchy.xml`
  and friends). **Scrub it first**: dumps and screenshots contain real usernames, captions and
  photos from your feed.

Never paste `.env`, `FRESHRSS_REFRESH_URL` (it carries a token), or anything from
`local/data/android`.

## Development setup

```bash
python -m venv local/.venv && . local/.venv/bin/activate
pip install --require-hashes -r app/requirements.txt -r requirements-dev.txt   # the locks
pip install --no-build-isolation --no-deps -e .   # app/ on the path, and the dev commands
```

Dependencies are locked with hashes. Edit `app/requirements.in` or `requirements-dev.in`, not the
`.txt` locks, then regenerate the locks with pip-tools, app first (the dev lock is constrained to
it, so shared dependencies match), using the command in each lock's header. Installing both locks
together fails on a version conflict, which is the sign the dev lock needs regenerating after an
app lock update:

```bash
(cd app && pip-compile --allow-unsafe --generate-hashes --strip-extras requirements.in)
pip-compile --allow-unsafe --generate-hashes --strip-extras requirements-dev.in
```

Before sending a change, run what CI runs. `scripts/check.sh` is the one entry point: each of
`ci.yml`'s jobs calls it with its own group, so the local run and CI can't drift apart.

```bash
scripts/check.sh --install   # once: tools.txt's pinned CI tools, into local/ci-tools
scripts/check.sh             # every check
scripts/check.sh --fast      # python + test, the inner loop for a Python change
scripts/check.sh --lint      # every static check, no tests
scripts/check.sh shell typos # only those groups or checks; `scripts/check.sh --help` lists them
```

The groups mirror CI's jobs: `python` (ruff, basedpyright, `lint-imports`, `local-annotations`), `test` (pytest with
coverage, failing under the floor), `audit` (`pip-audit`), `shell` (shellcheck, shfmt), `docs`
(markdownlint, prettier, lychee on relative links, typos, and the docs drift check), `workflows`
(actionlint, `.github/scripts/lint_workflows.sh`, zizmor) and `docker` (hadolint, advisory, and
`docker compose config`). It takes the Python tools from the dev venv, runs `npm ci` in `.github` for
the locked Markdown tools, and reports a tool it can't find as SKIP; under CI a missing tool fails
instead. The image build, smoke test and Trivy scan stay CI-only, as do gitleaks and dependency
review.

After changing a route in `app/feedserver/`, run `export-openapi` to regenerate
[docs/openapi.json](openapi.json); the test suite fails until you do. [TESTING.md](TESTING.md) has
the test tiers, fixtures and coverage in detail.

Most device-driving code is tested against `tests/fakedevice.py`, a scripted stand-in for a
device, so the suite needs no emulator and no Instagram account. New behavior should come with tests
in `tests/`.

The scraper lives in `app/instadroid/` (the package docstring lists the modules); `app/scraper.py` is
only the command line. The feed server is `app/feedserver/`, `app/shared/` is the leaf both of them
import, and `app/devtools/` holds the development commands, which aren't shipped in the image.
[ARCHITECTURE.md](ARCHITECTURE.md#component-map) has the full layout and the import boundaries.
Modules call each other as `device.human_pause(...)` and read settings as `config.NAME`, never
`from .device import human_pause`, so a test's `monkeypatch.setattr(device, "human_pause", ...)`
reaches every caller.

## Typing and coverage

> **Rule: all Python code must be 100% type annotated and at least 90% covered by tests.** That means
> app code, scripts and tests alike, every function signature and every local variable, with no
> `Any`, `cast()` or type-checker suppressions. CI enforces both: basedpyright strict (with
> `reportAny`), ruff's annotation rules and `local-annotations` for the first, and coverage's
> `fail_under = 90` over `app/` (`pyproject.toml`) for the second. A change that lowers either
> doesn't merge.

- ruff's `ANN` rules require an annotation on every function and ban an explicit `Any`;
- `local-annotations` (`app/devtools/local_annotations.py`, which spells out the rule) requires an
  annotation on every local variable where it's first bound, including tuple unpacking, `:=` and
  `with ... as` (declare the name on the line before); `for` targets and comprehension variables,
  which Python can't annotate, are exempt;
- basedpyright checks `app/` and `tests/` in strict mode with `reportAny`, so no
  value typed `Any` gets through, not even one returned by the standard library;
- libraries that ship no type information (uiautomator2, adbutils, feedgen) get local stubs in
  `typings/`.

## Supporting an Instagram version

Everything specific to one Instagram major version lives in its own directory,
`app/igprofiles/vXYZ/`: selectors, the APK build to install, behavior overrides, and test fixtures.
The oldest supported version is 424. [PROFILES.md](PROFILES.md) describes the design and walks through
adding a version step by step. The short version:

- **Use `new-profile`** to take a capture-mode baseline run of the build, see which selector
  keys each screen is missing, promote fixtures and record the build as validated. A version that
  changes nothing gets no profile of its own; `fork` creates one only when something drifted.
- **Change the version's own profile, not shared code.** If 447 renamed a resource-id, fork a `v447`
  profile and override that key in `v447/selectors.py`. If it changed behavior, override the
  `@versioned` function as a method on `v447`'s `Profile`. Don't add `if version == ...` checks to
  `app/instadroid/`.
- **Keep older versions passing.** The whole existing test suite runs against the root profile,
  `v424`, `tests/test_replay.py` replays every validated version's fixtures, and
  `test_every_profile_meets_the_contract` checks every profile directory automatically.
- **Fixtures must be synthetic or scrubbed.** A dump from a real feed goes into `vXYZ/fixtures/` only
  through `promote-dump`, which replaces the usernames, names, places and captions it can
  identify, and only after you've read the leftover text it prints.

## Running against a real device

Parts of this project drive a real redroid container and a real Instagram account. Read
[INCIDENTS.md](INCIDENTS.md) before doing that on your own host: it covers incidents that cost real
time, including a kernel panic, `/data` corruption from mixing Android versions, and a whole-host
freeze from a scrape that ran out of memory. The rules that came out of them:

- Before a device-driving run (`scraper.py login`, `once`, `scrape-now`, `new-profile baseline` or
  `restore`), check `docker stats` headroom and force-stop Instagram. Keep scrape test runs short
  (`MAX_SCROLLS=5`, `MAX_STORIES_PER_RUN=2`), and don't interleave manual `adb`/`am` commands with a
  run.
- Never point two different Android major-version redroid images at the same `/data` volume
  ([COMPATIBILITY.md](COMPATIBILITY.md#one-android-version-per-data-volume)).
- Don't lower redroid's `mem_limit` without remeasuring peak memory first; the numbers are in
  [RUNLOG.md](RUNLOG.md) and [INCIDENTS.md](INCIDENTS.md).
- Never disable `com.android.packageinstaller`, and test any other `pm disable-user` change against a
  full cold restart, not just the running instance.
- When boot is slow or adb is stuck, run `scripts/diagnose.sh` (it reads logcat and host `dmesg`)
  before restarting the container again.
- Never publish ADB (port 5555) or an unauthenticated feed beyond localhost.

AI agents working in this repository follow the same rules, plus a few agent-specific ones, in
[CLAUDE.md](https://github.com/ivylikethevine/instadroid/blob/main/CLAUDE.md).

## What CI runs

`.github/workflows/ci.yml`, on every non-draft pull request, every push to `main`, weekly and on
dispatch. Each job with a local equivalent runs `scripts/check.sh` with its group:

- a `changes` job first: when a push or pull request touches only Markdown, `docs/` or workflow
  files, the Python, shell and Docker jobs are skipped. The docs job and the workflow linters always
  run, and the schedule and a dispatch run everything;
- ruff, basedpyright, import-linter (`lint-imports`) and the local-variable annotation rule
  (`local-annotations`), and the test suite with coverage;
- `pip-audit` on the hashed dependency locks, and GitHub's dependency review on pull requests, which
  fails on a new dependency with a high-severity advisory;
- shellcheck and shfmt on the shell scripts;
- markdownlint, prettier, a relative-link and fragment check (lychee, offline) and typos, plus
  `.github/scripts/docs_drift.py`: `.env.example` names every setting the app and compose read,
  every long doc's `## Contents` matches its headings, and no page on the site links into a path the
  site leaves out;
- a build and smoke test of the image and `docker compose config`, with hadolint and a Trivy scan of
  the image as **advisory** steps: a finding is a warning and a step summary (Trivy's also goes to
  the Security tab), never a failed job;
- gitleaks, actionlint (with `.github/scripts/lint_workflows.sh`'s structural rules) and zizmor.

**`CI result`** is the one required check: a roll-up that fails when any job above failed or was
cancelled, and passes when each passed or was skipped, so jobs can be renamed or skipped by the
change filter without touching branch protection.

Every job in every workflow starts with `step-security/harden-runner`, which records the runner's
network egress (and blocks all but an allowlist in `publish.yml`'s `publish` and `release` jobs);
`lint_workflows.sh` fails a job whose first step isn't it. The same script requires a
`timeout-minutes` on every job, top-level `permissions:`, `persist-credentials: false` on every
checkout, a success gate on every `workflow_run` job, and a commit SHA with a version comment on
every third-party action.

The other workflows:

- `release-note.yml`: the **`release note (pr body)`** check, on every non-draft pull request but
  Dependabot's, re-run when the body is edited. It never fails: a missing or blank `## Release note`
  section is a warning, and the job summary shows the note as the release page would list it
  ([Pull requests](#pull-requests)).
- `coverage.yml`: the test suite with coverage, measured once as data after every green CI run on a
  push to `main`: the `coverage-badge` artifact holds the README's coverage and tests-passed badge
  files, and an HTML report is uploaded beside it. On a same-repository pull request that changes
  more than Markdown or `docs/`, it also posts the pull request's figures next to `main`'s in one
  comment, edited in place on every push. The floor itself is enforced by `ci.yml`'s test job.
- `pages.yml`: builds the [project site](https://ivylikethevine.github.io/instadroid/) with Jekyll
  from the repository root (`_config.yml`), fetches the newest `coverage-badge` artifact from `main`
  and serves it as `badges/coverage.json` and `badges/tests.json`, so no coverage service, token or
  badge branch is involved. Runs when Coverage finishes on `main`, on a push to `main` that changes
  the site's sources, and on dispatch.
- `cancel-closed-pr.yml`: when a same-repository pull request is merged or closed, cancels its runs
  still queued or in progress, so they don't hold runners after the merge.
- `codeql.yml`: CodeQL for Python and GitHub Actions, on pushes to `main`, pull requests and weekly on
  Tuesday.
- `advisories.yml`: `pip-audit` over both Python locks and `npm audit` over
  `.github/package-lock.json`, after every green CI run on a push to `main` and weekly. Advisory: a
  finding opens or refreshes one `advisories` tracking issue, which the next clean run closes.
- `image-scan.yml`: two advisory Trivy jobs, after every green CI run on a push to `main` and weekly.
  `pins` scans the digest-pinned images (the Dockerfile's base, and compose's redroid and FreshRSS)
  and what their tags point at now, and keeps a tracking issue open only while a repin would close
  fixable HIGH or CRITICAL findings; `trivy` scans the published image's `latest`, which goes stale
  between releases, with findings in the Security tab and a warning.
- `link-check.yml`: external links in the docs (lychee), after every green CI run on a push to
  `main` and weekly. Advisory: findings go to one tracking issue, so a site that's briefly down can't
  block a merge.
- `tool-versions.yml`: drift check over the pins Dependabot can't see (the CI tools in
  `.github/actions/setup-tool/tools.txt`, `apkeep`, the site theme) and a second look at the ones it
  can, after every green CI run on a push to `main` and weekly, reported in one tracking issue.
- `scorecard.yml`: OpenSSF Scorecard, after every green CI run on a push to `main`, weekly, and when
  a branch protection rule changes ([OPENSSF-IMPROVEMENTS.md](OPENSSF-IMPROVEMENTS.md)).
- `new-builds.yml`: weekly, keeps one issue open while APKPure lists an Instagram major version newer
  than every validated build, and closes it once nothing newer is listed.
- `publish.yml`: on a `vX.Y.Z` tag, the release ([RELEASING.md](RELEASING.md)).

Dependabot watches pip (both locks), npm (the Markdown tools' lock), the Dockerfile's base image,
compose's images and GitHub Actions (the workflows and the composite actions), in one weekly pull
request into `dev`, with a 7-day cooldown.

## Which docs change with what

Every fact has one home, mapped in [docs/README.md](README.md); update that home and link to it
rather than restating it elsewhere.

| You changed                                                           | Update                                                                                                                                             |
| --------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| a setting in compose or the app's environment                         | its commented default in `.env.example` (**enforced** by `docs_drift.py`), and [OPERATIONS.md](OPERATIONS.md) when it changes day-to-day operation |
| a feed server route                                                   | `docs/openapi.json` via `export-openapi` (**enforced** by the test suite), and [FRESHRSS.md](FRESHRSS.md) if readers see it                        |
| a new Instagram build checked on a device                             | [COMPATIBILITY.md](COMPATIBILITY.md)'s table, [PROFILES.md](PROFILES.md)'s Status, and a [RUNLOG.md](RUNLOG.md) entry                              |
| a redroid image tried                                                 | [COMPATIBILITY.md](COMPATIBILITY.md), including "Weighed and not shipped" when it's declined                                                       |
| an incident on a real device                                          | an [INCIDENTS.md](INCIDENTS.md) write-up, and a short rule in [Running against a real device](#running-against-a-real-device) and `CLAUDE.md`      |
| what a scrape does, as a user sees it                                 | the README's "How a scrape works"                                                                                                                  |
| a CI job or workflow                                                  | [What CI runs](#what-ci-runs), and [TESTING.md](TESTING.md#what-ci-runs) if it runs tests                                                          |
| the test harness, fixtures or the coverage floor                      | [TESTING.md](TESTING.md)                                                                                                                           |
| the release process                                                   | [RELEASING.md](RELEASING.md)                                                                                                                       |
| anything security-relevant: auth, secrets, exposure, the supply chain | [SECURITY.md](SECURITY.md) (assurance case, what the project already does) and [ARCHITECTURE.md](ARCHITECTURE.md#trust-boundaries)                 |
| a component, an import boundary, or where state lives                 | [ARCHITECTURE.md](ARCHITECTURE.md)                                                                                                                 |
| planned work                                                          | [ROADMAP.md](ROADMAP.md)                                                                                                                           |
| a new document under `docs/`                                          | [docs/README.md](README.md)'s index                                                                                                                |
| a heading in a doc with a `## Contents`                               | that doc's `Contents` list (**enforced** by `docs_drift.py`)                                                                                       |
| anything a user of a release would notice                             | the pull request's `## Release note` section ([Pull requests](#pull-requests))                                                                     |

## Pull requests

- Branch from `dev` and open the pull request against `dev`; `main` is what releases are cut from.
- Keep each pull request to one change, and explain the why, not just the what.
- **All Python code must be 100% type annotated and at least 90% covered by tests**
  ([Typing and coverage](#typing-and-coverage)). New code comes with its tests, and CI fails below
  either bar.
- Update the docs your change affects in the same pull request, per
  [Which docs change with what](#which-docs-change-with-what), including new or changed environment
  variables in `docker-compose.yml` and `.env.example`.
- Don't commit `.env`, anything under `local/`, APKs, or real screenshots.
- The pull request template's `## Release note` section is optional: one or two sentences a user
  would read on the release page, or `none` when nothing a user sees changes. A release's notes are
  built from these sections ([RELEASING.md](RELEASING.md#what-the-tag-triggers)), so a pull request
  without one adds nothing there; the `release note (pr body)` check warns but doesn't fail.
- A `dev` → `main` pull request is squash-merged, so it's the only pull request `main`'s history
  shows, and the release lists its note alone: its `## Release note` section collects the notes of
  every pull request it brings, as plain sentences.

## Releases

Releases are cut by the maintainer by pushing a `vX.Y.Z` tag; [RELEASING.md](RELEASING.md) has the
pipeline and its gates, and why there's no CHANGELOG.

## AI-assisted contributions

AI tools are fine to use, and this project uses them itself (see [AI usage](../README.md#ai-usage) in
the README). You're responsible for what you submit either way: understand every line, test it, and
make sure it doesn't include anything you couldn't license under this project's
[license](../LICENSE.md).
