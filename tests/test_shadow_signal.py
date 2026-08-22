"""Tests for the deployed momentum-sleeve signal (PROJECT.md §11o), reusing
the exact research pipeline -- no new signal-generation logic."""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import pytest

from algotrade.shadow import signal

D = date


def _synthetic_panel(n_days: int = 300, symbols: tuple = ("AAA", "BBB", "CCC", "DDD", "EEE")):
    """A panel long enough for 252-1-21 momentum to be non-null on the last
    session, small enough (5 names) that every name clears the buffer's
    entry=30 threshold -- so the held book is deterministic.
    """
    days = [D(2025, 1, 1) + timedelta(days=i) for i in range(n_days)]
    rows = []
    for j, sym in enumerate(symbols):
        price = 100.0
        for i, d in enumerate(days):
            price *= 1 + 0.0005 * (1 + j) * ((-1) ** (i // 10))
            rows.append({"date": d, "symbol": sym, "close": price, "turnover": 1e8})
    return pl.DataFrame(rows)


def test_deployed_weights_is_a_fully_invested_momentum_book():
    panel = _synthetic_panel()
    weights = signal.deployed_weights(panel)
    assert not weights.is_empty()
    assert set(weights.columns) == {"date", "symbol", "weight"}

    last = weights.filter(pl.col("date") == weights["date"].max())
    # All 5 names clear entry=30, so each is held at 1/5 and gross is 1.0 --
    # the sleeve is fully invested in ITSELF; the 50% account allocation is
    # applied by the caller, not here.
    assert last.height == 5
    assert last["weight"].to_list() == pytest.approx([0.2] * 5)
    assert last["weight"].sum() == pytest.approx(1.0)


def test_deployed_weights_holds_at_most_the_entry_count():
    panel = _synthetic_panel(symbols=tuple(f"S{i:02d}" for i in range(40)))
    weights = signal.deployed_weights(panel)
    per_date = weights.group_by("date").agg(pl.len().alias("n"))
    assert per_date["n"].max() <= signal.ENTRY


def test_the_frozen_parameters_are_the_ones_11o_deployed():
    """A guard, not a tautology: these are pre-registered (§10.4, §11o) and
    changing one is a new strategy version, not an edit to this one."""
    assert (signal.ENTRY, signal.EXIT, signal.CADENCE) == (30, 60, "monthly")
    assert (signal.MOMENTUM_LOOKBACK, signal.MOMENTUM_SKIP) == (252, 21)
