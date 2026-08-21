"""Tests for the PREV_CLOSE integrity checks (PROJECT.md §10.7).

Synthetic panels, because each behaviour needs a case constructed in isolation.
The real-data equivalents are in test_integrity_real.py.
"""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from algotrade.core import integrity as ig

D = date


def panel(rows: list[tuple[date, str, float, float]]) -> pl.DataFrame:
    """Build a minimal panel from (date, symbol, prev_close, close) tuples."""
    return pl.DataFrame(
        rows,
        schema={
            "date": pl.Date,
            "symbol": pl.String,
            "prev_close": pl.Float64,
            "close": pl.Float64,
        },
        orient="row",
    )


# --- corporate actions -------------------------------------------------------


def test_action_detected_from_within_row_gap():
    """TATASTEEL's real 1:10 split: prev_close is NOT adjusted on the ex-date."""
    p = panel(
        [
            (D(2022, 7, 27), "TATASTEEL", 949.50, 959.40),
            (D(2022, 7, 28), "TATASTEEL", 959.40, 100.35),
        ]
    )
    found = ig.find_actions(p)
    assert found.height == 1
    assert found["date"][0] == D(2022, 7, 28)
    # Detects reliably, but the implied factor is only ~9.56 against a true 10.
    assert found["implied_ratio"][0] == pytest.approx(9.56, abs=0.01)


def test_action_does_not_break_the_chain():
    """The two checks must stay independent: a split leaves the chain intact."""
    p = panel(
        [
            (D(2022, 7, 27), "TATASTEEL", 949.50, 959.40),
            (D(2022, 7, 28), "TATASTEEL", 959.40, 100.35),
        ]
    )
    assert ig.find_actions(p).height == 1
    assert ig.find_chain_breaks(p).is_empty()


def test_normal_moves_are_not_flagged():
    p = panel(
        [
            (D(2024, 1, 1), "FOO", 100.0, 100.0),
            (D(2024, 1, 2), "FOO", 100.0, 118.0),  # +18%, under the 25% floor
        ]
    )
    assert ig.find_actions(p).is_empty()


def test_listing_day_is_not_an_action():
    """SYRMA listed 2022-08-26: prev_close is the IPO issue price, not a close.

    Without skipping each symbol's first row this reads as a 41% action.
    """
    p = panel(
        [
            (D(2022, 8, 26), "SYRMA", 220.0, 310.50),
            (D(2022, 8, 29), "SYRMA", 310.50, 305.00),
        ]
    )
    assert ig.find_actions(p).is_empty()


# --- chain breaks and missing sessions ---------------------------------------


def test_chain_break_when_previous_session_absent():
    p = panel(
        [
            (D(2022, 8, 5), "FOO", 500.0, 521.40),
            # 2022-08-08 missing: prev_close refers to a close we don't have.
            (D(2022, 8, 10), "FOO", 549.50, 537.35),
        ]
    )
    breaks = ig.find_chain_breaks(p)
    assert breaks.height == 1
    assert breaks["expected_prev_close"][0] == pytest.approx(521.40)
    assert breaks["actual_prev_close"][0] == pytest.approx(549.50)


def test_holiday_does_not_break_the_chain():
    """NSE reports the last *traded* close, so gaps with no session are fine."""
    p = panel(
        [
            (D(2022, 8, 12), "FOO", 100.0, 105.0),
            # 2022-08-15 was Independence Day; next session is the 16th.
            (D(2022, 8, 16), "FOO", 105.0, 107.0),
        ]
    )
    assert ig.find_chain_breaks(p).is_empty()


def test_missing_session_needs_a_broad_break():
    """One symbol breaking is an anomaly; nearly all breaking is a missing file."""
    rows = [(D(2024, 1, 1), f"S{i}", 100.0, 100.0) for i in range(10)]
    # Nine of ten symbols disagree with their own prior close.
    rows += [(D(2024, 1, 3), f"S{i}", 150.0, 150.0) for i in range(9)]
    rows += [(D(2024, 1, 3), "S9", 100.0, 100.0)]
    missing = ig.find_missing_sessions(panel(rows))
    assert missing.height == 1
    assert missing["date"][0] == D(2024, 1, 3)
    assert missing["gap_after"][0] == D(2024, 1, 1)
    assert missing["frac_broken"][0] == pytest.approx(0.9)


