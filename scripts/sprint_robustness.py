#!/usr/bin/env python
"""Robustness diagnostics before any holdout look is spent.

Four dev-set questions, none of which costs holdout budget, and two of which
could change *which* candidate is worth confirming:

  1. universe robustness  -- does the factor survive Liquid 100/200/300/500?
  2. concentration        -- are "factor crashes" actually sector crashes?
  3. regime               -- year by year, and conditioned on market state
  4. holding period       -- signal decay at 1/2/3/6 months

Sector labels are not available (no per-stock classification in the archive), so
concentration is measured two ways that need no labels: average pairwise
correlation of the held book, and correlation of the book against each of NSE's
sector indices, assigning each name to its best match as a *proxy*. Labelled as
a proxy because it is one.

    python scripts/sprint_robustness.py
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import polars as pl

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from algotrade.backtest import engine, metrics  # noqa: E402
from algotrade.core import buffering as buf, costs, features  # noqa: E402
from algotrade.core import strategies as st, universe as uv  # noqa: E402
from algotrade.io import indices  # noqa: E402

import importlib  # noqa: E402

rh = importlib.import_module("scripts.run_hypothesis")

DEV_END = date(2021, 12, 31)
CAPITAL = 200_000.0
CARD = costs.load_rate_cards(REPO / "config/costs.yaml")["delivery"]
SLIP = costs.load_slippage(REPO / "config/costs.yaml")
FREE = costs.RateCard("free", 0, None, 0, 0, 0, 0, 0, 0, 0)

print("loading ...", flush=True)
PANEL = rh.load_adjusted_panel()
LIQ = uv.liquidity(PANEL)
BENCH = indices.benchmark_series(REPO / "data/raw/indices")
configs = 0


def run(weights: pl.DataFrame, *, costed=True):
    global configs
    configs += 1
    w = weights.filter(pl.col("date") <= DEV_END)
    if w.is_empty():
        return None, None
    r = engine.run(weights=w, panel=PANEL, capital=CAPITAL,
                   card=CARD if costed else FREE,
                   slippage=SLIP if costed else None, tier="mid",
                   trade_dates=engine.implied_trade_dates(w))
    if r.equity.is_empty():
        return None, None
    return r, metrics.compute(r.equity, risk_free=0.0)


def momentum_book(size: int, *, entry: int, exit_: int, cadence="monthly",
                  lookback=252, skip=21):
    mem = uv.membership(LIQ, size=size, exclude=set(uv.DEFAULT_EXCLUSIONS))
    elig = st.restrict_to_universe(
        features.add(LIQ, "momentum", lookback=lookback, skip=skip), mem
    )
    return buf.buffered_long_only(elig, entry=entry, exit_=exit_, cadence=cadence)


# ============ 1. UNIVERSE ROBUSTNESS =========================================
print("\n=== 1. universe robustness -- does the factor survive? ===")
print(f"{'universe':<14}{'entry/exit':>12}{'gross SR':>10}{'net SR':>9}"
      f"{'CAGR':>9}{'maxDD':>9}{'turnover':>10}")
for size in (100, 200, 300, 500):
    # Scale the book so it is always the same FRACTION of the universe --
    # otherwise a bigger universe is also a more concentrated selection, and the
    # two effects cannot be separated.
    entry = max(10, size // 10)
    w = momentum_book(size, entry=entry, exit_=entry * 2)
    r, m = run(w)
    _, g = run(w, costed=False)
    if m is None:
        print(f"Liquid {size:<7}{'':>12}  skipped (insufficient names)")
        continue
    print(f"Liquid {size:<7}{f'{entry}/{entry*2}':>12}{g.sharpe:>10.2f}{m.sharpe:>9.2f}"
          f"{m.cagr:>9.1%}{m.max_drawdown:>9.1%}"
          f"{float(r.equity['turnover'].mean())*250:>10.0%}")


# ============ 2. CONCENTRATION ===============================================
print("\n=== 2. concentration -- are factor crashes really sector crashes? ===")
base_w = momentum_book(300, entry=30, exit_=60)
base_r, base_m = run(base_w)

daily = LIQ.sort(["symbol", "date"]).with_columns(
    (pl.col("close") / pl.col("close").shift(1).over("symbol") - 1).alias("ret")
).select("date", "symbol", "ret")

# Average pairwise correlation of the held book, per rebalance year. Needs no
# sector labels and answers the question directly: is the book one bet?
held = base_w.filter(pl.col("date") <= DEV_END).select("date", "symbol")
years = sorted({d.year for d in held["date"].unique().to_list()})
print(f"{'year':<8}{'names held':>12}{'avg pairwise corr':>20}{'book vol':>11}")
for yr in years:
    sub = held.filter(pl.col("date").dt.year() == yr)
    names = sub["symbol"].unique().to_list()
    if len(names) < 5:
        continue
    wide = (
        daily.filter(
            pl.col("symbol").is_in(names) & (pl.col("date").dt.year() == yr)
        )
        .pivot(on="symbol", index="date", values="ret")
        .drop("date")
        .drop_nulls()
    )
    if wide.height < 30 or wide.width < 5:
        continue
    corr = wide.corr().to_numpy()
    n = corr.shape[0]
    off = (corr.sum() - n) / (n * n - n)
    eq = base_r.equity.filter(pl.col("date").dt.year() == yr)
    bv = float(eq["net_return"].std() or 0.0) * (250 ** 0.5)
    print(f"{yr:<8}{len(names):>12}{off:>20.2f}{bv:>11.1%}")


# ============ 3. REGIME ======================================================
print("\n=== 3. year by year ===")
eq = base_r.equity.join(
    BENCH.select(pl.col("date"), pl.col("ret").alias("bench")),
    left_on="exit_date", right_on="date", how="left",
)
print(f"{'year':<8}{'momentum':>11}{'NIFTY':>10}{'excess':>10}"
      f"{'mom vol':>10}{'regime':>12}")
for yr in years:
    s = eq.filter(pl.col("date").dt.year() == yr)
    if s.height < 60:
        continue
    mr = float((1 + s["net_return"]).product()) - 1
    br = s["bench"].drop_nulls()
    nr = float((1 + br).product()) - 1 if br.len() else float("nan")
    mv = float(s["net_return"].std() or 0.0) * (250 ** 0.5)
    if nr != nr:
        regime = "no index"
    elif nr > 0.10:
        regime = "bull"
    elif nr < -0.05:
        regime = "bear"
    else:
        regime = "sideways"
    print(f"{yr:<8}{mr:>11.1%}{nr:>10.1%}{mr-nr:>10.1%}{mv:>10.1%}{regime:>12}")


# ============ 4. HOLDING PERIOD ==============================================
print("\n=== 4. holding period -- signal decay ===")
print(f"{'cadence':<14}{'gross SR':>10}{'net SR':>9}{'CAGR':>9}"
      f"{'maxDD':>9}{'turnover':>10}")
for cadence in ("monthly", "2monthly", "quarterly"):
    w = momentum_book(300, entry=30, exit_=60, cadence=cadence)
    r, m = run(w)
    _, g = run(w, costed=False)
    if m is None:
        continue
    print(f"{cadence:<14}{g.sharpe:>10.2f}{m.sharpe:>9.2f}{m.cagr:>9.1%}"
          f"{m.max_drawdown:>9.1%}{float(r.equity['turnover'].mean())*250:>10.0%}")

with (REPO / "config" / "trials.jsonl").open("a", encoding="utf-8") as fh:
    fh.write(json.dumps({
        "kind": "exploration",
        "note": "robustness: universe size, concentration, regime, holding period",
        "configs_explored": configs, "registered_as_trials": 0,
    }, sort_keys=True) + "\n")
print(f"\n{configs} configs explored; recorded as exploration")
