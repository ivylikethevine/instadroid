"""A row of the runs table read for display, shared by the scraper and the feed server, each of which
keeps its own format."""

import sqlite3
from datetime import datetime

from . import sqlrows
from .errors import short_error
from .timestamps import parse_iso


def run_seconds(run: sqlite3.Row) -> int | None:
    """Whole seconds from a run's start to its finish, or None without both timestamps."""
    start: datetime | None = parse_iso(sqlrows.cell(run, "started_at"))
    end: datetime | None = parse_iso(sqlrows.cell(run, "finished_at"))
    return int((end - start).total_seconds()) if start and end else None


def run_result(run: sqlite3.Row) -> tuple[str, str]:
    """(severity, text): ("err", its error), else ("warn", "warn: " + its warning), else ("", "ok")."""
    error: str | None
    if error := sqlrows.cell_str(run, "error"):
        return "err", short_error(error)
    warning: str | None
    if warning := sqlrows.opt_str(run, "warning"):
        return "warn", f"warn: {short_error(warning)}"
    return "", "ok"
