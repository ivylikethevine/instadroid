# Testing

The test tiers, how to run each, the fixtures they use, and which of them CI runs.
[CONTRIBUTING.md](CONTRIBUTING.md#development-setup) has the setup and the full list of checks to run
before a pull request; this page is the reference behind the tests themselves.

## Contents

- [Tier 1: unit tests and coverage](#tier-1-unit-tests-and-coverage)
- [Tier 2: static contracts](#tier-2-static-contracts)
- [Tier 3: fixture replay](#tier-3-fixture-replay)
- [Tier 4: real-device runs](#tier-4-real-device-runs)
- [Fixtures](#fixtures)
- [What CI runs](#what-ci-runs)
- [Fuzzing](#fuzzing)

## Tier 1: unit tests and coverage

```bash
scripts/check.sh test                        # what CI runs: the suite with coverage, against the floor
scripts/check.sh test -- -k replay           # the same, passing arguments on to pytest
mkdir -p local                               # by hand: coverage keeps its data file there
pytest -q                                    # parser, feed, and device-flow tests; temp SQLite db
pytest -q --cov --cov-report=term-missing    # with coverage; fails under the floor
```

`scripts/check.sh`'s groups mirror CI's jobs: `test` and `python` together cover this page's tiers 1 to
3 (`--fast` runs both), and [CONTRIBUTING.md](CONTRIBUTING.md#development-setup) lists the rest.

Hermetic: no emulator, no Instagram account, no network. The device-driving code (login, feed
navigation, share sheet, carousels, stories, the scrape loop) runs against `tests/fakedevice.py`, the
feed server against FastAPI's test client (`tests/feedclient.py`), and every database is a temporary
SQLite file.

**The coverage floor is 95%**, `fail_under` in `pyproject.toml`'s `[tool.coverage.report]`, measured
over `app/`. pytest-cov enforces it, so a run under the floor fails. The README's coverage badge is
the figure from the latest green push to `main`, measured by `.github/workflows/coverage.yml` and
served by `pages.yml`; the badge turns bright green at that same `fail_under`.

`tests/conftest.py` holds the autouse fixtures every test gets:

- `profile_v424` pins the root profile, `v424`, so the suite as a whole is the `v424` regression
  suite unless a test selects another;
- `no_real_boot_wait` and `no_real_logcat` stand in for the calls that run the real `adb` binary (the
  boot wait, device tuning, and `diagnostics.save_failure_logcat`), which on a developer host could
  reach a live redroid;
- `no_profile_capture` keeps capture mode off whatever the environment says;
- `backups_in_tmp` and `control_files_in_tmp` put database backups and the poll loop's control files
  in throwaway directories, and `no_alert_delivery` blanks `ALERT_URL`;
- `close_databases` closes every connection `db_init()` opened at the test's teardown, rather than
  leaving it to garbage collection and a `ResourceWarning`;
- `no_permalink_backfill` turns the permalink backfill off; its own tests turn it back on.

Two more are opt-in: `fast_offline`, the device-flow setup (paths under `tmp_path`, no pauses, test
credentials), and `con`, a fresh database.

## Tier 2: static contracts

```bash
scripts/check.sh python    # all four, as CI's lint job runs them
ruff check . && ruff format --check .
basedpyright
lint-imports
constricter app tests .github/scripts
```

These run over `app/` and `tests/` alike and are part of the test contract, not just style:

- **Typing.** basedpyright strict with `reportAny`, plus ruff's `ANN` rules, so no value typed `Any`
  gets through, and [constricter](https://github.com/ivylikethevine/python-constricter) at its
  strictest level for the rule that every variable is annotated, which ruff doesn't have.
  `tests/test_typing_policy.py` runs constricter too
  and catches the suppression comments the linters can't forbid on their own. The rules are in
  [CONTRIBUTING.md](CONTRIBUTING.md#typing-and-coverage).
- **Import boundaries.** import-linter's contracts in `pyproject.toml`: the feed server doesn't import
  the scraper, the scraper doesn't import the feed server, `shared` imports neither, profiles don't
  import the scraper code they configure, and `instadroid.parsing` stays pure (no device, network or
  database code), which is what the replay tests rely on. See
  [ARCHITECTURE.md](ARCHITECTURE.md#component-map).
- **Profiles.** `tests/test_profiles.py` checks every profile directory automatically:
  `test_every_profile_meets_the_contract` requires every selector key the root profile has, no
  override that matches no `@versioned` function, and every validated build to be one its profile
  covers, and every profile but the lowest must differ from its parent.
- **The OpenAPI spec.** `tests/test_scripts_cli.py` runs `export-openapi --check`, so the suite fails
  until `docs/openapi.json` is regenerated after a route change.

## Tier 3: fixture replay

```bash
pytest -q tests/test_replay.py
promote-dump --update v424    # after an intentional parser or selector change: re-record expectations
```

`tests/test_replay.py` parses every recorded screen under `app/igprofiles/<profile>/fixtures/` with
that profile and compares the result with its `.expected.json`, and checks each still has its
screen's required selector keys (`app/igprofiles/screens.py`). A fixture gets there only through
`promote-dump` (below). This is how an older Instagram version keeps passing after a change made for
a newer one.

## Tier 4: real-device runs

Never run by CI, and never unattended. A real redroid container and a real Instagram account are
needed, so read [CONTRIBUTING.md](CONTRIBUTING.md#running-against-a-real-device) first; agents also
follow [CLAUDE.md](https://github.com/ivylikethevine/instadroid/blob/main/CLAUDE.md#hard-constraints)
and ask before starting one.

- **A manual scrape**, kept short: `MAX_SCROLLS=5 MAX_STORIES_PER_RUN=2` with
  `docker compose exec app python scraper.py once`, after checking `docker stats` headroom and
  force-stopping Instagram.
- **A new Instagram build: the `new-profile` flow.** `new-profile baseline <build>` installs the build
  and does one capped capture-mode run on a scratch database, refusing to start while the `app`
  service runs or memory headroom is short. `check` lists the selector keys each captured screen is
  missing, `fork` creates a profile only when something drifted, `promote` scrubs the captures into
  fixtures, `validate` records the build as validated, and `restore` reinstalls the default build.
  [PROFILES.md](PROFILES.md#adding-a-version) walks through every step.

Every dated device run, with its caps, result and peak memory, goes in [RUNLOG.md](RUNLOG.md), and a
new image and build pair in [COMPATIBILITY.md](COMPATIBILITY.md).

## Fixtures

No real account data is used in any fixture.

- **Synthetic screens.** `tests/fakedevice.py` is a scripted stand-in for a uiautomator2 device whose
  screens are synthetic hierarchy XML; `goto` and `clip` attributes on a node script what a tap does.
  `tests/deviceflows.py` wires it up as a Home → Following feed with seeded posts.
- **Scrubbed dumps.** `app/igprofiles/<profile>/fixtures/<screen>_<major>.xml`, one set per validated
  version, promoted from a real dump only through `promote-dump`, which replaces the usernames,
  names, places and captions it can identify and prints the leftover text to read before committing.
  [PROFILES.md](PROFILES.md#leak-scan-2026-09-14) records what a leak scan found.
- **Typed helpers.** `tests/support.py` has typed reads of JSON bodies and SQLite rows, database
  seeding, and a fake `urlopen()` response.
- Images, APKs and databases are never committed (`.gitignore`).

## What CI runs

On every non-draft pull request and every push to `main`, `ci.yml` runs tiers 1 to 3: the `test` job
runs the whole suite with coverage (replay and contract tests included), and the `lint` job runs
ruff, basedpyright, `lint-imports` and constricter, each through `scripts/check.sh`. Both are skipped when a
change touches only Markdown, `docs/` or workflow files. `coverage.yml` runs the suite with coverage
again after each green CI run on a push to `main`, for the badge, and on a same-repository pull
request, where it comments the pull request's coverage and test count next to `main`'s. The docs job
runs `.github/scripts/docs_drift.py`, which checks `.env.example` against the settings the code reads.
Tier 4 never runs in CI. Everything else CI checks (dependencies, shell,
docs, the image, workflows) is in [CONTRIBUTING.md](CONTRIBUTING.md#what-ci-runs).

## Fuzzing

None exists. The one input from outside the project that gets parsed is the hierarchy XML a device
returns, in `instadroid.parsing.parse_hierarchy()`, which is pure by contract (above) and so a
candidate for a Python fuzzer such as atheris. Captions reach the feed through
`feedserver.render.caption_html()`, which escapes them. See
[OPENSSF-IMPROVEMENTS.md](OPENSSF-IMPROVEMENTS.md).