def test_single_symbol_anomaly_is_not_a_missing_session():
    rows = [(D(2024, 1, 1), f"S{i}", 100.0, 100.0) for i in range(10)]
    rows += [(D(2024, 1, 2), f"S{i}", 100.0, 100.0) for i in range(9)]
    rows += [(D(2024, 1, 2), "S9", 175.0, 175.0)]  # lone break
    p = panel(rows)
    assert ig.find_missing_sessions(p).is_empty()
    assert ig.find_chain_breaks(p).height == 1


def test_unexplained_excludes_missing_session_noise():
    """The residue after removing missing-session breaks is the quality score."""
    rows = [(D(2024, 1, 1), f"S{i}", 100.0, 100.0) for i in range(10)]
    rows += [(D(2024, 1, 3), f"S{i}", 150.0, 150.0) for i in range(10)]
    rows += [(D(2024, 1, 4), f"S{i}", 150.0, 150.0) for i in range(9)]
    rows += [(D(2024, 1, 4), "S9", 999.0, 999.0)]  # unrelated anomaly
    p = panel(rows)
    assert ig.find_chain_breaks(p).height == 11
    residue = ig.unexplained_chain_breaks(p)
    assert residue.height == 1
    assert residue["symbol"][0] == "S9"
    assert residue["date"][0] == D(2024, 1, 4)


# --- renames -----------------------------------------------------------------


def test_rename_matched_on_price_continuity():
    """The real MINDAIND -> UNOMINDA changeover, 2022-08-04/05."""
    rows = [
        (D(2022, 8, 3), "MINDAIND", 520.0, 525.0),
        (D(2022, 8, 4), "MINDAIND", 525.0, 529.05),
        (D(2022, 8, 5), "UNOMINDA", 529.05, 521.40),
        (D(2022, 8, 8), "UNOMINDA", 521.40, 530.0),
        # A bystander spanning the whole window, so neither date is a boundary.
        (D(2022, 8, 3), "OTHER", 10.0, 10.0),
        (D(2022, 8, 4), "OTHER", 10.0, 10.0),
        (D(2022, 8, 5), "OTHER", 10.0, 10.0),
        (D(2022, 8, 8), "OTHER", 10.0, 10.0),
    ]
    found = ig.find_renames(panel(rows))
    assert found.height == 1
    assert found.row(0, named=True)["old_symbol"] == "MINDAIND"
    assert found.row(0, named=True)["new_symbol"] == "UNOMINDA"
    assert found["price"][0] == pytest.approx(529.05)


def test_ambiguous_renames_are_withheld():
    """Two vanish and two appear at the same price: report none, guess nothing."""
    rows = [
        (D(2024, 1, 1), "OLDA", 50.0, 100.0),
        (D(2024, 1, 1), "OLDB", 50.0, 100.0),
        (D(2024, 1, 2), "NEWA", 100.0, 101.0),
        (D(2024, 1, 2), "NEWB", 100.0, 102.0),
        (D(2024, 1, 1), "OTHER", 10.0, 10.0),
        (D(2024, 1, 2), "OTHER", 10.0, 10.0),
    ]
    assert ig.find_renames(panel(rows)).is_empty()


def test_delisting_is_not_reported_as_rename():
    rows = [
        (D(2024, 1, 1), "DEAD", 50.0, 40.0),
        (D(2024, 1, 1), "LIVE", 10.0, 10.0),
        (D(2024, 1, 2), "LIVE", 10.0, 10.0),
    ]
    p = panel(rows)
    assert ig.find_renames(p).is_empty()
    delisted = ig.find_delistings(p)
    assert delisted.height == 1
    assert delisted["symbol"][0] == "DEAD"


def test_missing_required_column_raises():
    with pytest.raises(ValueError, match="missing required columns"):
        ig.find_actions(pl.DataFrame({"date": [], "symbol": []}))
