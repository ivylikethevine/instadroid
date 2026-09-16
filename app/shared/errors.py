"""Error text for display, shared by the scraper and the feed server."""


def short_error(error: str, limit: int | None = 140) -> str:
    """First line only, capped at `limit` characters (None = uncapped): some exceptions (e.g. a
    uiautomator2 server crash) embed a whole multi-KB Java stack trace in their own str(), which would
    otherwise blow up a table, an alert or a report."""
    first_line: str = next(iter(error.splitlines()), "")
    return first_line[: limit - 1] + "…" if limit is not None and len(first_line) > limit else first_line
