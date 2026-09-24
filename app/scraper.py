"""Command-line entry point. `python scraper.py` runs the poll loop; the subcommands are one-offs:

    once                     one scrape run (recorded in the runs table like a scheduled one)
    login                    log in (or confirm the session is live), then stop Instagram
    profiles                 list the Instagram version profiles, what each covers and has validated
    install [VERSION|latest] install an Instagram build (default: igprofiles.DEFAULT_BUILD)
    dump                     save the current screen's hierarchy + screenshot to DEBUG_DIR
    doctor                   one report: control state, recent runs, device over plain adb, logcat
                             crash signatures with their fixes (never drives the device)
    compat                   redroid image / Instagram build pairs this database has run
    backup                   copy the database to BACKUP_DIR now
    lock / unlock            hold scheduled runs while driving the device by hand, then release
                             (unlock also clears the hold a login challenge raised)
    scrape-now               ask the poll loop to run now (rate-limited, see instadroid/control.py)
    rename OLD NEW           move an account's history to its new username

The scraper itself lives in the instadroid/ package.
"""

import sqlite3
import sys
from contextlib import closing

from igprofiles import BaseProfile, default_build, version_key
from igprofiles import available as available_profiles
from igprofiles import load as load_profile
from instadroid import (
    backup,
    config,
    control,
    db,
    device,
    diagnostics,
    doctor,
    install,
    navigation,
    scrape,
    uidevice,
    versioning,
)
from instadroid.db import VersionPair
from instadroid.scrape import RunStats

if __name__ == "__main__":
    version: list[str]
    action: str
    old: str
    new: str
    match sys.argv[1:]:
        case ["once", *_]:
            con: sqlite3.Connection
            stats: RunStats | None
            exc: Exception | None
            with closing(db.db_init()) as con:
                wait: float
                if wait := scrape.budget_wait_seconds(con):
                    sys.exit(
                        f"{config.MAX_RUNS_PER_DAY} runs already started in the last 24h (MAX_RUNS_PER_DAY);"
                        f" the next is allowed in {wait / 3600:.1f}h. MAX_RUNS_PER_DAY=0 disables the budget."
                    )
                stats, exc = scrape.run_recorded(con)  # recorded in runs, like a scheduled run
            if exc or stats is None:
                sys.exit(f"run failed: {exc!r}")
            print(stats["new"], "new posts,", stats["metrics"].get("new_stories", 0), "new stories")
        case ["login", *_]:
            d: uidevice.Device = device.connect_device()
            try:
                navigation.ensure_logged_in(d)
                print("logged in")
            finally:
                device.force_stop(d, config.IG_PKG)  # don't leave ~800MiB resident for whatever runs next
        case ["profiles", *_]:
            names: list[str] = available_profiles()
            name: str
            following: str | None
            for name, following in zip(names, [*names[1:], None], strict=True):
                p: BaseProfile = load_profile(name)
                covers: str = f"{p.major}-{int(following[1:]) - 1}" if following else f"{p.major} and newer"
                active: str = " (active)" if p.name == versioning.PROFILE.name else ""
                validated: str = ", ".join(sorted(p.own_validated, key=version_key)) or "none yet"
                print(f"{p.name}{active}  covers Instagram {covers}  {p.notes}\n  validated: {validated}")
            print("default install:", default_build() or "latest")
        case ["install", *version] if len(version) <= 1:
            d = device.connect_device()
            print("installed:", install.install_instagram_version(d, version[0] if version else None))
        case ["install", *_]:
            print("usage: scraper.py install [VERSION|latest]   (default: igprofiles.DEFAULT_BUILD)")
            sys.exit(1)
        case ["dump", *_]:
            d = device.connect_device()
            diagnostics.dump_debug(d, "manual")
            print("wrote", config.DEBUG_DIR)
        case ["doctor", *_]:
            with closing(db.db_init()) as con:
                print(doctor.report(con), end="")
        case ["compat", *_]:
            pairs: list[VersionPair]
            with closing(db.db_init()) as con:
                pairs = db.version_pairs(con)
            print("redroid image | Instagram | profile | runs (ok, clean) | new posts | last run")
            pair: VersionPair
            for pair in pairs:
                print(
                    f"{pair['redroid_image'] or '?'} | {pair['ig_version']}"
                    f" | {pair['selector_profile'] or '?'} | {pair['runs']}"
                    f" ({pair['ok_runs']}, {pair['clean_runs']}) | {pair['new_posts']}"
                    f" | {pair['last_run'][:10]}"
                )
            if not pairs:
                print("(no runs that reached the device yet)")
        case ["lock" | "unlock" as action, *_]:
            control.set_lock(action == "lock")
            print(f"{action}ed:", control.locked())
        case ["scrape-now", *_]:
            control.request_run_now()
            print(
                "requested; the poll loop starts a run within",
                int(config.CONTROL_POLL_SECONDS),
                "seconds if due",
            )
        case ["backup", *_]:
            with closing(db.db_init()) as con:
                print("wrote", backup.backup_database(con, force=True))
        case ["rename", old, new]:
            n: int
            with closing(db.db_init()) as con:
                n = db.rename_account(con, old, new)
            print(f"moved {n} post(s) from {old!r} to {new!r}")
        case ["rename", *_]:
            print("usage: scraper.py rename <old_username> <new_username>")
            sys.exit(1)
        case _:
            scrape.main()
