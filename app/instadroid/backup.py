"""Consistent copies of the posts database, taken at the end of a run and by `scraper.py backup`.

sqlite3's online backup API copies a consistent snapshot even while the database is open, unlike a
file copy of a database the feed server may be reading. For redroid's /data (the logged-in session),
see scripts/snapshot-android-data.sh: that needs redroid stopped, so it isn't automatic.
"""

import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import config
from .common import log

_NAME = "posts-%Y%m%dT%H%M%SZ.sqlite"


def _taken_at(path: Path) -> datetime | None:
    try:
        return datetime.strptime(path.name, _NAME).replace(tzinfo=UTC)
    except ValueError:
        return None


def backups() -> list[Path]:
    """Existing backups in BACKUP_DIR, oldest first (_NAME is fixed-width UTC, so by name)."""
    directory: Path = Path(config.BACKUP_DIR)
    found: list[Path] = (
        [p for p in directory.glob("posts-*.sqlite") if _taken_at(p)] if directory.is_dir() else []
    )
    return sorted(found, key=lambda p: p.name)


def backup_database(con: sqlite3.Connection, force: bool = False, now: datetime | None = None) -> Path | None:
    """Copy the database to BACKUP_DIR/posts-<utc>.sqlite when the newest backup is at least
    BACKUP_EVERY_HOURS old (or `force`), then keep only the newest BACKUP_KEEP. Returns the new file,
    or None when no backup was due. BACKUP_EVERY_HOURS=0 disables the automatic one."""
    now = now or datetime.now(UTC)
    if not force:
        if config.BACKUP_EVERY_HOURS <= 0:
            return None
        existing: list[Path] = backups()
        newest: datetime | None = _taken_at(existing[-1]) if existing else None
        if newest and now - newest < timedelta(hours=config.BACKUP_EVERY_HOURS):
            return None
    directory: Path = Path(config.BACKUP_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    dest: Path = directory / now.strftime(_NAME)
    partial: Path = dest.with_name(dest.name + ".part")  # never leave a half-written file under the real name
    out: sqlite3.Connection
    with closing(sqlite3.connect(partial)) as out:
        con.backup(out)
    partial.replace(dest)
    for old in backups()[: -config.BACKUP_KEEP] if config.BACKUP_KEEP > 0 else []:
        old.unlink(missing_ok=True)
    log(f"backed up the database to {dest}")
    return dest
