"""Tests for the backtest engine, above all its fill timing (PROJECT.md §10.5)."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from algotrade.backtest import engine, metrics
from algotrade.core import costs

CONFIG = Path(__file__).parent.parent / "config" / "costs.yaml"
D = date


@pytest.fixture(scope="module")
def card() -> costs.RateCard:
    return costs.load_rate_cards(CONFIG)["delivery"]


@pytest.fixture(scope="module")
def free_card() -> costs.RateCard:
    """A zero-cost card, to isolate return mechanics from cost mechanics."""
    return costs.RateCard("free", 0, None, 0, 0, 0, 0, 0, 0, 0)


def panel(rows: list[tuple[date, str, float, float]]) -> pl.DataFrame:
    """(date, symbol, open, close) -> panel."""
    return pl.DataFrame(
        rows,
        schema={
            "date": pl.Date,
            "symbol": pl.String,
            "open": pl.Float64,
            "close": pl.Float64,
        },
        orient="row",
    )


def weights(rows: list[tuple[date, str, float]]) -> pl.DataFrame:
    return pl.DataFrame(
        rows,
        schema={"date": pl.Date, "symbol": pl.String, "weight": pl.Float64},
        orient="row",
    )


# --- fill timing: the thing that matters most --------------------------------


def test_return_is_measured_open_to_open(free_card):
    """A weight from close(D) is filled at open(D+1) and marked at open(D+2)."""
    p = panel(
        [
            (D(2024, 1, 1), "A", 100.0, 100.0),
            (D(2024, 1, 2), "A", 110.0, 130.0),  # fill here
            (D(2024, 1, 3), "A", 121.0, 150.0),  # exit here
        ]
    )
    res = engine.run(
        p, weights([(D(2024, 1, 1), "A", 1.0)]), capital=100_000, card=free_card
    )
    # 121 / 110 - 1 = 10%. The closes are decoys.
    assert res.equity["gross_return"][0] == pytest.approx(0.10)


def test_overnight_gap_is_not_captured(free_card):
    """The correction that makes reversal backtests honest (§10.5).

    A signal from close(D) cannot capture the gap into open(D+1) -- it receives
    it. Here the whole move happens in that gap and the strategy earns nothing,
    which is exactly right. Filling at close(D) would have booked +20%.
    """
    p = panel(
        [
            (D(2024, 1, 1), "A", 100.0, 100.0),
            (D(2024, 1, 2), "A", 120.0, 120.0),  # gapped up 20% overnight
            (D(2024, 1, 3), "A", 120.0, 120.0),
        ]
    )
    res = engine.run(
        p, weights([(D(2024, 1, 1), "A", 1.0)]), capital=100_000, card=free_card
    )
    assert res.equity["gross_return"][0] == pytest.approx(0.0)


def test_unfillable_weights_are_dropped(free_card):
    """The final sessions have no next open, so those weights cannot trade."""
    p = panel([(D(2024, 1, 1), "A", 100.0, 100.0), (D(2024, 1, 2), "A", 100.0, 100.0)])
    res = engine.run(
        p,
        weights([(D(2024, 1, 1), "A", 1.0), (D(2024, 1, 2), "A", 1.0)]),
        capital=100_000,
        card=free_card,
    )
    assert res.equity.is_empty()


# --- P&L and sizing ----------------------------------------------------------


def test_long_short_returns_net_off(free_card):
    p = panel(
        [
            (D(2024, 1, 1), "UP", 100.0, 100.0),
            (D(2024, 1, 2), "UP", 100.0, 100.0),
            (D(2024, 1, 3), "UP", 110.0, 110.0),
            (D(2024, 1, 1), "DOWN", 100.0, 100.0),
            (D(2024, 1, 2), "DOWN", 100.0, 100.0),
            (D(2024, 1, 3), "DOWN", 90.0, 90.0),
        ]
    )
    res = engine.run(
        p,
        weights([(D(2024, 1, 1), "UP", 0.5), (D(2024, 1, 1), "DOWN", -0.5)]),
        capital=100_000,
        card=free_card,
    )
    # +10% on half long, -10% on half short = +10% combined.
    assert res.equity["gross_return"][0] == pytest.approx(0.10)


def test_capital_compounds(free_card):
    days = [D(2024, 1, 1) + timedelta(days=i) for i in range(5)]
    p = panel([(d, "A", 100.0 * (1.1**i), 100.0) for i, d in enumerate(days)])
    res = engine.run(
        p,
        weights([(d, "A", 1.0) for d in days]),
        capital=100_000,
        card=free_card,
    )
    caps = res.equity["capital"].to_list()
    assert caps[1] > caps[0] > 100_000


def test_empty_weights_returns_empty(free_card):
    res = engine.run(
        panel([(D(2024, 1, 1), "A", 1.0, 1.0)]),
        weights([]),
        capital=100_000,
        card=free_card,
    )
    assert res.equity.is_empty()
    assert res.final_capital == 0.0


# --- costs -------------------------------------------------------------------


def test_costs_reduce_net_below_gross(card):
    p = panel(
        [
            (D(2024, 1, 1), "A", 100.0, 100.0),
            (D(2024, 1, 2), "A", 100.0, 100.0),
            (D(2024, 1, 3), "A", 110.0, 110.0),
        ]
    )
    res = engine.run(
        p, weights([(D(2024, 1, 1), "A", 1.0)]), capital=100_000, card=card
    )
    row = res.equity.row(0, named=True)
    assert row["gross_return"] == pytest.approx(0.10)
    assert row["net_return"] < row["gross_return"]
    assert row["cost"] > 0


def test_holding_a_position_incurs_no_new_charges(card):
    """Only the traded delta is charged, so an unchanged weight is free.

    This is what makes low turnover the structural retail advantage (§3).
    """
    days = [D(2024, 1, 1) + timedelta(days=i) for i in range(4)]
    p = panel([(d, "A", 100.0, 100.0) for d in days])
    res = engine.run(
        p, weights([(d, "A", 1.0) for d in days]), capital=100_000, card=card
    )
    costs_by_day = res.equity["cost"].to_list()
    assert costs_by_day[0] > 0  # establishing the position
    assert all(c == pytest.approx(0.0) for c in costs_by_day[1:])


def test_blotter_records_each_trade(card):
    p = panel(
        [
            (D(2024, 1, 1), "A", 100.0, 100.0),
            (D(2024, 1, 2), "A", 100.0, 100.0),
            (D(2024, 1, 3), "A", 100.0, 100.0),
        ]
    )
    res = engine.run(
        p, weights([(D(2024, 1, 1), "A", 1.0)]), capital=100_000, card=card
    )
    assert res.blotter.height == 1
    row = res.blotter.row(0, named=True)
    assert row["side"] == "buy"
    assert row["value"] == pytest.approx(100_000)


def test_slippage_is_optional_and_additive(card):
    p = panel(
        [
            (D(2024, 1, 1), "A", 100.0, 100.0),
            (D(2024, 1, 2), "A", 100.0, 100.0),
            (D(2024, 1, 3), "A", 100.0, 100.0),
        ]
    ).with_columns(pl.lit(1e9).alias("adtv"))
    args = dict(capital=100_000, card=card)
    w = weights([(D(2024, 1, 1), "A", 1.0)])
    bare = engine.run(p, w, **args).equity["cost"][0]
    slipped = engine.run(
        p, w, slippage=costs.load_slippage(CONFIG), **args
    ).equity["cost"][0]
    assert slipped > bare


# --- metrics -----------------------------------------------------------------


def test_metrics_flag_implausible_sharpe():
    """Above ~2.5 in sample, treat it as a bug report (§8.1)."""
    equity = pl.DataFrame(
        {
            "date": [D(2024, 1, 1) + timedelta(days=i) for i in range(250)],
            "capital": [100_000 * (1.001**i) for i in range(250)],
            "gross_return": [0.001] * 250,
            "net_return": [0.001] * 250,
            "cost": [0.0] * 250,
            "turnover": [0.0] * 250,
        }
    )
    m = metrics.compute(equity)
    assert m.implausible  # near-zero vol with positive drift
    assert "IMPLAUSIBLE" in m.summary()


def test_max_drawdown_is_negative():
    caps = pl.Series([100.0, 120.0, 90.0, 110.0])
    assert metrics.max_drawdown(caps) == pytest.approx(-0.25)


def test_trade_concentration_detects_luck():
    """Five great days carrying an otherwise flat strategy must be exposed."""
    net = [0.0] * 100 + [0.5] * 5
    equity = pl.DataFrame(
        {
            "net_return": net,
            "capital": [100.0] * len(net),
            "gross_return": net,
            "turnover": [0.0] * len(net),
        }
    )
    got = metrics.trade_concentration(equity, drop=5)
    assert got["full"] > 5.0
    assert got["excluding_best"] == pytest.approx(0.0)
    assert not got["survives"]


def test_benchmark_comparison_uses_niftybees():
    equity = pl.DataFrame(
        {
            "date": [D(2024, 1, 1) + timedelta(days=i) for i in range(250)],
            "capital": [100_000 * (1.0002**i) for i in range(250)],
            "gross_return": [0.0002] * 250,
            "net_return": [0.0002] * 250,
            "cost": [0.0] * 250,
            "turnover": [0.0] * 250,
        }
    )
    got = metrics.vs_benchmark(equity)
    assert got["benchmark_cagr"] == pytest.approx(0.12)
    assert got["beats_benchmark"] is (got["cagr"] > 0.12)


def test_metrics_on_empty_equity():
    m = metrics.compute(pl.DataFrame())
    assert m.periods == 0
    assert not m.implausible


# --- gate_inputs: closing the convention gap --------------------------------


def _equity(net: list[float]) -> pl.DataFrame:
    caps, c = [], 100_000.0
    for r in net:
        c *= 1 + r
        caps.append(c)
    return pl.DataFrame(
        {
            "date": [D(2024, 1, 1) + timedelta(days=i) for i in range(len(net))],
            "capital": caps,
            "gross_return": net,
            "net_return": net,
            "cost": [0.0] * len(net),
            "turnover": [0.0] * len(net),
        }
    )


def test_gate_inputs_supplies_every_key_the_gates_need():
    """Regression: rolling_3m_positive was required by the gates and computed
    nowhere, so dict.get(..., 0.0) made that gate silently always fail."""
    from algotrade.backtest import registry as reg

    got = metrics.gate_inputs(_equity([0.001] * 200))
    checked = reg.check_gates(got, reg.Gates())
    # Every gate must be evaluable, i.e. no key silently defaulted.
    assert set(checked) == {
        "oos_sharpe",
        "max_drawdown",
        "rolling_3m_positive",
        "beats_benchmark",
        "survives_concentration",
        "not_implausible",
    }
    assert got["rolling_3m_positive"] > 0.0


def test_rolling_window_positive_on_a_steady_riser():
    assert metrics.rolling_window_positive(_equity([0.001] * 200)) == pytest.approx(1.0)


def test_rolling_window_positive_on_a_steady_faller():
    assert metrics.rolling_window_positive(_equity([-0.001] * 200)) == pytest.approx(0.0)


def test_rolling_window_positive_needs_a_full_window():
    """A backtest shorter than one window must not accidentally pass."""
    assert metrics.rolling_window_positive(_equity([0.01] * 10), window=63) == 0.0


def test_rolling_window_catches_lumpy_returns():
    """Consistency, not total return: two great quarters then a long bleed."""
    lumpy = [0.02] * 63 + [-0.0015] * 189
    steady = [0.001] * 252
    assert metrics.rolling_window_positive(_equity(lumpy)) < metrics.rolling_window_positive(
        _equity(steady)
    )


# --- measured benchmark and beta (§11d) --------------------------------------


def _bench(rets: list[float], start: date = D(2024, 1, 1)) -> pl.DataFrame:
    return pl.DataFrame(
        {"date": [start + timedelta(days=i) for i in range(len(rets))], "ret": rets}
    )


def test_measured_benchmark_beats_assumption_when_index_did_better():
    """The assumed 0.12 can flatter or flatter-not depending on the window."""
    eq = _equity([0.0004] * 250)          # ~10.5%/yr
    strong = _bench([0.0008] * 250)       # ~22%/yr
    weak = _bench([0.0001] * 250)         # ~2.5%/yr
    assert not metrics.vs_benchmark_series(eq, strong)["beats_benchmark"]
    assert metrics.vs_benchmark_series(eq, weak)["beats_benchmark"]


def test_benchmark_uses_only_overlapping_dates():
    eq = _equity([0.001] * 100)
    short = _bench([0.001] * 10)
    got = metrics.vs_benchmark_series(eq, short)
    assert got["overlapping_periods"] == 10


def test_beta_is_measured():
    """A nominally market-neutral book with real beta is not neutral.

    A large drawdown on a neutral strategy is usually this, not bad luck (§11d).
    """
    market = [0.01, -0.01] * 100
    levered = _equity([2 * r for r in market])
    got = metrics.vs_benchmark_series(levered, _bench(market))
    assert got["beta"] == pytest.approx(2.0, rel=0.05)
    assert got["correlation"] == pytest.approx(1.0, abs=0.01)


def test_market_neutral_book_shows_near_zero_beta():
    market = [0.01, -0.01, 0.005, -0.005] * 50
    flat = _equity([0.0002] * len(market))
    got = metrics.vs_benchmark_series(flat, _bench(market))
    assert abs(got["beta"]) < 0.1


def test_no_overlap_reports_rather_than_crashes():
    got = metrics.vs_benchmark_series(
        _equity([0.001] * 10), _bench([0.001] * 10, start=D(2030, 1, 1))
    )
    assert got["beats_benchmark"] is False
    assert got["overlapping_periods"] == 0
