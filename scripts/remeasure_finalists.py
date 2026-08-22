#!/usr/bin/env python
"""Re-measure the four surviving configurations on the audited engine (§11j).

**Not research. No new factors, no new parameters, no search.** The blend work
in §11g/§11h ran on an engine that re-equalised the book daily between monthly
rebalances, so every net-of-cost number it produced was understated -- and cost
was one of the two reasons the project moved from momentum alone toward blends.
That pillar moved. This re-runs the same four configs, unchanged, and checks
whether the ranking still holds.

The four:
    1. Momentum            -- 12-1, liquid 300, buffer 30/60, monthly
    2. Low Volatility      -- 60d realised vol, long the lowest, same book shape
    3. 25/75 Momentum + Low Vol
    4. 25/75 Momentum + NIFTY

Two things this does that the earlier sweep did not:

**Blends are rebalanced monthly and charged for it.** `sprint_blends.py` blended
the two net-return *series*, which silently rebalances the blend to exact weights
every session at zero cost -- the same modelling error §11j just found in the
engine, one level up. Here each sleeve's capital drifts with its own return and
is corrected monthly, and the correction is charged at the measured blended rate.

**After-tax CAGR is reported.** Every holding is short-term, so gains are taxed
at 20% STCG annually, with losses carried forward. NIFTY buy-and-hold is taxed
once at 12.5% LTCG on exit. That asymmetry is the honest comparison and it has
never been in a results table.

    python scripts/remeasure_finalists.py
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
from algotrade.core import buffering as buf, costs, features  # noqa: E402
from algotrade.core import strategies as st, universe as uv  # noqa: E402
from algotrade.io import indices  # noqa: E402

import importlib  # noqa: E402

rh = importlib.import_module("scripts.run_hypothesis")

DEV_END = date(2021, 12, 31)
CAPITAL = 200_000.0
ENTRY, EXIT, CADENCE = 30, 60, "monthly"
CARD = costs.load_rate_cards(REPO / "config/costs.yaml")["delivery"]
SLIP = costs.load_slippage(REPO / "config/costs.yaml")
FREE = costs.RateCard("free", 0, None, 0, 0, 0, 0, 0, 0, 0)

STCG = 0.20   # short-term capital gains, all equity holdings under 12 months
LTCG = 0.125  # long-term, what a NIFTYBEES holder pays once on exit


@dataclass
class Row:
    label: str
    cagr: float
    sharpe: float
    maxdd: float
    turnover: float
    cost_drag: float
    cost_bps: float
    after_tax_cagr: float
    gross_sharpe: float
    beta: float


# ---------------------------------------------------------------- tax ---------
def after_tax_cagr(daily: pl.DataFrame, *, rate: float = STCG) -> float:
    """CAGR after 20% STCG charged annually, with loss carry-forward.

    Gains are realised continuously by a monthly-rebalanced book, so there is no
    deferral: tax falls due each year on that year's net gain. Losses carry
    forward and offset future gains, which is what the Income Tax Act allows and
    which materially softens the drag after a crash year.

    Taxes are paid *out of the book*, so the next year compounds on the reduced
    base. That compounding is the whole point -- an annual 20% haircut is worth
    far more than 20% of the terminal figure.
    """
    frame = daily.select(
        pl.col("date").dt.year().alias("yr"), pl.col("net_return")
    ).drop_nulls()
    if frame.is_empty():
        return 0.0

    capital, carried = 1.0, 0.0
    for (yr,), g in frame.group_by("yr", maintain_order=True):
        start = capital
        for r in g["net_return"].to_list():
            capital *= 1 + r
        gain = capital - start
        if gain > 0:
            taxable = max(0.0, gain - carried)
            carried = max(0.0, carried - gain)
            capital -= taxable * rate
        else:
            carried += -gain

    years = _years(daily)
    return capital ** (1 / years) - 1 if years > 0 and capital > 0 else -1.0


def _years(daily: pl.DataFrame) -> float:
    """Calendar span, NOT row count.

    Every series here is compared against every other, and they do not have
    identical session counts: the NIFTY file carries ~135 fewer sessions than the
    equity panel over the same window. Annualising by row count then reports the
    sparser series over a shorter year and flatters its CAGR by ~0.9pp -- the
    same defect §11j.6 found in `vs_benchmark_series`. `metrics.compute` still
    uses the row-count convention, so Sharpe and CAGR are taken from these
    functions instead wherever the two must be compared.
    """
    days = (daily["date"].max() - daily["date"].min()).days
    return days / 365.25 if days > 0 else daily.height / 250


# ------------------------------------------------------------- sleeves --------
def sleeve(kind: str, *, long_side: str, **params):
    """One long-only book, run on the audited engine with an explicit cadence."""
    elig = st.restrict_to_universe(features.add(LIQ, kind, **params), MEMBERS)
    w = buf.buffered_long_only(
        elig, entry=ENTRY, exit_=EXIT, cadence=CADENCE,
        descending=(long_side == "high"),
    ).filter(pl.col("date") <= DEV_END)
    td = engine.implied_trade_dates(w)
    net = engine.run(weights=w, panel=PANEL, capital=CAPITAL, card=CARD,
                     slippage=SLIP, tier="mid", trade_dates=td)
    gross = engine.run(weights=w, panel=PANEL, capital=CAPITAL, card=FREE,
                       slippage=None, tier="mid", trade_dates=td)
    return net, gross


def realised_bps(blotter: pl.DataFrame) -> float:
    """Cost per rupee traded. The rate card is not bps -- DP is flat per scrip --
    so this is the only honest way to state the cost rate (§11j)."""
    if blotter.is_empty():
        return 0.0
    traded = float(blotter["value"].sum())
    return (
        float((blotter["charges"] + blotter["slippage"]).sum()) / traded * 10_000
        if traded > 0
        else 0.0
    )


def summarise(label: str, net_eq, gross_eq, *, turnover, cost_drag, cost_bps) -> Row:
    m = metrics.compute(net_eq, risk_free=0.0)
    g = metrics.compute(gross_eq, risk_free=0.0) if gross_eq is not None else m
    b = metrics.vs_benchmark_series(net_eq, BENCH)
    daily = net_eq.select(
        pl.col("exit_date" if "exit_date" in net_eq.columns else "date").alias("date"),
        "net_return",
    ).drop_nulls("date")
    return Row(
        label=label,
        cagr=m.cagr,
        sharpe=m.sharpe,
        maxdd=m.max_drawdown,
        turnover=turnover,
        cost_drag=cost_drag,
        cost_bps=cost_bps,
        after_tax_cagr=after_tax_cagr(daily),
        gross_sharpe=g.sharpe,
        beta=b.get("beta", float("nan")),
    )


# ------------------------------------------------------------- blending -------
def blend(label: str, a: pl.DataFrame, b: pl.DataFrame, wa: float,
          *, cost_bps: float) -> tuple[Row, float]:
    """Two sleeves held side by side, rebalanced to `wa`/`1-wa` monthly.

    Each sleeve's capital drifts with its own return between rebalances -- which
    is what actually happens in an account -- and the monthly correction is a
    real trade, charged at `cost_bps`. Blending the two net-return series
    instead, as the earlier sweep did, rebalances the blend to exact weights
    every session for free: the same error §11j found in the engine, one level
    up. Returns the row and the blend's own annual turnover, on top of the
    sleeves'.
    """
    j = a.join(b, on="date", how="inner", suffix="_b").sort("date")
    rebal = buf.rebalance_dates(j["date"].to_list(), CADENCE)

    ca, cb = CAPITAL * wa, CAPITAL * (1 - wa)
    rows, blend_traded = [], 0.0
    for d, ra, rb in zip(j["date"].to_list(), j["r"].to_list(), j["r_b"].to_list()):
        opening = ca + cb
        ca *= 1 + ra
        cb *= 1 + rb
        total = ca + cb
        cost = 0.0
        if d in rebal:
            want_a = total * wa
            moved = abs(want_a - ca)
            cost = moved * cost_bps / 10_000
            blend_traded += moved
            total -= cost
            ca, cb = total * wa, total * (1 - wa)
        rows.append(
            {
                "date": d,
                "capital": total,
                "net_return": total / opening - 1 if opening > 0 else 0.0,
                "gross_return": (total + cost) / opening - 1 if opening > 0 else 0.0,
                "cost": cost,
                "turnover": (moved if d in rebal else 0.0) / max(opening, 1e-9),
            }
        )
    eq = pl.DataFrame(rows)
    years = _years(eq)
    return eq, blend_traded / max(CAPITAL, 1.0) / max(years, 1e-9)


# ---------------------------------------------------------------- run ---------
print("loading ...", flush=True)
PANEL = rh.load_adjusted_panel()
LIQ = uv.liquidity(PANEL)
MEMBERS = uv.membership(LIQ, exclude=set(uv.DEFAULT_EXCLUSIONS))
BENCH = indices.benchmark_series(REPO / "data/raw/indices")

print("running sleeves ...", flush=True)
mom_net, mom_gross = sleeve("momentum", long_side="high", lookback=252, skip=21)
lv_net, lv_gross = sleeve("realised_vol", long_side="low", lookback=60)

mom_bps = realised_bps(mom_net.blotter)
lv_bps = realised_bps(lv_net.blotter)
mom_turn = float(mom_net.equity["turnover"].mean()) * 250
lv_turn = float(lv_net.equity["turnover"].mean()) * 250
mom_drag = metrics.compute(mom_net.equity, risk_free=0.0).cost_drag
lv_drag = metrics.compute(lv_net.equity, risk_free=0.0).cost_drag


def as_series(eq: pl.DataFrame, name: str = "r") -> pl.DataFrame:
    """Daily net returns keyed on the REALISATION date, so streams align."""
    key = "exit_date" if "exit_date" in eq.columns else "date"
    return eq.select(
        pl.col(key).alias("date"), pl.col("net_return").alias(name)
    ).drop_nulls("date").sort("date")


nifty = (
    BENCH.drop_nulls("ret")
    .filter(pl.col("date") <= DEV_END)
    .sort("date")
    .select("date", pl.col("ret").alias("r"))
)

# Blends first: each returns a full equity frame on its own inner-joined index.
eq_ml, turn_ml = blend(
    "25/75 Mom + LowVol", as_series(mom_net.equity), as_series(lv_net.equity),
    0.25, cost_bps=0.25 * mom_bps + 0.75 * lv_bps,
)
eq_mn, turn_mn = blend(
    "25/75 Mom + NIFTY", as_series(mom_net.equity), nifty, 0.25,
    cost_bps=0.25 * mom_bps + 0.75 * 30.0,
)

# ---- ONE COMMON WINDOW -------------------------------------------------------
# The five series do NOT share a date index: the equity panel starts in 2011 and
# the NIFTY file in Feb 2012, and the index carries ~135 fewer sessions over the
# overlap. Comparing CAGRs computed on different windows -- or annualised by row
# count, as `metrics.compute` does -- flatters whichever series is shorter or
# sparser. Momentum's own CAGR moves ~0.9pp on that choice alone, which is a
# sixth of the gap being judged. Everything below is measured on the
# intersection, annualised by calendar span.
STREAMS = {
    "Momentum": as_series(mom_net.equity),
    "Low Volatility": as_series(lv_net.equity),
    "25/75 Mom + LowVol": eq_ml.select("date", pl.col("net_return").alias("r")),
    "25/75 Mom + NIFTY": eq_mn.select("date", pl.col("net_return").alias("r")),
    "NIFTY buy & hold": nifty,
}
common = None
for s in STREAMS.values():
    d = s.select("date")
    common = d if common is None else common.join(d, on="date", how="inner")
common = common.sort("date")
print(f"  common window: {common['date'].min()} .. {common['date'].max()} "
      f"({common.height:,} sessions)")

COST = {
    "Momentum": (mom_turn, mom_drag, mom_bps),
    "Low Volatility": (lv_turn, lv_drag, lv_bps),
    "25/75 Mom + LowVol": (
        0.25 * mom_turn + 0.75 * lv_turn + turn_ml,
        0.25 * mom_drag + 0.75 * lv_drag
        + metrics.compute(eq_ml, risk_free=0.0).cost_drag,
        0.25 * mom_bps + 0.75 * lv_bps,
    ),
    # The NIFTY sleeve is an ETF: no sleeve turnover of its own, only the
    # blend's monthly correction.
    "25/75 Mom + NIFTY": (
        0.25 * mom_turn + turn_mn,
        0.25 * mom_drag + metrics.compute(eq_mn, risk_free=0.0).cost_drag,
        0.25 * mom_bps + 0.75 * 30.0,
    ),
    "NIFTY buy & hold": (0.0, 0.0, 0.0),
}


def measure(label: str, s: pl.DataFrame) -> Row:
    d = common.join(s, on="date", how="left").sort("date")
    eq = d.with_columns(
        (CAPITAL * (1 + pl.col("r")).cum_prod()).alias("capital"),
        pl.col("r").alias("net_return"),
    )
    years = _years(eq)
    total = float(eq["capital"][-1]) / CAPITAL
    cagr = total ** (1 / years) - 1
    vol = float(eq["r"].std() or 0.0) * (250**0.5)
    turnover, drag, bps = COST[label]

    if label.startswith("NIFTY"):
        # Buy and hold defers tax entirely: one LTCG event on exit, and the
        # untaxed gains compound in the meantime. That deferral is worth more
        # than the lower rate, and it is the asymmetry every active book must
        # overcome.
        at = (1 + (total - 1) * (1 - LTCG)) ** (1 / years) - 1
    else:
        at = after_tax_cagr(eq.select("date", "net_return"))

    return Row(
        label=label, cagr=cagr, sharpe=cagr / vol if vol > 0 else 0.0,
        maxdd=metrics.max_drawdown(eq["capital"]), turnover=turnover,
        cost_drag=drag, cost_bps=bps, after_tax_cagr=at,
        gross_sharpe=(cagr + drag) / vol if vol > 0 else 0.0,
        beta=metrics.vs_benchmark_series(
            eq.select("date", "net_return",
                      pl.col("capital"), pl.lit(0.0).alias("gross_return"),
                      pl.lit(0.0).alias("cost"), pl.lit(0.0).alias("turnover")),
            BENCH,
        ).get("beta", float("nan")),
    )


results = [measure(k, v) for k, v in STREAMS.items()]

# ------------------------------------------------------------- report ---------
print(f"\n{'='*98}")
print("RE-MEASURED ON THE AUDITED ENGINE (§11j)  --  dev only, one common window")
print("=" * 98)
print(f"\n{'config':<24}{'CAGR':>8}{'SR':>7}{'grossSR':>9}{'maxDD':>9}"
      f"{'turnover':>10}{'drag':>8}{'bps':>7}{'beta':>7}{'after-tax':>11}")
for r in sorted(results, key=lambda x: -x.sharpe):
    print(f"{r.label:<24}{r.cagr:>8.1%}{r.sharpe:>7.2f}{r.gross_sharpe:>9.2f}"
          f"{r.maxdd:>9.1%}{r.turnover:>10.0%}{r.cost_drag:>8.2%}"
          f"{r.cost_bps:>7.1f}{r.beta:>7.2f}{r.after_tax_cagr:>11.1%}")

base = next(r for r in results if r.label.startswith("NIFTY"))
print(f"\n{'config':<24}{'after-tax':>11}{'vs NIFTY':>11}{'maxDD':>9}"
      f"{'return per unit of DD':>24}")
for r in sorted(results, key=lambda x: -x.after_tax_cagr):
    print(f"{r.label:<24}{r.after_tax_cagr:>11.1%}"
          f"{r.after_tax_cagr - base.after_tax_cagr:>+11.1%}{r.maxdd:>9.1%}"
          f"{r.after_tax_cagr / abs(r.maxdd):>24.2f}")

print("\n--- what the tax asymmetry costs each config ---")
print(f"{'config':<24}{'pre-tax':>10}{'after-tax':>11}{'given up':>11}")
for r in sorted(results, key=lambda x: -x.cagr):
    print(f"{r.label:<24}{r.cagr:>10.1%}{r.after_tax_cagr:>11.1%}"
          f"{(r.cagr - r.after_tax_cagr) * 100:>9.1f}pp")

with (REPO / "config" / "trials.jsonl").open("a", encoding="utf-8") as fh:
    fh.write(json.dumps({
        "kind": "exploration",
        "note": "re-measurement of 4 EXISTING configs on the audited engine; "
                "no new configs, no search -- denominator unchanged",
        "configs_explored": 0, "registered_as_trials": 0,
    }, sort_keys=True) + "\n")
print("\n0 new configs: re-measurement of existing ones, denominator unchanged")
