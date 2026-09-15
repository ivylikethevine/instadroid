"""Selector drift: a run's cards-per-screen compared with a baseline of recent successful runs."""

import sqlite3

import pytest
from instadroid import config, db

from tests.support import record_run_ago


def test_selector_drift_flags_a_drop_below_the_baseline(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "SELECTOR_DRIFT_MIN_RUNS", 3)
    for _ in range(5):
        record_run_ago(con, 0, cards_per_screen=4.0, share_captioned=0.8, share_complete=0.9)

    warning = db.check_selector_drift(con, cards_per_screen=1.0, share_captioned=0.8, share_complete=0.9)

    assert warning is not None
    assert "cards/screen" in warning
    assert "1.00 vs 4.00 baseline (5 runs)" in warning


def test_selector_drift_silent_when_in_line_with_baseline(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "SELECTOR_DRIFT_MIN_RUNS", 3)
    for _ in range(5):
        record_run_ago(con, 0, cards_per_screen=4.0, share_captioned=0.8, share_complete=0.9)

    warning = db.check_selector_drift(con, cards_per_screen=3.6, share_captioned=0.75, share_complete=0.85)

    assert warning is None


def test_selector_drift_silent_with_too_few_baseline_runs(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "SELECTOR_DRIFT_MIN_RUNS", 3)
    record_run_ago(con, 0, cards_per_screen=4.0, share_captioned=0.8, share_complete=0.9)
    record_run_ago(con, 0, cards_per_screen=4.0, share_captioned=0.8, share_complete=0.9)

    # Only 2 prior runs, below SELECTOR_DRIFT_MIN_RUNS — nothing to judge against yet.
    warning = db.check_selector_drift(con, cards_per_screen=0.0, share_captioned=0.0, share_complete=0.0)

    assert warning is None


def test_selector_drift_ignores_failed_runs_in_the_baseline(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "SELECTOR_DRIFT_MIN_RUNS", 3)
    for _ in range(4):
        record_run_ago(con, 0, cards_per_screen=4.0, share_captioned=0.8, share_complete=0.9)
    # A failed run with no parse stats at all (error set, cards_per_screen NULL) must not count
    # toward, or break, the baseline query.
    record_run_ago(con, 0, error="DeviceNotReady")

    warning = db.check_selector_drift(con, cards_per_screen=3.6, share_captioned=0.75, share_complete=0.85)

    assert warning is None


def test_selector_drift_disabled_when_baseline_runs_is_zero(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "SELECTOR_DRIFT_BASELINE_RUNS", 0)
    for _ in range(5):
        record_run_ago(con, 0, cards_per_screen=4.0, share_captioned=0.8, share_complete=0.9)

    warning = db.check_selector_drift(con, cards_per_screen=0.0, share_captioned=0.0, share_complete=0.0)

    assert warning is None
