"""shared.sqlrows: typed reads of SQLite rows, and the TypeError each typed read raises on a mismatch."""

import sqlite3

import pytest
from shared import sqlrows

from tests.support import fetch_row


def _to_pair(raw: bytes) -> tuple[int, int]:
    """A column converter (PARSE_COLNAMES) handing back something SQLite never does: a tuple."""
    return (len(raw), 0)


sqlite3.register_converter("SQLROWS_TEST_PAIR", _to_pair)


@pytest.fixture
def row() -> sqlite3.Row:
    """One row with a text, an integer, a real, a NULL and a converter-made non-SQL value."""
    con: sqlite3.Connection = sqlite3.connect(":memory:", detect_types=sqlite3.PARSE_COLNAMES)
    try:
        return fetch_row(
            con.execute(
                "SELECT 'hi' AS name, 7 AS n, 1.5 AS ratio, NULL AS gone, 'xy' AS \"pair [SQLROWS_TEST_PAIR]\""
            )
        )
    finally:
        con.close()


def test_cell_reads_sql_values_by_name_or_position(row: sqlite3.Row) -> None:
    assert sqlrows.cell(row, "name") == "hi" and sqlrows.cell(row, "NAME") == "hi"
    assert sqlrows.cell(row, 1) == 7 and sqlrows.cell(row, "gone") is None
    with pytest.raises(IndexError):
        sqlrows.cell(row, "missing")
    with pytest.raises(TypeError, match="column 'pair' holds tuple, not an SQLite value"):
        sqlrows.cell(row, "pair")


def test_typed_reads_reject_the_wrong_column_type(row: sqlite3.Row) -> None:
    assert sqlrows.cell_str(row, "name") == "hi" and sqlrows.cell_str(row, "gone") is None
    assert sqlrows.cell_int(row, "n") == 7 and sqlrows.cell_int(row, "gone") is None
    assert sqlrows.cell_float(row, "ratio") == 1.5 and sqlrows.cell_float(row, "n") == 7
    with pytest.raises(TypeError, match="column 'n' holds int, not text"):
        sqlrows.cell_str(row, "n")
    with pytest.raises(TypeError, match="column 'name' holds str, not an integer"):
        sqlrows.cell_int(row, "name")
    with pytest.raises(TypeError, match="column 'name' holds str, not a number"):
        sqlrows.cell_float(row, "name")


def test_must_reads_reject_null(row: sqlite3.Row) -> None:
    assert sqlrows.must_str(row, "name") == "hi" and sqlrows.must_int(row, "n") == 7
    with pytest.raises(TypeError, match="column 'gone' is NULL"):
        sqlrows.must_str(row, "gone")
    with pytest.raises(TypeError, match="column 'gone' is NULL"):
        sqlrows.must_int(row, "gone")


def test_optional_reads_tolerate_a_column_the_row_predates(row: sqlite3.Row) -> None:
    assert sqlrows.has_column(row, "Name") and not sqlrows.has_column(row, "hi")
    assert sqlrows.opt_str(row, "name") == "hi" and sqlrows.opt_str(row, "missing") is None
    assert sqlrows.opt_int(row, "n") == 7 and sqlrows.opt_int(row, "missing") is None


def test_scalars_and_values() -> None:
    con: sqlite3.Connection = sqlite3.connect(":memory:")
    try:
        assert sqlrows.scalar(con.execute("SELECT 'x'")) == "x"
        assert sqlrows.scalar_int(con.execute("SELECT COUNT(*) FROM sqlite_master")) == 0
        assert sqlrows.scalar(con.execute("SELECT 1 WHERE 0")) is None
        assert sqlrows.scalar_int(con.execute("SELECT 1 WHERE 0")) is None
        assert sqlrows.fetch_one(con.execute("SELECT 1 WHERE 0")) is None
        assert sqlrows.values(fetch_row(con.execute("SELECT 1, 'two', NULL"))) == (1, "two", None)
    finally:
        con.close()
