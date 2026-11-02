"""Command-line entry point. `python scraper.py` runs the poll loop; the subcommands are one-offs:

    once                     one scrape run (recorded in the runs table like a scheduled one)
    login                    log in (or confirm the session is live), then stop Instagram
    profiles                 list the Instagram version profiles, what each covers and has validated
    install [--force] [VERSION|latest]
                             install an Instagram build (default: igprofiles.DEFAULT_BUILD); refused
                             without a /data snapshot younger than SNAPSHOT_MAX_DAYS unless --force
    tune                     apply the device tuning now, whatever TUNE_ON_CONNECT says
    dump                     save the current screen's hierarchy + screenshot to DEBUG_DIR
    doctor                   one report: control state, recent runs, device over plain adb, logcat
                             crash signatures with their fixes (never drives the device)
    compat                   redroid image / Instagram build pairs this database has run
    backup                   copy the database to BACKUP_DIR now
    export DIR [--format html|json]
                             write every stored post and its media under DIR, to keep past
                             RETAIN_DAYS; a rerun adds to it (default html: JSON plus static pages)
    lock / unlock            hold scheduled runs while driving the device by hand, then release
                             (unlock also clears the hold a login challenge raised)
    scrape-now               ask the poll loop to run now (rate-limited, see instadroid/control.py)
    following [--accept]     the stored Following list, and a refresh held back for being far
                             shorter; --accept stores that refresh instead
    rename OLD NEW           move an account's history to its new username
    rename --dismiss OLD NEW   OLD did not become NEW: drop that suspicion for good

The scraper itself lives in the instadroid/ package.
"""

import sqlite3
import sys
from collections.abc import Callable
from contextlib import closing
from typing import Concatenate

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
    export,
    install,
    navigation,
    scrape,
    tune,
    uidevice,
    versioning,
)
from instadroid.db import VersionPair
from instadroid.scrape import RunStats


def _with_db[**P, R](
    action: Callable[Concatenate[sqlite3.Connection, P], R], *args: P.args, **kwargs: P.kwargs
) -> R:
    """Run `action` on an open database connection, closed afterward."""
    con: sqlite3.Connection
    with closing(db.db_init()) as con:
        return action(con, *args, **kwargs)


def _run_once(con: sqlite3.Connection) -> None:
    wait: float
    if wait := scrape.budget_wait_seconds(con):
        sys.exit(
            f"{config.MAX_RUNS_PER_DAY} runs already started in the last 24h (MAX_RUNS_PER_DAY);"
            f" the next is allowed in {wait / 3600:.1f}h. MAX_RUNS_PER_DAY=0 disables the budget."
        )
    stats: RunStats | None
    exc: Exception | None
    stats, exc = scrape.run_recorded(con)  # recorded in runs, like a scheduled run
    if exc or stats is None:
        sys.exit(f"run failed: {exc!r}")
    print(stats["new"], "new posts,", stats["metrics"].get("new_stories", 0), "new stories")


def _print_profiles() -> None:
    names: list[str] = available_profiles()
    name: str
    following: str | None
    for name, following in zip(names, [*names[1:], None], strict=True):
        p: BaseProfile = load_profile(name)
        covers: str = f"{p.major}-{int(following[1:]) - 1}" if following else f"{p.major} and newer"
        active: str = " (active)" if p.name == versioning.PROFILE.name else ""
        confirmed: str = ", ".join(sorted(p.own_validated, key=version_key)) or "none yet"
        suspected: str = (
            ", ".join(str(low) if low == high else f"{low}-{high}" for low, high in p.suspected()) or "none"
        )
        print(
            f"{p.name}{active}  covers Instagram {covers}  {p.notes}\n"
            f"  verified (confirmed by a run): {confirmed}\n"
            f"  verified (suspected, between confirmed builds): {suspected}"
        )
    print("default install:", default_build() or "latest")


def _print_compat(pairs: list[VersionPair]) -> None:
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


def _print_following(con: sqlite3.Connection, accept: bool) -> None:
    if accept:
        print(f"stored the held refresh: {db.accept_following(con)} account(s)")
    print(db.following_summary(con))


def main(argv: list[str]) -> None:
    version: list[str]
    args: list[str]
    action: str
    old: str
    new: str
    directory: str
    d: uidevice.Device
    match argv:
        case []:
            scrape.main()
        case ["once", *_]:
            _with_db(_run_once)
        case ["login", *_]:
            d = device.connect_device()
            try:
                navigation.ensure_logged_in(d)
                print("logged in")
            finally:
                device.force_stop(d, config.IG_PKG)  # don't leave ~800MiB resident for whatever runs next
        case ["profiles", *_]:
            _print_profiles()
        case ["install", *args] if len([a for a in args if a != "--force"]) <= 1:
            version = [a for a in args if a != "--force"]
            snapshot: str | None
            if (snapshot := install.snapshot_problem()) and "--force" not in args:
                sys.exit(
                    f"{snapshot}. Stop redroid and run scripts/snapshot-android-data.sh first,"
                    " or pass --force to replace Instagram without one."
                )
            d = device.connect_device()
            print("installed:", install.install_instagram_version(d, version[0] if version else None))
        case ["install", *_]:
            print(
                "usage: scraper.py install [--force] [VERSION|latest]   (default: igprofiles.DEFAULT_BUILD)"
            )
            sys.exit(1)
        case ["tune", *_]:
            if not tune.tune_device(device.connect_device(), force=True):
                sys.exit("tuning failed; see the log above")
        case ["dump", *_]:
            d = device.connect_device()
            diagnostics.dump_debug(d, "manual")
            print("wrote", config.DEBUG_DIR)
        case ["doctor", *_]:
            print(_with_db(doctor.report), end="")
        case ["compat", *_]:
            _print_compat(_with_db(db.version_pairs))
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
            print("wrote", _with_db(backup.backup_database, force=True))
        case ["export", directory, *args] if not directory.startswith("-") and args in (
            [],
            ["--format", "html"],
            ["--format", "json"],
        ):
            summary: export.Summary
            try:
                summary = _with_db(export.export_posts, directory, html=args != ["--format", "json"])
            except OSError as e:
                sys.exit(f"export failed: {e}")
            print(f"exported {summary['posts']} post(s) of {summary['accounts']} account(s) to {directory}")
        case ["export", *_]:
            print("usage: scraper.py export <directory> [--format html|json]")
            sys.exit(1)
        case ["following", *args] if args in ([], ["--accept"]):
            _with_db(_print_following, bool(args))
        case ["following", *_]:
            print("usage: scraper.py following [--accept]")
            sys.exit(1)
        case ["rename", old, new] if not old.startswith("-"):
            print(f"moved {_with_db(db.rename_account, old, new)} post(s) from {old!r} to {new!r}")
        case ["rename", "--dismiss", old, new]:
            n: int = _with_db(db.dismiss_rename, old, new)
            print(f"dismissed: {old!r} is not {new!r} ({n} post(s) of evidence dropped)")
        case ["rename", *_]:
            print("usage: scraper.py rename [--dismiss] <old_username> <new_username>")
            sys.exit(1)
        case _:
            sys.exit(f"unknown subcommand {argv[0]!r}\n\n{__doc__}")


if __name__ == "__main__":
    main(sys.argv[1:])
