"""Tests for the shadow-portfolio target signal: the deployed 50/50
Momentum + Low-Vol blend (PROJECT.md §11o), reusing the exact research
pipeline -- no new signal-generation logic (design spec, Architecture)."""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import pytest

from algotrade.shadow import signal

D = date


def test_combine_sleeves_sums_overlapping_symbols():
    a = pl.DataFrame(
        {"date": [D(2026, 1, 1)] * 2, "symbol": ["AAA", "BBB"], "weight": [0.5, 0.5]}
    )
    b = pl.DataFrame(
        {"date": [D(2026, 1, 1)] * 2, "symbol": ["AAA", "CCC"], "weight": [0.4, 0.6]}
    )
    out = signal.combine_sleeves((a, 0.5), (b, 0.5))
    by_symbol = dict(zip(out["symbol"].to_list(), out["weight"].to_list()))
    assert by_symbol["AAA"] == pytest.approx(0.5 * 0.5 + 0.5 * 0.4)  # in both sleeves
    assert by_symbol["BBB"] == pytest.approx(0.5 * 0.5)              # sleeve a only
    assert by_symbol["CCC"] == pytest.approx(0.5 * 0.6)              # sleeve b only


def test_combine_sleeves_total_gross_matches_allocations():
    a = pl.DataFrame({"date": [D(2026, 1, 1)], "symbol": ["AAA"], "weight": [1.0]})
    b = pl.DataFrame({"date": [D(2026, 1, 1)], "symbol": ["BBB"], "weight": [1.0]})
    out = signal.combine_sleeves((a, 0.5), (b, 0.5))
    assert out["weight"].sum() == pytest.approx(1.0)


def _synthetic_panel(n_days: int = 300, symbols: tuple = ("AAA", "BBB", "CCC", "DDD", "EEE")):
    """A panel long enough for 252-1-21 momentum and 60d realised vol to be
    non-null on the last session, small enough (5 names) that every name
    clears the buffer's entry=30 threshold on every rebalance -- so each
    sleeve's per-symbol weight is deterministic: 1 / len(symbols).
    """
    days = [D(2025, 1, 1) + timedelta(days=i) for i in range(n_days)]
    rows = []
    for j, sym in enumerate(symbols):
        # Distinct, gently trending series so momentum/vol are well-defined
        # (not zero-variance), without needing the ranking to matter here.
        price = 100.0
        for i, d in enumerate(days):
            price *= 1 + 0.0005 * (1 + j) * ((-1) ** (i // 10))
            rows.append({"date": d, "symbol": sym, "close": price, "turnover": 1e8})
    return pl.DataFrame(rows)


def test_deployed_weights_blends_momentum_and_low_vol_50_50():
    panel = _synthetic_panel()
    weights = signal.deployed_weights(panel)
    assert not weights.is_empty()
    assert set(weights.columns) == {"date", "symbol", "weight"}

    last_date = weights["date"].max()
    last = weights.filter(pl.col("date") == last_date)
    # All 5 names clear entry=30 in both sleeves, so each is held by both and
    # the combined weight is exactly 1/5 per name, gross exactly 1.0.
    assert last.height == 5
    assert last["weight"].to_list() == pytest.approx([0.2] * 5)
    assert last["weight"].sum() == pytest.approx(1.0)
