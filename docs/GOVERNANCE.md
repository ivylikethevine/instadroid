# Governance

Who decides, who holds which role, and what happens to the project if the one person running it
disappears. The [README](../README.md) covers using
instadroid and [CONTRIBUTING.md](CONTRIBUTING.md) covers sending a change.

## Decision-making model

instadroid is a **single-maintainer project**. There's no steering committee, no vote, and no second
person to appeal a decision to. [`.github/CODEOWNERS`](https://github.com/ivylikethevine/instadroid/blob/main/.github/CODEOWNERS)
names one owner for the whole tree because there's nobody yet to split ownership with.

Decisions are still made in public: planned work is in the [roadmap](ROADMAP.md), the dated device
runs behind each version profile are in the [run log](RUNLOG.md), and the incidents behind the
host-safety rules are in [INCIDENTS.md](INCIDENTS.md).

## Roles

| Role        | Who                                                                            | What they can do                                                                                                               |
| ----------- | ------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------ |
| Maintainer  | [@ivylikethevine](https://github.com/ivylikethevine), per `.github/CODEOWNERS` | Reviews and merges pull requests, cuts releases, holds repository admin access, sets direction, triages security reports.      |
| Contributor | Anyone who opens a pull request                                                | Proposes a change. No merge rights; the maintainer reviews every pull request before it lands.                                 |
| Reporter    | Anyone who opens an issue or a private security advisory                       | Raises a bug, a feature request or a vulnerability. [SUPPORT.md](SUPPORT.md) and [SECURITY.md](SECURITY.md) say which channel. |

**Becoming a maintainer**: nobody has yet, so there's no process to describe. If sustained
contribution ever makes it worth having one, it will be decided and written down here.

## How a change gets in

1. A feature branch starts from `dev`, and its pull request targets `dev`.
2. CI has to pass (the checks are listed in [CONTRIBUTING.md](CONTRIBUTING.md#what-ci-runs)).
3. The maintainer reviews it and squash-merges it into `dev`, so each pull request lands as one
   commit.
4. When `dev` is ready to release, a pull request from `dev` to `main` is squash-merged the same way.
5. The maintainer pushes a `vX.Y.Z` tag on `main`, which publishes the release
   ([RELEASING.md](RELEASING.md)).

## Continuity

All access sits with the one maintainer account: repository admin, the protected release tags, and
the container packages on GHCR. Every account with write access has to use two-factor
authentication.

instadroid holds no user data of its own: no hosted service, no telemetry, and nothing collected
from anyone's instance. Each operator's Instagram session, database and feed live on their own
host, so losing the maintainer exposes nobody.

The project is MIT-licensed ([LICENSE.md](../LICENSE.md)),
with its source, history and CI configuration public, so anyone willing to pick it up can fork it
without asking. That's the continuity plan, rather than a promise of a second maintainer the project
doesn't have.
