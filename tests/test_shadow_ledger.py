"""Tests for the shadow-portfolio paper ledger (shadow_full vs benchmark, §9)."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from algotrade.core import costs
from algotrade.shadow import ledger

CONFIG = Path(__file__).parent.parent / "config" / "costs.yaml"
D = date


@pytest.fixture(scope="module")
def card() -> costs.RateCard:
    return costs.load_rate_cards(CONFIG)["delivery"]


def _dates(n: int) -> list:
    return [D(2026, 1, 1) + timedelta(days=i) for i in range(n)]


def _panel(days: list, prices: dict) -> pl.DataFrame:
    """prices: {symbol: [open per day]}."""
    rows = [
        {"date": d, "symbol": sym, "open": p}
        for sym, opens in prices.items()
        for d, p in zip(days, opens)
    ]
    return pl.DataFrame(rows)


def test_shadow_full_curve_is_a_date_nav_return_series(card):
    days = _dates(5)
    panel = _panel(days, {"AAA": [100, 101, 102, 103, 104]})
    weights = pl.DataFrame(
        {"date": days, "symbol": ["AAA"] * 5, "weight": [1.0] * 5}
    )
    curve = ledger.shadow_full_curve(weights, panel, capital=100_000.0, card=card)
    assert set(curve.columns) == {"date", "nav", "net_return"}
    assert curve.height > 0
    assert curve["nav"].dtype == pl.Float64


def test_shadow_full_curve_is_empty_for_empty_weights(card):
    panel = _panel(_dates(3), {"AAA": [100, 101, 102]})
    weights = pl.DataFrame(schema={"date": pl.Date, "symbol": pl.String, "weight": pl.Float64})
    curve = ledger.shadow_full_curve(weights, panel, capital=100_000.0, card=card)
    assert curve.is_empty()
    assert set(curve.columns) == {"date", "nav", "net_return"}


def test_benchmark_curve_compounds_from_capital():
    rets = pl.DataFrame(
        {"date": _dates(3), "ret": [0.0, 0.10, -0.10]}
    )
    curve = ledger.benchmark_curve(rets, capital=100_000.0)
    assert curve["nav"].to_list() == pytest.approx([100_000.0, 110_000.0, 99_000.0])


def test_benchmark_curve_drops_null_returns():
    rets = pl.DataFrame(
        {"date": _dates(3), "ret": [None, 0.10, -0.10]}
    )
    curve = ledger.benchmark_curve(rets, capital=100_000.0)
    assert curve.height == 2


def test_benchmark_curve_is_empty_for_empty_input():
    rets = pl.DataFrame(schema={"date": pl.Date, "ret": pl.Float64})
    curve = ledger.benchmark_curve(rets, capital=100_000.0)
    assert curve.is_empty()
    assert set(curve.columns) == {"date", "nav"}


def test_build_ledger_joins_on_common_dates_only():
    shadow = pl.DataFrame(
        {"date": _dates(3), "nav": [100.0, 101.0, 102.0], "net_return": [0.0, 0.01, 0.0099]}
    )
    benchmark = pl.DataFrame(
        {"date": _dates(4), "nav": [100.0, 105.0, 106.0, 107.0]}
    )
    out = ledger.build_ledger(shadow, benchmark)
    assert out["date"].to_list() == _dates(3)
    assert out["shadow_full_nav"].to_list() == [100.0, 101.0, 102.0]
    assert out["benchmark_nav"].to_list() == [100.0, 105.0, 106.0]
