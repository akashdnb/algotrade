#!/usr/bin/env python
"""Implementation sprint for 12-1 momentum -- six priorities, one report.

Not factor discovery. The factor is established (gross Sharpe ~0.97-1.24); the
question is whether its *implementation* can be made investable at retail scale:
net Sharpe >= 0.80, max drawdown <= 25%.

Everything here is DIAGNOSTIC. Nothing is registered as a hypothesis, but the
config count is appended to the trial log because §8.2's honest denominator
includes unregistered exploration.

    python scripts/sprint_momentum.py
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import polars as pl

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from algotrade.backtest import engine, metrics  # noqa: E402
from algotrade.core import buffering as buf  # noqa: E402
from algotrade.core import costs, features  # noqa: E402
from algotrade.core import portfolio as pf, strategies as st, universe as uv  # noqa: E402
from algotrade.io import indices  # noqa: E402

import importlib  # noqa: E402

rh = importlib.import_module("scripts.run_hypothesis")

DEV_END = date(2021, 12, 31)
CAPITAL = 200_000.0

CARD = costs.load_rate_cards(REPO / "config/costs.yaml")["delivery"]
SLIP = costs.load_slippage(REPO / "config/costs.yaml")
FREE = costs.RateCard("free", 0, None, 0, 0, 0, 0, 0, 0, 0)

configs = 0


@dataclass
class Row:
    label: str
    cagr: float
    sharpe: float
    vol: float
    maxdd: float
    turnover: float
    beta: float
    excess: float
    gross_sharpe: float


def evaluate(label: str, weights: pl.DataFrame, bench: pl.DataFrame,
             *, capital: float = CAPITAL) -> Row | None:
    """Run costed and free, and return the metrics the sprint asks for."""
    global configs
    configs += 1
    weights = weights.filter(pl.col("date") <= DEV_END)
    if weights.is_empty():
        return None
    td = engine.implied_trade_dates(weights)
    net = engine.run(weights=weights, panel=PANEL, capital=capital,
                     card=CARD, slippage=SLIP, tier="mid", trade_dates=td)
    if net.equity.is_empty():
        return None
    gross = engine.run(weights=weights, panel=PANEL, capital=capital,
                       card=FREE, slippage=None, tier="mid", trade_dates=td)
    m = metrics.compute(net.equity, risk_free=0.0)
    g = metrics.compute(gross.equity, risk_free=0.0)
    b = metrics.vs_benchmark_series(net.equity, bench)
    return Row(
        label=label,
        cagr=m.cagr,
        sharpe=m.sharpe,
        vol=m.volatility,
        maxdd=m.max_drawdown,
        turnover=float(net.equity["turnover"].mean()) * 250,
        beta=b.get("beta", float("nan")),
        excess=b.get("excess", float("nan")),
        gross_sharpe=g.sharpe,
    )


def table(title: str, rows: list[Row]) -> None:
    print(f"\n=== {title} ===")
    print(f"{'variant':<30}{'CAGR':>8}{'vol':>7}{'net SR':>8}{'gross SR':>9}"
          f"{'maxDD':>9}{'turnover':>10}{'beta':>7}{'vs NIFTY':>10}")
    for r in rows:
        if r is None:
            continue
        print(f"{r.label:<30}{r.cagr:>8.1%}{r.vol:>7.1%}{r.sharpe:>8.2f}"
              f"{r.gross_sharpe:>9.2f}{r.maxdd:>9.1%}{r.turnover:>10.0%}"
              f"{r.beta:>7.2f}{r.excess:>+10.1%}")


print("loading panel ...", flush=True)
PANEL = rh.load_adjusted_panel()
LIQ = uv.liquidity(PANEL)
MEMBERS = uv.membership(LIQ, exclude=set(uv.DEFAULT_EXCLUSIONS))
ELIG = st.restrict_to_universe(
    features.add(LIQ, "momentum", lookback=252, skip=21), MEMBERS
)
BENCH = indices.benchmark_series(REPO / "data/raw/indices")
print(f"  {ELIG.height:,} eligible rows, benchmark {BENCH.height:,} sessions")


# --- P1: where does 951% turnover come from? ---------------------------------
base_ls = pf.resample(
    st.cross_sectional(ELIG, n_side=15, long_side="high"), "monthly"
).filter(pl.col("date") <= DEV_END)
base_run = engine.run(weights=base_ls, panel=PANEL, capital=CAPITAL,
                      card=CARD, slippage=SLIP, tier="mid",
                      trade_dates=engine.implied_trade_dates(base_ls))
print("\n=== P1: turnover attribution (long/short n=15, monthly) ===")
attr = buf.attribute_turnover(base_run.blotter)
print(f"{'kind':<22}{'value (Rs)':>16}{'trades':>9}{'share':>8}")
for r in attr.iter_rows(named=True):
    print(f"{r['kind']:<22}{r['value']:>16,.0f}{r['trades']:>9,}{r['share']:>8.1%}")

sessions = base_run.equity["date"].n_unique()
rebals = base_run.blotter["date"].n_unique()
print(f"\nsessions {sessions:,} | sessions with trades {rebals:,} "
      f"({rebals/sessions:.0%}) | annual turnover "
      f"{float(base_run.equity['turnover'].mean())*250:.0%}")


# --- P6 baseline: long-only, no changes --------------------------------------
lo_base = pf.resample(st.long_only(ELIG, n=30, side="high"), "monthly")
rows = [evaluate("long-only n=30 (baseline)", lo_base, BENCH)]

# --- P2: rank buffering ------------------------------------------------------
buf_rows = [rows[0]]
for entry, exit_ in ((30, 30), (30, 40), (30, 50), (30, 60)):
    label = f"buffer {entry}/{exit_}" + (" (no buffer)" if entry == exit_ else "")
    w = buf.buffered_long_only(ELIG, entry=entry, exit_=exit_, cadence="monthly")
    buf_rows.append(evaluate(label, w, BENCH))
table("P2: rank buffering (long-only, monthly)", buf_rows)

# --- P3: rebalance frequency -------------------------------------------------
freq_rows = []
for cadence in ("monthly", "2monthly", "quarterly"):
    w = buf.buffered_long_only(ELIG, entry=30, exit_=50, cadence=cadence)
    freq_rows.append(evaluate(f"buffer 30/50, {cadence}", w, BENCH))
table("P3: rebalance frequency (buffer 30/50)", freq_rows)

# --- P4: volatility targeting ------------------------------------------------
vol_rows = []
best_w = buf.buffered_long_only(ELIG, entry=30, exit_=50, cadence="quarterly")
_uw = best_w.filter(pl.col("date") <= DEV_END)
unlevered = engine.run(weights=_uw,
                       panel=PANEL, capital=CAPITAL, card=CARD,
                       slippage=SLIP, tier="mid",
                       trade_dates=engine.implied_trade_dates(_uw))
for target in (0.10, 0.12, 0.15):
    lev = pf.vol_scalar(unlevered.equity.select("date", "net_return"),
                        target=target, lookback=60, max_leverage=1.0)
    scaled = pf.scale(best_w, lev)
    vol_rows.append(evaluate(f"vol target {target:.0%}", scaled, BENCH))
table("P4: volatility targeting (buffer 30/50, quarterly, max leverage 1.0)",
      vol_rows)

# --- P5: trend filters -------------------------------------------------------
trend_rows = [freq_rows[-1]]

with_dma = LIQ.sort(["symbol", "date"]).with_columns(
    pl.col("close").rolling_mean(window_size=200).over("symbol").alias("dma200")
)
elig_dma = st.restrict_to_universe(
    features.add(with_dma, "momentum", lookback=252, skip=21), MEMBERS
)
above = elig_dma.filter(pl.col("close") > pl.col("dma200"))
trend_rows.append(evaluate(
    "stock > 200DMA",
    buf.buffered_long_only(above, entry=30, exit_=50, cadence="quarterly"),
    BENCH,
))

# NIFTY regime filter: hold nothing when the index is below its own 200DMA.
regime = BENCH.sort("date").with_columns(
    pl.col("close").rolling_mean(window_size=200).alias("idx_dma")
).with_columns((pl.col("close") > pl.col("idx_dma")).alias("risk_on"))
risk_on_dates = set(
    regime.filter(pl.col("risk_on"))["date"].to_list()
)
w_regime = buf.buffered_long_only(ELIG, entry=30, exit_=50, cadence="quarterly")
w_regime = w_regime.filter(pl.col("date").is_in(list(risk_on_dates)))
trend_rows.append(evaluate("NIFTY > 200DMA (regime)", w_regime, BENCH))

w_both = above.pipe(
    lambda p: buf.buffered_long_only(p, entry=30, exit_=50, cadence="quarterly")
).filter(pl.col("date").is_in(list(risk_on_dates)))
trend_rows.append(evaluate("both filters", w_both, BENCH))
table("P5: trend filters (buffer 30/50, quarterly)", trend_rows)

log = REPO / "config" / "trials.jsonl"
with log.open("a", encoding="utf-8") as fh:
    fh.write(json.dumps({
        "kind": "exploration",
        "note": "momentum implementation sprint: turnover attribution, rank "
                "buffering, cadence, vol targeting, trend filters",
        "configs_explored": configs,
        "registered_as_trials": 0,
        "why": "implementation research on an established factor, not factor "
               "discovery -- but §8.2's denominator counts it",
    }, sort_keys=True) + "\n")
print(f"\n{configs} configs explored; recorded as exploration in trials.jsonl")
