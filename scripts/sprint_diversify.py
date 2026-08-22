#!/usr/bin/env python
"""Can momentum-crash risk be DIVERSIFIED rather than timed? (§11g, R1/R4)

§11g established that momentum's -54% drawdown is factor-specific: four of the
five worst episodes were momentum crashes, 2-3x the market's decline in windows
where the index barely moved. No implementation lever fixed it without pushing
returns below the index.

This asks the other question: is there an economically distinct return stream
that *rises* when momentum crashes?

**Two of the five requested pairings are not computable and are not faked.**
Quality needs margins, leverage and earnings stability; Value needs per-stock
P/E or P/B. We have neither -- the index files carry P/E per *index*, not per
name. `core/features.py` already records why no price-only proxy is honest
enough to call quality.

Substituted instead: **long-term reversal**, which is what the theory actually
points at. A momentum crash *is* a reversal event -- beaten-down losers rallying
violently -- so a contrarian signal is the diversifier with a mechanism, not
merely a low correlation. It is also the standard price-only stand-in for value.

The load-bearing measurement is **conditional**, not average: a stream that is
uncorrelated on average but still falls during momentum's crash windows
diversifies nothing.

    python scripts/sprint_diversify.py
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

print("loading ...", flush=True)
PANEL = rh.load_adjusted_panel()
LIQ = uv.liquidity(PANEL)
MEMBERS = uv.membership(LIQ, exclude=set(uv.DEFAULT_EXCLUSIONS))
BENCH = indices.benchmark_series(REPO / "data/raw/indices")


def book(kind: str, *, long_side: str, **params) -> pl.DataFrame:
    elig = st.restrict_to_universe(features.add(LIQ, kind, **params), MEMBERS)
    return buf.buffered_long_only(
        elig, entry=ENTRY, exit_=EXIT, cadence=CADENCE,
        descending=(long_side == "high"),
    )


def curve(weights: pl.DataFrame) -> pl.DataFrame | None:
    w = weights.filter(pl.col("date") <= DEV_END)
    if w.is_empty():
        return None
    r = engine.run(weights=w, panel=PANEL, capital=CAPITAL, card=CARD,
                   slippage=SLIP, tier="mid",
                   trade_dates=engine.implied_trade_dates(w))
    return None if r.equity.is_empty() else r.equity


def stats(eq: pl.DataFrame, label: str) -> dict:
    m = metrics.compute(eq, risk_free=0.0)
    b = metrics.vs_benchmark_series(eq, BENCH)
    return {
        "label": label, "cagr": m.cagr, "sharpe": m.sharpe, "vol": m.volatility,
        "maxdd": m.max_drawdown, "beta": b.get("beta", float("nan")),
    }


def show(title: str, rows: list[dict]) -> None:
    print(f"\n=== {title} ===")
    print(f"{'stream':<34}{'CAGR':>8}{'vol':>7}{'SR':>7}{'maxDD':>9}{'beta':>7}")
    for r in rows:
        print(f"{r['label']:<34}{r['cagr']:>8.1%}{r['vol']:>7.1%}"
              f"{r['sharpe']:>7.2f}{r['maxdd']:>9.1%}{r['beta']:>7.2f}")


# --- the components ----------------------------------------------------------
components: dict[str, pl.DataFrame] = {}

mom = curve(book("momentum", long_side="high", lookback=252, skip=21))
components["momentum"] = mom

lowvol = curve(book("realised_vol", long_side="low", lookback=60))
components["low volatility"] = lowvol

# Long-term reversal: return from 3y ago to 1y ago, long the WORST. The 1-year
# skip is essential -- without it this is just inverted momentum.
ltr = curve(book("momentum", long_side="low", lookback=504, skip=252))
components["long-term reversal"] = ltr

# NIFTY buy-and-hold, as an investable stream rather than a benchmark.
nifty = BENCH.drop_nulls("ret").sort("date").filter(pl.col("date") <= DEV_END)
nifty_eq = nifty.select(
    "date", pl.col("date").alias("exit_date"),
    (CAPITAL * (1 + pl.col("ret")).cum_prod()).alias("capital"),
    pl.col("ret").alias("gross_return"), pl.col("ret").alias("net_return"),
    pl.lit(0.0).alias("cost"), pl.lit(0.0).alias("turnover"),
)
components["NIFTY buy-and-hold"] = nifty_eq

show("components, standalone", [stats(v, k) for k, v in components.items() if v is not None])


# --- correlation, unconditional AND during momentum crashes ------------------
mom_eq = mom.sort("date").with_columns(
    (pl.col("capital") / pl.col("capital").cum_max() - 1).alias("dd")
)
# The four factor-specific crash episodes identified in §11g.
CRASHES = [
    (date(2013, 1, 29), date(2014, 5, 29)),
    (date(2016, 1, 5), date(2016, 10, 20)),
    (date(2016, 10, 28), date(2017, 2, 1)),
    (date(2011, 11, 8), date(2012, 3, 29)),
]
crash_mask = pl.any_horizontal(
    [pl.col("date").is_between(a, b) for a, b in CRASHES]
)

# Align on the REALISATION date. A momentum row labelled D carries the
# open(D+1)->open(D+2) return, so joining streams on D misaligns them by ~2
# sessions -- which collapsed momentum's correlation with NIFTY to 0.02 for a
# book with beta 1.08, and gave a 50/50 momentum+cash blend beta 0.01 where 0.54
# is arithmetically required.
rets = None
for name, eq in components.items():
    if eq is None:
        continue
    key = "exit_date" if "exit_date" in eq.columns else "date"
    s = eq.select(pl.col(key).alias("date"), pl.col("net_return").alias(name)).drop_nulls("date")
    rets = s if rets is None else rets.join(s, on="date", how="inner")

print("\n=== correlation with momentum ===")
print(f"{'stream':<34}{'all periods':>13}{'during crashes':>16}{'crash CAGR':>13}")
for name in list(components):
    if name == "momentum" or name not in rets.columns:
        continue
    full = float(rets.select(pl.corr("momentum", name)).item() or 0.0)
    sub = rets.filter(crash_mask)
    cond = float(sub.select(pl.corr("momentum", name)).item() or 0.0)
    # Annualised return of the stream restricted to momentum's crash windows.
    n = sub.height
    comp = float((1 + sub[name]).product()) if n else 1.0
    cagr = comp ** (250 / max(n, 1)) - 1
    print(f"{name:<34}{full:>13.2f}{cond:>16.2f}{cagr:>13.1%}")

mom_crash = rets.filter(crash_mask)
mc = float((1 + mom_crash["momentum"]).product()) ** (250 / max(mom_crash.height, 1)) - 1
print(f"{'momentum (for reference)':<34}{1.00:>13.2f}{1.00:>16.2f}{mc:>13.1%}")


# --- blends, 50/50, no leverage ----------------------------------------------
def blend(a: str, b: str, wa: float = 0.5) -> dict | None:
    if a not in rets.columns or b not in rets.columns:
        return None
    j = rets.select("date", a, b).drop_nulls()
    mix = j.with_columns((wa * pl.col(a) + (1 - wa) * pl.col(b)).alias("net_return"))
    caps, c = [], CAPITAL
    for r in mix["net_return"].to_list():
        c *= 1 + r
        caps.append(c)
    # rets["date"] is ALREADY the realisation date, so exit_date == date here.
    eq = mix.select("date", pl.col("date").alias("exit_date"), "net_return").with_columns(
        pl.Series("capital", caps), pl.col("net_return").alias("gross_return"),
        pl.lit(0.0).alias("cost"), pl.lit(0.0).alias("turnover"),
    )
    out = stats(eq, f"50/50 momentum + {b}")
    sub = mix.filter(crash_mask)
    n = sub.height
    out["crash"] = (float((1 + sub["net_return"]).product()) ** (250 / max(n, 1)) - 1)
    return out


rows = []
solo = stats(mom, "momentum alone")
solo["crash"] = mc
rows.append(solo)
for other in ("low volatility", "long-term reversal", "NIFTY buy-and-hold"):
    r = blend("momentum", other)
    if r:
        rows.append(r)

# Cash overlay: 50% invested, 50% idle. No leverage, no second signal.
half = rets.select("date", (0.5 * pl.col("momentum")).alias("net_return"))
caps, c = [], CAPITAL
for r in half["net_return"].to_list():
    c *= 1 + r
    caps.append(c)
cash_eq = half.select("date", pl.col("date").alias("exit_date"), "net_return").with_columns(
    pl.Series("capital", caps), pl.col("net_return").alias("gross_return"),
    pl.lit(0.0).alias("cost"), pl.lit(0.0).alias("turnover"),
)
cash = stats(cash_eq, "50/50 momentum + cash")
sub = half.filter(crash_mask)
cash["crash"] = float((1 + sub["net_return"]).product()) ** (250 / max(sub.height, 1)) - 1
rows.append(cash)

print("\n=== blends (equal weight, no leverage) ===")
print(f"{'portfolio':<34}{'CAGR':>8}{'vol':>7}{'SR':>7}{'maxDD':>9}"
      f"{'beta':>7}{'crash CAGR':>12}")
for r in rows:
    print(f"{r['label']:<34}{r['cagr']:>8.1%}{r['vol']:>7.1%}{r['sharpe']:>7.2f}"
          f"{r['maxdd']:>9.1%}{r['beta']:>7.2f}{r.get('crash', float('nan')):>12.1%}")

with (REPO / "config" / "trials.jsonl").open("a", encoding="utf-8") as fh:
    fh.write(json.dumps({
        "kind": "exploration",
        "note": "diversification of momentum-crash risk: low-vol, long-term "
                "reversal, NIFTY, cash. Quality and Value not computable.",
        "configs_explored": 8,
        "registered_as_trials": 0,
    }, sort_keys=True) + "\n")
print("\n8 configs explored; recorded as exploration in trials.jsonl")
