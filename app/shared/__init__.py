"""The leaf both processes share: the scraper (instadroid) and the feed server (feedserver) may import
it, and it imports neither (pyproject.toml's import-linter contracts).

    fileenv   secrets from the environment or a NAME_FILE
    sqlrows   typed reads of SQLite rows
"""
