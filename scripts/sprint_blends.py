#!/usr/bin/env python
"""Blend weight sweep, judged on drawdown during momentum's crash episodes.

§11g found momentum's -54% drawdown is factor-specific, and the diversification
sprint found no available stream decorrelates during crashes (everything on
offer is long-only Indian equity sharing beta ~1). This sweeps the weights
anyway, because "does not decorrelate" and "does not help" are different claims
-- two books that crash at different *times* can still blend well even at 0.77
unconditional correlation.

The headline metric is per-episode drawdown, not the average. A blend that
improves overall drawdown by shaving every small dip while leaving the big ones
intact has not solved anything.

    python scripts/sprint_blends.py
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
ENTRY, EXIT, CADENCE = 30, 60, "monthly"

# All five worst episodes from §11g's attribution, with how each was classified.
EPISODES = [
    ("2018-01 COVID span", date(2018, 1, 25), date(2020, 12, 14), "market"),
    ("2013-01 style rot.", date(2013, 1, 29), date(2014, 5, 29), "factor"),
    ("2016-01 style rot.", date(2016, 1, 5), date(2016, 10, 20), "factor"),
    ("2016-10 style rot.", date(2016, 10, 28), date(2017, 2, 1), "factor"),
    ("2011-11 style rot.", date(2011, 11, 8), date(2012, 3, 29), "factor"),
]

print("loading ...", flush=True)
PANEL = rh.load_adjusted_panel()
LIQ = uv.liquidity(PANEL)
MEMBERS = uv.membership(LIQ, exclude=set(uv.DEFAULT_EXCLUSIONS))
BENCH = indices.benchmark_series(REPO / "data/raw/indices")


def stream(kind: str, *, long_side: str, **params):
    """Equity curve and annual turnover for one long-only book."""
    elig = st.restrict_to_universe(features.add(LIQ, kind, **params), MEMBERS)
    w = buf.buffered_long_only(
        elig, entry=ENTRY, exit_=EXIT, cadence=CADENCE,
        descending=(long_side == "high"),
    ).filter(pl.col("date") <= DEV_END)
    r = engine.run(weights=w, panel=PANEL, capital=CAPITAL, card=CARD,
                   slippage=SLIP, tier="mid")
    if r.equity.is_empty():
        return None, 0.0
    return r.equity, float(r.equity["turnover"].mean()) * 250


print("building component streams ...", flush=True)
mom_eq, mom_to = stream("momentum", long_side="high", lookback=252, skip=21)
rev_eq, rev_to = stream("momentum", long_side="low", lookback=504, skip=252)
lv_eq, lv_to = stream("realised_vol", long_side="low", lookback=60)

nifty = BENCH.drop_nulls("ret").filter(pl.col("date") <= DEV_END).sort("date")

# Everything aligned on the REALISATION date -- a momentum row labelled D carries
# the open(D+1)->open(D+2) return, so joining on D misaligns streams by ~2
# sessions and collapses their measured correlation.
def as_series(eq, name: str) -> pl.DataFrame:
    key = "exit_date" if "exit_date" in eq.columns else "date"
    return eq.select(
        pl.col(key).alias("date"), pl.col("net_return").alias(name)
    ).drop_nulls("date")


rets = (
    as_series(mom_eq, "momentum")
    .join(as_series(rev_eq, "reversal"), on="date", how="inner")
    .join(as_series(lv_eq, "lowvol"), on="date", how="inner")
    .join(nifty.select("date", pl.col("ret").alias("nifty")), on="date", how="inner")
    .sort("date")
)
TURN = {"momentum": mom_to, "reversal": rev_to, "lowvol": lv_to, "nifty": 0.0}
print(f"  {rets.height:,} aligned sessions")


def assess(label: str, parts: dict[str, float]) -> dict:
    """parts maps stream name -> weight. Weights must sum to 1."""
    expr = sum(w * pl.col(c) for c, w in parts.items())
    frame = rets.select("date", expr.alias("net_return"))
    caps, c = [], CAPITAL
    for r in frame["net_return"].to_list():
        c *= 1 + r
        caps.append(c)
    eq = frame.with_columns(
        pl.Series("capital", caps),
        pl.col("date").alias("exit_date"),
        pl.col("net_return").alias("gross_return"),
        pl.lit(0.0).alias("cost"), pl.lit(0.0).alias("turnover"),
    )
    m = metrics.compute(eq, risk_free=0.0)
    b = metrics.vs_benchmark_series(eq, BENCH)

    # Per-episode drawdown: worst peak-to-trough WITHIN each window.
    per_ep = []
    for _, lo, hi, _ in EPISODES:
        sub = eq.filter(pl.col("date").is_between(lo, hi)).sort("date")
        if sub.height < 2:
            per_ep.append(float("nan"))
            continue
        peak = sub["capital"].cum_max()
        per_ep.append(float((sub["capital"] / peak - 1).min()))

    return {
        "label": label,
        "cagr": m.cagr, "sharpe": m.sharpe, "vol": m.volatility,
        "maxdd": m.max_drawdown, "beta": b.get("beta", float("nan")),
        # Component turnover, weighted. Holding fixed blend weights adds more on
        # top; this is the floor, not the total.
        "turnover": sum(w * TURN[c] for c, w in parts.items()),
        "episodes": per_ep,
    }


BLENDS = [
    ("100% momentum", {"momentum": 1.0}),
    ("75/25 mom + reversal", {"momentum": 0.75, "reversal": 0.25}),
    ("50/50 mom + reversal", {"momentum": 0.50, "reversal": 0.50}),
    ("25/75 mom + reversal", {"momentum": 0.25, "reversal": 0.75}),
    ("100% reversal", {"reversal": 1.0}),
    ("75/25 mom + lowvol", {"momentum": 0.75, "lowvol": 0.25}),
    ("50/50 mom + lowvol", {"momentum": 0.50, "lowvol": 0.50}),
    ("25/75 mom + lowvol", {"momentum": 0.25, "lowvol": 0.75}),
    ("75/25 mom + NIFTY", {"momentum": 0.75, "nifty": 0.25}),
    ("50/50 mom + NIFTY", {"momentum": 0.50, "nifty": 0.50}),
    ("25/75 mom + NIFTY", {"momentum": 0.25, "nifty": 0.75}),
    ("100% NIFTY", {"nifty": 1.0}),
]
results = [assess(label, parts) for label, parts in BLENDS]

print(f"\n{'blend':<24}{'CAGR':>8}{'vol':>7}{'SR':>7}{'maxDD':>9}"
      f"{'beta':>7}{'turnover':>10}")
for r in results:
    print(f"{r['label']:<24}{r['cagr']:>8.1%}{r['vol']:>7.1%}{r['sharpe']:>7.2f}"
          f"{r['maxdd']:>9.1%}{r['beta']:>7.2f}{r['turnover']:>10.0%}")

print("\n=== THE HEADLINE: drawdown WITHIN each momentum-crash episode ===")
hdr = f"{'blend':<24}"
for name, _, _, kind in EPISODES:
    hdr += f"{name.split()[0]:>12}"
print(hdr + f"{'worst':>9}")
print(f"{'':<24}" + "".join(f"{k:>12}" for _, _, _, k in EPISODES))
for r in results:
    line = f"{r['label']:<24}"
    for d in r["episodes"]:
        line += f"{d:>12.1%}"
    line += f"{min(r['episodes']):>9.1%}"
    print(line)

with (REPO / "config" / "trials.jsonl").open("a", encoding="utf-8") as fh:
    fh.write(json.dumps({
        "kind": "exploration",
        "note": "blend weight sweep judged on per-episode crash drawdown",
        "configs_explored": len(BLENDS),
        "registered_as_trials": 0,
    }, sort_keys=True) + "\n")
print(f"\n{len(BLENDS)} configs explored; recorded as exploration")
