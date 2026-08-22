"""Tests for the shadow-portfolio parity diff (PROJECT.md §12, shadow-portfolio
agreement) -- does tonight's recomputed weight history match last night's, on
every date that was already decided?
"""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from algotrade.shadow import parity

D = date


def _weights(rows: list[tuple]) -> pl.DataFrame:
    return pl.DataFrame(
        rows, schema=["date", "symbol", "weight"], orient="row"
    ).cast({"date": pl.Date, "symbol": pl.String, "weight": pl.Float64})


def test_identical_history_produces_no_discrepancies():
    old = _weights([(D(2026, 1, 1), "TCS", 0.5), (D(2026, 1, 1), "INFY", 0.5)])
    new = _weights(
        [(D(2026, 1, 1), "TCS", 0.5), (D(2026, 1, 1), "INFY", 0.5),
         (D(2026, 1, 2), "TCS", 0.5), (D(2026, 1, 2), "INFY", 0.5)]
    )
    out = parity.diff_weights(old, new, new_session=D(2026, 1, 2))
    assert out.is_empty()


def test_a_changed_weight_on_a_past_date_is_a_discrepancy():
    old = _weights([(D(2026, 1, 1), "TCS", 0.5)])
    new = _weights([(D(2026, 1, 1), "TCS", 0.6)])
    out = parity.diff_weights(old, new, new_session=D(2026, 1, 2))
    assert out.height == 1
    row = out.row(0, named=True)
    assert row["date"] == D(2026, 1, 1)
    assert row["symbol"] == "TCS"
    assert row["old_weight"] == 0.5
    assert row["new_weight"] == 0.6
    assert row["diff"] == pytest.approx(0.1)


def test_a_row_added_on_a_past_date_is_a_discrepancy():
    old = _weights([(D(2026, 1, 1), "TCS", 0.5)])
    new = _weights([(D(2026, 1, 1), "TCS", 0.5), (D(2026, 1, 1), "INFY", 0.3)])
    out = parity.diff_weights(old, new, new_session=D(2026, 1, 2))
    assert out.height == 1
    row = out.row(0, named=True)
    assert row["symbol"] == "INFY"
    assert row["old_weight"] is None
    assert row["new_weight"] == 0.3


def test_a_row_removed_on_a_past_date_is_a_discrepancy():
    old = _weights([(D(2026, 1, 1), "TCS", 0.5), (D(2026, 1, 1), "INFY", 0.3)])
    new = _weights([(D(2026, 1, 1), "TCS", 0.5)])
    out = parity.diff_weights(old, new, new_session=D(2026, 1, 2))
    assert out.height == 1
    row = out.row(0, named=True)
    assert row["symbol"] == "INFY"
    assert row["old_weight"] == 0.3
    assert row["new_weight"] is None


def test_the_new_tail_is_never_flagged_even_though_it_is_absent_from_old():
    old = _weights([(D(2026, 1, 1), "TCS", 0.5)])
    new = _weights([(D(2026, 1, 1), "TCS", 0.5), (D(2026, 1, 2), "TCS", 0.5)])
    out = parity.diff_weights(old, new, new_session=D(2026, 1, 2))
    assert out.is_empty()


def test_a_diff_inside_tolerance_is_not_flagged():
    old = _weights([(D(2026, 1, 1), "TCS", 0.333333333)])
    new = _weights([(D(2026, 1, 1), "TCS", 0.333333334)])
    out = parity.diff_weights(old, new, new_session=D(2026, 1, 2), tol=1e-6)
    assert out.is_empty()


def test_an_empty_old_history_flags_nothing_new_before_the_session():
    """First-ever run: nothing to compare against, so nothing is a regression."""
    old = _weights([])
    new = _weights([(D(2026, 1, 1), "TCS", 0.5)])
    out = parity.diff_weights(old, new, new_session=D(2026, 1, 1))
    assert out.is_empty()
