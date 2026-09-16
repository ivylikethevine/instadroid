# Support

Where to ask for help with instadroid, what to include so the question can be answered, and what to
expect back.

## Where to ask

Open an issue: <https://github.com/ivylikethevine/instadroid/issues/new/choose>. The bug report
and feature request forms ask for what's needed. A question that fits neither can go in a blank
issue.

**A security problem never goes in an issue.** Report it privately, as described in
[SECURITY.md](SECURITY.md#reporting-a-vulnerability).

## What to include

For anything that goes wrong on the device (a slow or looping boot, adb stuck `offline`, Instagram
pushed back to the launcher, a run that parses nothing):

- the output of `docker compose exec app python scraper.py doctor`: the control state (a lock or a
  hold), the last runs, the device as plain adb sees it, and the device's logcat checked for the
  crash patterns this project has already hit, with the matching fix when one is known. It never
  drives the device, so it's safe mid-run. `./scripts/diagnose.sh`, run from the repository root on
  the host, does the logcat part too and also reads the host's `dmesg`, which is the only place an
  Android that crashed before adb came up leaves a trace;
- the relevant lines of `docker compose logs app`;
- the instadroid release or commit, the Instagram build and profile, and the redroid image (all but
  the first are shown on `/status`);
- the host OS and kernel (`uname -r`).

Scrub account content first: logs, debug dumps and screenshots can contain real usernames, captions
and photos. Never paste `.env`, `FRESHRSS_REFRESH_URL` or anything from `local/data/android`.
[CONTRIBUTING.md](CONTRIBUTING.md#reporting-a-bug) has more on debug dumps.

## Unsupported setups

Instagram only runs on a redroid image with working ARM translation, and only some image and host
combinations work. Check [COMPATIBILITY.md](COMPATIBILITY.md) before reporting a problem with a
different image or Android version: it may already be recorded as not working, with the reason.
A pair it doesn't list is still worth reporting, with the details above.

## What to expect

instadroid has one maintainer ([GOVERNANCE.md](GOVERNANCE.md)) and is maintained on a best-effort
basis, so there's no guaranteed response time. An issue with the details above is much quicker to act
on than one that needs a round of follow-up questions first.
