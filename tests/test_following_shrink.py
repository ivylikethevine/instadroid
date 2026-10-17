"""A Following-list refresh far shorter than the stored list (db.store_following()): held back with an
alert, until a refresh agrees with the stored list again or `scraper.py following --accept` takes it."""

import runpy
import sqlite3
import sys

import pytest
from devtools import ROOT
from instadroid import alerts, config, db

from tests.support import sql_column

STORED: list[str] = ["a", "b", "c", "d"]


@pytest.fixture
def con(con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    monkeypatch.setattr(config, "FOLLOWING_SHRINK_PERCENT", 50)
    assert db.store_following(con, STORED) is None
    return con


def _following(con: sqlite3.Connection, table: str = "following") -> set[object]:
    return set(sql_column(con.execute(f"SELECT username FROM {table}")))


def test_a_refresh_of_at_least_the_threshold_replaces_the_list(con: sqlite3.Connection) -> None:
    assert db.store_following(con, ["a", "e"]) is None  # 2 of 4 is 50%
    assert _following(con) == {"a", "e"}
    assert db.held_following(con) is None


def test_a_far_shorter_refresh_is_held_and_the_stored_list_kept(con: sqlite3.Connection) -> None:
    con.execute("UPDATE following SET updated_at = '2020-01-01'")
    assert db.store_following(con, ["a"]) == (1, 4)
    assert _following(con) == set(STORED)
    assert _following(con, "following_pending") == {"a"}
    assert db.held_following(con) == (1, 4)
    assert sql_column(con.execute("SELECT DISTINCT updated_at FROM following")) != ["2020-01-01"]


def test_a_refresh_that_agrees_again_clears_the_held_one(con: sqlite3.Connection) -> None:
    assert db.store_following(con, ["a"]) is not None
    assert db.store_following(con, [*STORED, "e"]) is None
    assert db.held_following(con) is None and _following(con) == {*STORED, "e"}


def test_zero_turns_the_check_off(con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "FOLLOWING_SHRINK_PERCENT", 0)
    assert db.store_following(con, ["a"]) is None
    assert _following(con) == {"a"}


def test_accepting_stores_the_held_refresh(con: sqlite3.Connection) -> None:
    assert db.accept_following(con) == 0  # nothing held
    assert db.store_following(con, ["a"]) is not None
    assert db.accept_following(con) == 1
    assert _following(con) == {"a"} and db.held_following(con) is None


def test_a_held_refresh_is_an_alert_until_it_is_resolved(con: sqlite3.Connection) -> None:
    assert alerts.FOLLOWING not in alerts.conditions(con)
    assert db.store_following(con, ["a"]) is not None
    message: str = alerts.conditions(con)[alerts.FOLLOWING]
    assert "found 1 of the 4 stored accounts" in message and "scraper.py following --accept" in message
    assert db.accept_following(con) == 1
    assert alerts.FOLLOWING not in alerts.conditions(con)


def test_the_summary_names_the_list_and_a_held_refresh(con: sqlite3.Connection) -> None:
    assert db.following_summary(con).startswith("4 account(s) stored, refreshed 20")
    assert db.store_following(con, ["a"]) is not None
    assert "held back: a refresh that found 1 account(s)" in db.following_summary(con)
    con.execute("DELETE FROM following")
    assert db.following_summary(con).startswith("no Following list stored")


def test_the_following_command_shows_and_accepts(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert db.store_following(con, ["a"]) is not None

    def run(*args: str) -> None:
        monkeypatch.setattr(sys, "argv", ["scraper.py", *args])
        runpy.run_path(str(ROOT / "app" / "scraper.py"), run_name="__main__")

    run("following")
    assert "held back" in capsys.readouterr().out
    run("following", "--accept")
    out: str = capsys.readouterr().out
    assert "stored the held refresh: 1 account(s)" in out and "1 account(s) stored" in out
    with pytest.raises(SystemExit):
        run("following", "--bogus")
