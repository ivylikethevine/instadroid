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
pip install -r scripts/requirements-dev.txt -r app/requirements.txt
```

Before sending a change, run what CI runs:

```bash
ruff check . && ruff format --check .      # also formats Python code blocks in Markdown
PYTHONPATH=app pytest app/tests -q
shellcheck -S warning scripts/*.sh
docker compose config -q
```

Most device-driving code is tested against `app/tests/fakedevice.py`, a scripted stand-in for a
device, so the suite needs no emulator and no Instagram account. New behavior should come with tests
there.

## Supporting an Instagram version

Everything specific to one Instagram major version lives in its own directory,
`app/igprofiles/vXYZ/`: selectors, the APK build to install, behavior overrides, and test fixtures.
The oldest supported version is 440. [NEXT.md](NEXT.md) describes the design and walks through adding
a version step by step. The short version:

- **Change the version's own directory, not shared code.** If 446 renamed a resource-id, override that
  key in `v446/selectors.py`. If it changed behavior, override the `@versioned` function as a method
  on `v446`'s `Profile`. Don't add `if version == ...` checks to `scraper.py`.
- **Keep older profiles passing.** The whole existing test suite runs against `v445`, and
  `test_every_profile_meets_the_contract` checks every profile directory automatically.
- **Fixtures must be synthetic or scrubbed.** A dump from a real feed goes into `vXYZ/fixtures/` only
  with usernames, captions, places and any other personal details replaced.

## Running against a real device

Parts of this project drive a real redroid container and a real Instagram account. Read
[CLAUDE.md](../CLAUDE.md) before doing that on your own host. It documents incidents that cost real
time, including a kernel panic, `/data` corruption from mixing Android versions, and a whole-host
freeze from a scrape that ran out of memory. In particular:

- Keep scrape test runs short (`MAX_SCROLLS=5`, `MAX_STORIES_PER_RUN=2`) and check `docker stats`
  headroom first.
- Never point two different Android major-version redroid images at the same `/data` volume.
- Never publish ADB (port 5555) or an unauthenticated feed beyond localhost.

## Pull requests

- Branch from `dev` and open the pull request against `dev`; `main` is what releases are cut from.
- Keep each pull request to one change, and explain the why, not just the what.
- Update the docs your change affects (`README.md`, `.env.example`, `docs/`) in the same pull
  request, including new or changed environment variables in `docker-compose.yml` and `.env.example`.
- Don't commit `.env`, anything under `local/`, APKs, or real screenshots.

Releases are cut by the maintainer by pushing a `vX.Y.Z` tag (see "Releases" in the README).

## AI-assisted contributions

AI tools are fine to use, and this project uses them itself (see "AI Usage" in the README). You're
responsible for what you submit either way: understand every line, test it, and make sure it
doesn't include anything you couldn't license under this project's [license](../LICENSE.md).
