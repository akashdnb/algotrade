#!/usr/bin/env python
"""Decompose momentum's 951%/yr turnover and sweep portfolio breadth.

A DIAGNOSTIC, not a set of hypothesis tests -- but §8.2's argument is that the
honest denominator includes unregistered exploration, so the config count is
appended to the trial log rather than left off the books.

    python scripts/diagnose_turnover.py
"""
from __future__ import annotations
import json, sys
from datetime import date
from pathlib import Path
import polars as pl

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from algotrade.backtest import engine, metrics  # noqa: E402
from algotrade.core import costs, features, portfolio as pf  # noqa: E402
from algotrade.core import strategies as st, universe as uv  # noqa: E402
import importlib  # noqa: E402
rh = importlib.import_module("scripts.run_hypothesis")

DEV_END = date(2021, 12, 31)
card = costs.load_rate_cards(REPO / "config/costs.yaml")["delivery"]
slip = costs.load_slippage(REPO / "config/costs.yaml")
free = costs.RateCard("free", 0, None, 0, 0, 0, 0, 0, 0, 0)

print("loading ...", flush=True)
panel = rh.load_adjusted_panel()
liq = uv.liquidity(panel)
members = uv.membership(liq, exclude=set(uv.DEFAULT_EXCLUSIONS))
feat = features.add(liq, "momentum", lookback=252, skip=21)
elig = st.restrict_to_universe(feat, members)


def build(n_side: int, *, cap: float | None, cadence: str = "monthly"):
    w = st.cross_sectional(elig, n_side=n_side, long_side="high", gross=1.0)
    if cap is not None:
        w = pf.cap_weights(w, max_weight=cap)
    return pf.resample(w, cadence).filter(pl.col("date") <= DEV_END)


def run(w, *, costed=True, capital=200_000):
    r = engine.run(liq, w, capital=capital, card=card if costed else free,
                   slippage=slip if costed else None, tier="mid",
                   trade_dates=engine.implied_trade_dates(w))
    if r.equity.is_empty():
        return None, 0.0
    m = metrics.compute(r.equity, risk_free=0.0)
    return m, float(r.equity["turnover"].mean()) * 250


configs = 0
print("\n=== 1. is the cap driving turnover, or the signal? ===")
print(f"{'config':<34}{'turnover':>10}{'gross CAGR':>12}{'net CAGR':>10}{'net SR':>8}")
for label, cap in (("n=15, cap 0.15 (as run)", 0.15),
                   ("n=15, NO cap", None)):
    w = build(15, cap=cap); configs += 1
    g, t = run(w, costed=False)
    n, _ = run(w, costed=True)
    print(f"{label:<34}{t:>9.0%}{g.cagr:>12.1%}{n.cagr:>10.1%}{n.sharpe:>8.2f}")

print("\n=== 2. breadth sweep (cap scaled to 2x equal weight) ===")
print(f"{'n_side':<10}{'names':>7}{'turnover':>10}{'gross SR':>10}"
      f"{'net CAGR':>10}{'net SR':>8}{'maxDD':>9}")
for n_side in (15, 30, 50, 75):
    w = build(n_side, cap=min(0.15, 2.0 / (2 * n_side))); configs += 1
    g, t = run(w, costed=False)
    n, _ = run(w, costed=True)
    if g is None:
        # cross_sectional requires 4*n_side eligible names so the two tails stay
        # tails; with a 300-name universe that caps n_side at ~75.
        print(f"{n_side:<10}{2*n_side:>7}   skipped: needs {4*n_side} eligible "
              f"names, universe is 300")
        continue
    print(f"{n_side:<10}{2*n_side:>7}{t:>9.0%}{g.sharpe:>10.2f}"
          f"{n.cagr:>10.1%}{n.sharpe:>8.2f}{n.max_drawdown:>9.1%}")

print("\n=== 3. is this signal-constrained or CAPITAL-constrained? ===")
print("If net converges on gross as capital rises, the binding constraint is the")
print("flat DP charge per scrip (§3), not the signal.")
print(f"{'capital':<12}{'n_side':>8}{'net CAGR':>10}{'net SR':>8}{'gross SR':>10}{'gap':>8}")
for capital in (200_000, 1_000_000, 5_000_000, 20_000_000):
    w = build(50, cap=0.04); configs += 1
    g, _ = run(w, costed=False, capital=capital)
    n, _ = run(w, costed=True, capital=capital)
    if g is None or n is None:
        continue
    print(f"Rs {capital/100000:>5.1f}L{'':<3}{50:>8}{n.cagr:>10.1%}"
          f"{n.sharpe:>8.2f}{g.sharpe:>10.2f}{g.sharpe-n.sharpe:>8.2f}")

log = REPO / "config" / "trials.jsonl"
with log.open("a", encoding="utf-8") as fh:
    fh.write(json.dumps({
        "kind": "exploration",
        "note": "turnover decomposition + breadth sweep, momentum 12-1, dev only",
        "configs_explored": configs,
        "registered_as_trials": 0,
        "why": "diagnostic, not hypothesis tests -- but §8.2's honest denominator "
               "includes unregistered exploration, so the count is on the books",
    }, sort_keys=True) + "\n")
print(f"\n{configs} configs explored; recorded in config/trials.jsonl as exploration")
