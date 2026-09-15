---
title: Contributing
---

# Contributing

Thanks for looking at instadroid. It's experimental until v1.0.0, so expect things to move. Bug
reports, selector fixes for a new Instagram version, and focused pull requests are all welcome.

Please read the [code of conduct](CODE_OF_CONDUCT.md) first. For a security problem, don't open an
issue: follow [SECURITY.md](SECURITY.md).

## Reporting a bug

Include:

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
pip install --require-hashes -r requirements-dev.txt   # locked dev tools and app requirements
pip install --no-deps -e .                              # app/ on the path, and the dev commands
```

Dependencies are locked with hashes. Edit `app/requirements.in` or `requirements-dev.in`, not the
`.txt` locks, then regenerate the locks with pip-tools, app first (the dev lock is constrained to
it), using the command in each lock's header:

```bash
(cd app && pip-compile --allow-unsafe --generate-hashes --strip-extras requirements.in)
pip-compile --allow-unsafe --generate-hashes --strip-extras requirements-dev.in
```

Before sending a change, run what CI runs:

```bash
ruff check . && ruff format --check .      # also formats Python code blocks in Markdown
basedpyright
lint-imports                               # import boundaries (pyproject.toml)
pytest -q --cov                           # fails under 90% coverage
shellcheck -S warning scripts/*.sh scripts/ci/*.sh app/entrypoint.sh && shfmt -d scripts/ app/entrypoint.sh
typos                                     # spelling, everywhere ([tool.typos] in pyproject.toml)
git ls-files -z '*.md' | xargs -0 npx --yes markdownlint-cli2@0.23.2
git ls-files -z '*.md' | xargs -0 npx --yes prettier@3.9.6 --check
git ls-files -z '*.md' | xargs -0 lychee --offline --include-fragments   # relative links
docker compose config -q
```

Most device-driving code is tested against `tests/fakedevice.py`, a scripted stand-in for a
device, so the suite needs no emulator and no Instagram account. New behavior should come with tests
in `tests/`.

The scraper lives in `app/instadroid/` (the package docstring lists the modules); `app/scraper.py` is
only the command line. The feed server is `app/feedserver/`, `app/shared/` is the leaf both of them
import, and `app/devtools/` holds the development commands, which aren't shipped in the image. The
README's Development section has the full layout. Modules call each other as `device.human_pause(...)` and read settings as
`config.NAME`, never `from .device import human_pause`, so a test's `monkeypatch.setattr(device,
"human_pause", ...)` reaches every caller.

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
[CLAUDE.md](../CLAUDE.md) (the rules) and [INCIDENTS.md](INCIDENTS.md) (the write-ups behind them)
before doing that on your own host. They cover incidents that cost real time, including a kernel
panic, `/data` corruption from mixing Android versions, and a whole-host freeze from a scrape that ran
out of memory. In particular:

- Keep scrape test runs short (`MAX_SCROLLS=5`, `MAX_STORIES_PER_RUN=2`) and check `docker stats`
  headroom first.
- Never point two different Android major-version redroid images at the same `/data` volume.
- Never publish ADB (port 5555) or an unauthenticated feed beyond localhost.

## Pull requests

- Branch from `dev` and open the pull request against `dev`; `main` is what releases are cut from.
- Keep each pull request to one change, and explain the why, not just the what.
- **All Python code must be 100% type annotated and at least 90% covered by tests** (see the rule in
  the README's Development section). New code comes with its tests, and CI fails below either bar.
- Update the docs your change affects (`README.md`, `.env.example`, `docs/`) in the same pull
  request, including new or changed environment variables in `docker-compose.yml` and `.env.example`.
- Don't commit `.env`, anything under `local/`, APKs, or real screenshots.

Releases are cut by the maintainer by pushing a `vX.Y.Z` tag (see "Releases" in the README).

## AI-assisted contributions

AI tools are fine to use, and this project uses them itself (see "AI Usage" in the README). You're
responsible for what you submit either way: understand every line, test it, and make sure it
doesn't include anything you couldn't license under this project's [license](../LICENSE.md).
