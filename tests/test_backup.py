import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from instadroid import backup, config


@pytest.fixture
def con(con: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    """conftest's database, holding one post, with backups under tmp_path."""
    monkeypatch.setattr(config, "BACKUP_DIR", str(tmp_path / "backups"))
    con.execute(
        "INSERT INTO posts (id, username, scraped_at) VALUES ('p1', 'someone', '2026-09-14T00:00:00+00:00')"
    )
    con.commit()
    return con


NOW: datetime = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)


def test_a_backup_is_a_consistent_copy(con: sqlite3.Connection) -> None:
    path: Path | None = backup.backup_database(con, now=NOW)
    assert path is not None and path.name == "posts-20260914T120000Z.sqlite"
    copy: sqlite3.Connection
    with closing(sqlite3.connect(path)) as copy:
        assert copy.execute("SELECT id FROM posts").fetchall() == [("p1",)]
    assert not list(path.parent.glob("*.part"))


def test_backups_wait_out_the_interval_unless_forced(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "BACKUP_EVERY_HOURS", 24)
    assert backup.backup_database(con, now=NOW)
    assert backup.backup_database(con, now=NOW + timedelta(hours=23)) is None
    assert backup.backup_database(con, force=True, now=NOW + timedelta(hours=23))
    assert backup.backup_database(con, now=NOW + timedelta(hours=47)) is not None
    assert len(backup.backups()) == 3


def test_only_the_newest_backups_are_kept(con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "BACKUP_KEEP", 2)
    day: int
    for day in range(4):
        backup.backup_database(con, force=True, now=NOW + timedelta(days=day))
    assert [p.name for p in backup.backups()] == [
        "posts-20260916T120000Z.sqlite",
        "posts-20260917T120000Z.sqlite",
    ]


def test_zero_hours_disables_automatic_backups(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "BACKUP_EVERY_HOURS", 0)
    assert backup.backup_database(con, now=NOW) is None
    assert backup.backup_database(con, force=True, now=NOW) is not None


def test_backups_ignore_files_that_are_not_timestamped_backups(con: sqlite3.Connection) -> None:
    directory: Path = Path(config.BACKUP_DIR)
    directory.mkdir()
    (directory / "posts-manual-copy.sqlite").write_bytes(b"")  # e.g. copied there by hand
    assert backup.backups() == []
    taken: Path | None = backup.backup_database(con, force=True, now=NOW)
    assert backup.backups() == [taken]
    assert (directory / "posts-manual-copy.sqlite").exists()  # never pruned as an "old backup"
