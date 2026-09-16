# Next steps

A dated plan from the 2026-09-15 review of the codebase, ordered by what protects the account and
the host first. Each item says what changes, where, and how it's verified. Items move to "Done" as
they land; the longer-term ideas stay in [docs/ROADMAP.md](docs/ROADMAP.md).

## Contents

- [Principles](#principles)
- [Phase 1: stop the failure spirals](#phase-1-stop-the-failure-spirals)
- [Phase 2: quality of what's captured](#phase-2-quality-of-whats-captured)
- [Phase 3: ease of use](#phase-3-ease-of-use)
- [Deliberately not doing](#deliberately-not-doing)
- [Done](#done)

## Principles

- Every Instagram-facing action has a ceiling that holds no matter which code path reaches it.
- A failure that needs a person stops the scraper until a person acts; no timer resumes it.
- Nothing ever restarts redroid automatically. Health checks report, they don't act.
- Stay in Python and Docker. The one non-Python idea worth anything (an on-device Kotlin UiAutomator
  agent) waits for a month of run data showing the adb round trips are the bottleneck.

## Phase 1: stop the failure spirals

1. **Hold after a "needs a human" error.** A challenge, an unrecognised login form, a wrong
   password or missing credentials writes `needs-human.hold` in `CONTROL_DIR` with the error as its
   text. The poll loop, `scrape-now` and `/control/scrape-now` treat it like the manual lock, but it
   never expires; `scraper.py unlock` and `DELETE /control/lock` clear it. Without this the
   scraper retyped the password every poll interval, 6 to 9 times a day.
   Files: `app/shared/control.py`, `app/instadroid/control.py`, `app/instadroid/scrape.py`,
   `app/instadroid/alerts.py`, `app/feedserver/control.py`, `app/feedserver/status.py`.
   Verify: unit tests for the hold, and the loop test that shows the second run never connects.
2. **Back off after repeated non-transient failures.** `next_sleep_seconds()` doubles the poll
   interval per consecutive failed run (any kind), capped at `FAILURE_BACKOFF_MAX_HOURS` (default
   24), and resets on success. A broken selector set then costs one launch a day, not eight.
3. **A daily launch budget.** `MAX_RUNS_PER_DAY` (default 10): before connecting, count runs
   started in the last 24h; at the ceiling, skip and sleep until the oldest one ages out. Counts every
   path: schedule, retries, `scrape-now`, `once`, restarts.
4. **A per-run time budget.** `RUN_MAX_MINUTES` (default 30), checked wherever the memory guard is
   checked; past it the run stops with a warning, same shape as the memory guard.
5. **Never expire a lock by default.** `LOCK_MAX_HOURS` default 6 → 0. A person mid-challenge who
   steps away must not have the scraper resume under them.
6. **Confirm Instagram is really missing before auto-installing.** `ensure_logged_in()` checks
   `pm path` as well as `app_list()` before it downloads anything.
7. **redroid healthcheck for visibility only.** A compose healthcheck on `sys.boot_completed`, so a
   boot-looping Android shows in `docker ps`. No autoheal, ever.

## Phase 2: quality of what's captured

1. **Stable post identity across truncation.** `_post_key()` hashes the caption's first 200
   characters, so a card seen truncated and later expanded hashes differently and is processed
   twice. Key on a shorter prefix with the "…" stripped, plus the media description, and add a
   replay fixture pair (truncated and expanded) that must hash the same.
2. **Root-cause the empty clipboard.** Copy link left the clipboard empty 6 of 8 times. Android 10+
   denies clipboard reads to unfocused apps, and uiautomator2 reads from its background
   instrumentation. Experiments, on a locked device: read the clip with `dumpsys clipboard` over
   root adb; or activate uiautomator2's IME for the read. If either is reliable, the retry and
   backfill knobs can shrink.
3. **Dump the card when its media node isn't found** (already in ROADMAP.md).

## Phase 3: ease of use

1. **Fold the boot wait and `tune-android.sh` into the app's first connect**, idempotently, so
   first-time setup is `cp .env.example .env`, `docker compose up -d`, `scraper.py login`.
2. **`scraper.py doctor`**: the `scripts/diagnose.sh` triage plus control state, headroom and the
   last run, in one command a bug report can paste.
3. **Split `.env.example`** into "the five you set" and an advanced section.
4. **Lock/unlock/scrape-now buttons on `/status`**, since the endpoints already exist.
5. **Freeze CI expansion until v1.0.** Thirteen workflows are enough; every new pin is upkeep.

## Deliberately not doing

- Rewriting the driver in another language, or moving to a private-API client (higher ban risk,
  and it defeats the point of driving the real app).
- Raising `REDROID_WIDTH`: Instagram serves feed images at display width, so 1080 is already native.
- Any automatic restart of redroid or of the app on "unhealthy".

## Done

- 2026-09-15: Phase 1 item 1, the needs-a-human hold.
- 2026-09-15: Phase 1 item 2, the failure backoff (`FAILURE_BACKOFF_MAX_HOURS`).
- 2026-09-15: Phase 1 item 3, the daily launch budget (`MAX_RUNS_PER_DAY`).
- 2026-09-15: Phase 1 item 4, the per-run time budget (`RUN_MAX_MINUTES`).
- 2026-09-15: Phase 1 item 5, `LOCK_MAX_HOURS` defaults to 0.
- 2026-09-15: Phase 1 item 6, `pm path` confirms Instagram is missing before an auto-install.
- 2026-09-15: Phase 1 item 7, a visibility-only redroid healthcheck (not yet seen against a running container).
