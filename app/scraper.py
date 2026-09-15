"""Command-line entry point. `python scraper.py` runs the poll loop; the subcommands are one-offs:

    once                     one scrape run (recorded in the runs table like a scheduled one)
    login                    log in (or confirm the session is live), then stop Instagram
    profiles                 list the Instagram version profiles, what each covers and has validated
    install [VERSION|latest] install an Instagram build (default: igprofiles.DEFAULT_BUILD)
    dump                     save the current screen's hierarchy + screenshot to DEBUG_DIR
    compat                   redroid image / Instagram build pairs this database has run
    backup                   copy the database to BACKUP_DIR now
    lock / unlock            hold scheduled runs while driving the device by hand, then release
    scrape-now               ask the poll loop to run now (rate-limited, see instadroid/control.py)
    rename OLD NEW           move an account's history to its new username

The scraper itself lives in the instadroid/ package.
"""

import sys

from igprofiles import available as available_profiles
from igprofiles import default_build, version_key
from igprofiles import load as load_profile
from instadroid import (
    backup,
    config,
    control,
    db,
    device,
    diagnostics,
    install,
    navigation,
    scrape,
    versioning,
)

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "once":
        stats, exc = scrape.run_recorded(db.db_init())  # recorded in runs, like a scheduled run
        if exc or stats is None:
            sys.exit(f"run failed: {exc!r}")
        print(stats["new"], "new posts,", stats["metrics"].get("new_stories", 0), "new stories")
    elif len(sys.argv) > 1 and sys.argv[1] == "login":
        d = device.connect_device()
        try:
            print("logged in:", navigation.ensure_logged_in(d))
        finally:
            device.force_stop(d, config.IG_PKG)  # don't leave ~800MiB resident for whatever runs next
    elif len(sys.argv) > 1 and sys.argv[1] == "profiles":
        names = available_profiles()
        for name, following in zip(names, [*names[1:], None], strict=True):
            p = load_profile(name)
            covers = f"{p.major}-{int(following[1:]) - 1}" if following else f"{p.major} and newer"
            active = " (active)" if p.name == versioning.PROFILE.name else ""
            validated = ", ".join(sorted(p.own_validated, key=version_key)) or "none yet"
            print(f"{p.name}{active}  covers Instagram {covers}  {p.notes}\n  validated: {validated}")
        print("default install:", default_build() or "latest")
    elif len(sys.argv) > 1 and sys.argv[1] == "install":
        if len(sys.argv) > 3:
            print("usage: scraper.py install [VERSION|latest]   (default: igprofiles.DEFAULT_BUILD)")
            sys.exit(1)
        d = device.connect_device()
        print("installed:", install.install_instagram_version(d, sys.argv[2] if len(sys.argv) == 3 else None))
    elif len(sys.argv) > 1 and sys.argv[1] == "dump":
        d = device.connect_device()
        diagnostics.dump_debug(d, "manual")
        print("wrote", config.DEBUG_DIR)
    elif len(sys.argv) > 1 and sys.argv[1] == "compat":
        pairs = db.version_pairs(db.db_init())
        print("redroid image | Instagram | profile | runs (ok, clean) | new posts | last run")
        for p in pairs:
            print(
                f"{p['redroid_image'] or '?'} | {p['ig_version']} | {p['selector_profile'] or '?'}"
                f" | {p['runs']} ({p['ok_runs']}, {p['clean_runs']}) | {p['new_posts']} | {p['last_run'][:10]}"
            )
        if not pairs:
            print("(no runs that reached the device yet)")
    elif len(sys.argv) > 1 and sys.argv[1] in ("lock", "unlock"):
        control.set_lock(sys.argv[1] == "lock")
        print(f"{sys.argv[1]}ed:", control.locked())
    elif len(sys.argv) > 1 and sys.argv[1] == "scrape-now":
        control.request_run_now()
        print(
            "requested; the poll loop starts a run within", int(config.CONTROL_POLL_SECONDS), "seconds if due"
        )
    elif len(sys.argv) > 1 and sys.argv[1] == "backup":
        print("wrote", backup.backup_database(db.db_init(), force=True))
    elif len(sys.argv) > 1 and sys.argv[1] == "rename":
        if len(sys.argv) != 4:
            print("usage: scraper.py rename <old_username> <new_username>")
            sys.exit(1)
        n = db.rename_account(db.db_init(), sys.argv[2], sys.argv[3])
        print(f"moved {n} post(s) from {sys.argv[2]!r} to {sys.argv[3]!r}")
    else:
        scrape.main()
