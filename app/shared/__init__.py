"""The leaf both processes share: the scraper (instadroid) and the feed server (feedserver) may import
it, and it imports neither (pyproject.toml's import-linter contracts).

    control     the manual-lock and scrape-now files, and their settings
    env         the settings both read the same way (DB_PATH, MEDIA_DIR, POLL_MAX_HOURS)
    errors      an error's first line, capped, for display
    fileenv     secrets from the environment or a NAME_FILE
    sqlrows     typed reads of SQLite rows
    timestamps  stored ISO timestamps
"""
