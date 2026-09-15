"""Instagram -> SQLite scraper driving a real Instagram app inside a redroid container via uiautomator2.

Strategy: open the chronological "Following" feed, scroll slowly, parse the accessibility tree for post
cards, store new ones, stop once we hit posts we've already seen. Selectors live in per-Instagram-version
profiles under igprofiles/ (see docs/NEXT.md).

    config       settings from the environment (read as config.NAME, so tests can patch them)
    common       logging, timestamps, bounds, hashing
    versioning   the active version profile, SELECTORS, and the @versioned hook
    device       uiautomator2 connection, launch, timing/gestures, memory housekeeping
    install      apkeep fetch + adb install
    diagnostics  debug dumps and failure logcats
    parsing      pure parsing of hierarchy dumps (what the replay tests exercise)
    db           schema, numbered migrations, runs, accounts, duplicate merging
    backup       consistent copies of the database
    retention    pruning posts, stories and media
    navigation   login, feeds, sheets, the Following list
    capture      captions, permalinks, media crops, carousels, avatars
    stories      story capture and dedupe
    scrape       one run (scrape_once) and the poll loop (main)
    alerts       failure alerts after each run
    control      manual lock and scrape-now requests for the poll loop

Modules refer to each other as module.name (device.human_pause), never `from .device import
human_pause`, so a monkeypatch on the owning module reaches every caller.
"""

# Import every module so all @versioned functions are registered before a profile is checked.
from . import (  # noqa: F401
    alerts,
    backup,
    capture,
    common,
    config,
    control,
    db,
    device,
    diagnostics,
    install,
    navigation,
    parsing,
    retention,
    scrape,
    stories,
    versioning,
)
