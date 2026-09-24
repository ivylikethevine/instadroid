# Documentation

Reference material that doesn't fit in [the README](../README.md)'s overview. **Each fact has one
home; every other page links to it rather than restating it.** This page is the map: when a change
needs a doc update, [CONTRIBUTING.md](CONTRIBUTING.md#which-docs-change-with-what) says which one.

## Using instadroid

| Doc                               | Covers                                                                                                               |
| --------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| [README](../README.md)            | What it does, host requirements, first-time setup, how a scrape works, and the settings worth knowing first.         |
| [Compatibility](COMPATIBILITY.md) | Which redroid image runs which Instagram build, why Android 13, and what was weighed and not shipped.                |
| [Operations](OPERATIONS.md)       | Health, `doctor`, restarts, manual lock and scrape-now, alerts, the selector-drift canary, memory, storage, backups. |
| [FreshRSS](FRESHRSS.md)           | Subscribing, feed auth, OPML, push refresh, and running FreshRSS on the same host.                                   |
| [Version profiles](PROFILES.md)   | How per-Instagram-version selector profiles work, and the steps for supporting a new build.                          |
| [Support](SUPPORT.md)             | Where to ask for help, what to include, and what to expect back.                                                     |
| [OpenAPI spec](openapi.json)      | The feed server's routes, generated from the code.                                                                   |

## Understanding it

| Doc                             | Covers                                                                                                            |
| ------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| [Architecture](ARCHITECTURE.md) | The component map, how a scrape reaches a feed reader, trust boundaries, where state lives, and designs declined. |
| [Security policy](SECURITY.md)  | The threat model, the assurance case, supported versions, and how to report a vulnerability.                      |
| [Incidents](INCIDENTS.md)       | Dated write-ups of redroid incidents on the maintainer's host: symptoms, root causes, recovery, and measurements. |
| [Run log](RUNLOG.md)            | Dated device runs behind the version profiles: baselines, validations and probes, with peak memory.               |

## Changing it

| Doc                                             | Covers                                                                                              |
| ----------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| [Contributing](CONTRIBUTING.md)                 | Development setup, the checks to run, what CI runs, which docs change with what, and pull requests. |
| [Testing](TESTING.md)                           | The test tiers, how to run each, fixtures, coverage, and which tiers CI runs.                       |
| [Releasing](RELEASING.md)                       | The tag scheme, the unattended publish pipeline and its gates, verifying an image, and prereleases. |
| [Governance](GOVERNANCE.md)                     | Decision-making, roles, how a change gets in, and continuity for a single-maintainer project.       |
| [Roadmap](ROADMAP.md)                           | Planned work, ordered by scope.                                                                     |
| [Code of conduct](CODE_OF_CONDUCT.md)           | Standards for participating in any project space, and how they're enforced.                         |
| [OpenSSF improvements](OPENSSF-IMPROVEMENTS.md) | Where the Scorecard score is capped, and the Best Practices answer sheet with evidence.             |
