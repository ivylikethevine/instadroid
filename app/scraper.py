"""Command-line entry point. `python scraper.py` runs the poll loop; the subcommands are one-offs:

    once                     one scrape run
    login                    log in (or confirm the session is live), then stop Instagram
    profiles                 list the Instagram version profiles
    install [VERSION|latest] install an Instagram build (default: the active profile's)
    dump                     save the current screen's hierarchy + screenshot to DEBUG_DIR
    rename OLD NEW           move an account's history to its new username

The scraper itself lives in the instadroid/ package.
"""

import sys

from igprofiles import available as available_profiles
from igprofiles import select as select_profile
from instadroid import config, db, device, diagnostics, install, navigation, scrape, versioning

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "once":
        con = db.db_init()
        stats = scrape.scrape_once(device.connect_device(), con)
        print(stats["new"], "new posts,", stats["new_stories"], "new stories")
    elif len(sys.argv) > 1 and sys.argv[1] == "login":
        d = device.connect_device()
        try:
            print("logged in:", navigation.ensure_logged_in(d))
        finally:
            device.force_stop(d, config.IG_PKG)  # don't leave ~800MiB resident for whatever runs next
    elif len(sys.argv) > 1 and sys.argv[1] == "profiles":
        for name in available_profiles():
            p = select_profile(name)[0]
            active = " (active)" if p.name == versioning.PROFILE.name else ""
            print(f"{p.name}{active}  installs {p.apk_version}  {p.notes}")
    elif len(sys.argv) > 1 and sys.argv[1] == "install":
        if len(sys.argv) > 3:
            print("usage: scraper.py install [VERSION|latest]   (default: the active profile's apk_version)")
            sys.exit(1)
        d = device.connect_device()
        print("installed:", install.install_instagram_version(d, sys.argv[2] if len(sys.argv) == 3 else None))
    elif len(sys.argv) > 1 and sys.argv[1] == "dump":
        d = device.connect_device()
        diagnostics.dump_debug(d, "manual")
        print("wrote", config.DEBUG_DIR)
    elif len(sys.argv) > 1 and sys.argv[1] == "rename":
        if len(sys.argv) != 4:
            print("usage: scraper.py rename <old_username> <new_username>")
            sys.exit(1)
        n = db.rename_account(db.db_init(), sys.argv[2], sys.argv[3])
        print(f"moved {n} post(s) from {sys.argv[2]!r} to {sys.argv[3]!r}")
    else:
        scrape.main()
