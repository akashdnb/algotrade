"""Tests for point-in-time universe selection (PROJECT.md §10.4)."""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import pytest

from algotrade.core import universe as uv


def build(rows: list[tuple[date, str, float]]) -> pl.DataFrame:
    """Panel from (date, symbol, turnover) tuples."""
    return pl.DataFrame(
        rows,
        schema={"date": pl.Date, "symbol": pl.String, "turnover": pl.Float64},
        orient="row",
    )


def dense(symbols: dict[str, float], sessions: int = 25) -> pl.DataFrame:
    """A panel where every symbol trades every session at a fixed turnover."""
    start = date(2024, 1, 1)
    rows = [
        (start + timedelta(days=i), sym, turnover)
        for i in range(sessions)
        for sym, turnover in symbols.items()
    ]
    return build(rows)


def test_session_index_is_dense_over_all_dates():
    panel = uv.with_session_index(
        build(
            [
                (date(2024, 1, 1), "A", 1e8),
                (date(2024, 1, 5), "A", 1e8),  # gap in between
                (date(2024, 1, 5), "B", 1e8),
            ]
        )
    )
    assert sorted(panel["session_idx"].unique().to_list()) == [0, 1]


def test_ranks_by_trailing_turnover():
    panel = uv.liquidity(dense({"BIG": 1e9, "MID": 5e8, "SMALL": 1e8}))
    picked = uv.select(panel, date(2024, 1, 25))
    assert picked["symbol"].to_list() == ["BIG", "MID", "SMALL"]


def test_turnover_floor_excludes_illiquid():
    """Below Rs 5 crore/day the fills are fiction, so the name is not tradeable."""
    panel = uv.liquidity(dense({"OK": 1e8, "THIN": 1e6}))
    picked = uv.select(panel, date(2024, 1, 25))
    assert picked["symbol"].to_list() == ["OK"]


def test_size_cap_is_respected():
    panel = uv.liquidity(dense({f"S{i:03d}": 1e8 + i for i in range(50)}))
    assert uv.select(panel, date(2024, 1, 25), size=10).height == 10


def test_sporadic_trader_is_excluded_by_coverage():
    """A name that trades 5 of 20 sessions can show healthy median turnover.

    Without the coverage filter it enters the universe and its fills are
    imaginary.
    """
    start = date(2024, 1, 1)
    # DENSE must span the whole period: the session grid is the union of all
    # symbols' dates, so without it SPORADIC's own days would *be* the sessions
    # and its coverage would legitimately be 1.0.
    rows = [(start + timedelta(days=i), "DENSE", 1e8) for i in range(125)]
    # SPORADIC trades every 5th session at the same turnover.
    rows += [(start + timedelta(days=i), "SPORADIC", 1e8) for i in range(0, 125, 5)]
    panel = uv.liquidity(build(rows))
    last = panel["date"].max()
    picked = uv.select(panel, last)
    assert "DENSE" in picked["symbol"].to_list()
    assert "SPORADIC" not in picked["symbol"].to_list()


def test_no_selection_before_the_window_fills():
    """adtv is null until `lookback` observations exist, so no look-ahead."""
    panel = uv.liquidity(dense({"A": 1e8}, sessions=25))
    early = panel["date"].min()
    assert uv.select(panel, early).is_empty()


def test_selection_uses_only_past_data():
    """A turnover collapse must not retroactively remove earlier membership.

    The trailing median at an earlier date cannot see the later crash.
    """
    start = date(2024, 1, 1)
    rows = [(start + timedelta(days=i), "A", 1e9) for i in range(25)]
    rows += [(start + timedelta(days=25 + i), "A", 1.0) for i in range(25)]
    rows += [(start + timedelta(days=i), "B", 1e8) for i in range(50)]
    panel = uv.liquidity(build(rows))
    assert "A" in uv.select(panel, start + timedelta(days=24))["symbol"].to_list()


def test_exclude_drops_etfs():
    """ETFs share SERIES == 'EQ' with ordinary shares, so nothing in the
    bhavcopy distinguishes them -- they must be excluded by name (§10.8)."""
    panel = uv.liquidity(dense({"RELIANCE": 1e9, "NIFTYBEES": 5e8}))
    picked = uv.select(panel, date(2024, 1, 25), exclude={"NIFTYBEES"})
    assert picked["symbol"].to_list() == ["RELIANCE"]


def test_delisted_name_keeps_historical_membership():
    """The whole point of a date-organised archive: a name that later dies must
    still appear in the universe for the period it genuinely qualified."""
    start = date(2024, 1, 1)
    rows = [(start + timedelta(days=i), "DOOMED", 1e9) for i in range(25)]
    rows += [(start + timedelta(days=i), "SURVIVOR", 1e8) for i in range(60)]
    panel = uv.liquidity(build(rows))
    picked = uv.select(panel, start + timedelta(days=24))
    assert "DOOMED" in picked["symbol"].to_list()


def test_rebalance_dates_are_month_ends():
    start = date(2024, 1, 1)
    panel = build(
        [(start + timedelta(days=i), "A", 1e8) for i in range(75)]
    )
    days = uv.rebalance_dates(panel)
    assert days[0] == date(2024, 1, 31)
    assert days[1] == date(2024, 2, 29)  # 2024 is a leap year


def test_membership_spans_rebalances():
    panel = build(
        [
            (date(2024, 1, 1) + timedelta(days=i), sym, 1e8 + n)
            for i in range(75)
            for n, sym in enumerate(("A", "B", "C"))
        ]
    )
    mem = uv.membership(panel, size=2)
    assert mem["date"].n_unique() >= 2
    assert set(mem["symbol"].unique()) <= {"A", "B", "C"}
    # Only the top 2 by turnover per rebalance.
    assert mem.group_by("date").len()["len"].max() == 2


def test_select_requires_liquidity_first():
    with pytest.raises(ValueError, match="liquidity"):
        uv.select(dense({"A": 1e8}), date(2024, 1, 25))
