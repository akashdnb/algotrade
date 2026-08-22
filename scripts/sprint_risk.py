#!/usr/bin/env python
"""Risk-management sprint: can momentum's drawdown be tamed long-only?

The factor is established and turnover is largely solved (buffer 30/60, monthly,
net Sharpe ~0.80). The remaining gap is drawdown: -54% against a 25% target.

**P5 runs first.** The sprint lists drawdown attribution fifth but says "before
adding more complexity", and the ordering matters: if drawdowns are market
crashes then only beta reduction can help, and stock-level filters and weighting
schemes are wasted effort. Diagnose before treating.

All diagnostic. Config count appended to the trial log (§8.2).

    python scripts/sprint_risk.py
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
from algotrade.core import portfolio as pf, strategies as st, universe as uv  # noqa: E402
from algotrade.io import indices  # noqa: E402

import importlib  # noqa: E402

rh = importlib.import_module("scripts.run_hypothesis")

DEV_END = date(2021, 12, 31)
CAPITAL = 200_000.0
CARD = costs.load_rate_cards(REPO / "config/costs.yaml")["delivery"]
SLIP = costs.load_slippage(REPO / "config/costs.yaml")
FREE = costs.RateCard("free", 0, None, 0, 0, 0, 0, 0, 0, 0)

# FROZEN, per the sprint rules. Best from the turnover sprint.
ENTRY, EXIT, CADENCE = 30, 60, "monthly"

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
    exposure: float


def evaluate(label: str, weights: pl.DataFrame, *, capital=CAPITAL) -> Row | None:
    global configs
    configs += 1
    w = weights.filter(pl.col("date") <= DEV_END)
    if w.is_empty():
        return None
    r = engine.run(weights=w, panel=PANEL, capital=capital, card=CARD,
                   slippage=SLIP, tier="mid",
                   trade_dates=engine.implied_trade_dates(w))
    if r.equity.is_empty():
        return None
    m = metrics.compute(r.equity, risk_free=0.0)
    b = metrics.vs_benchmark_series(r.equity, BENCH)
    # Average gross exposure: what fraction of capital was actually deployed.
    exp = float(w.group_by("date").agg(pl.col("weight").abs().sum())["weight"].mean())
    return Row(label, m.cagr, m.sharpe, m.volatility, m.max_drawdown,
               float(r.equity["turnover"].mean()) * 250,
               b.get("beta", float("nan")), b.get("excess", float("nan")), exp)


def table(title: str, rows) -> None:
    print(f"\n=== {title} ===")
    print(f"{'variant':<28}{'CAGR':>8}{'vol':>7}{'SR':>7}{'maxDD':>9}"
          f"{'turn':>8}{'beta':>7}{'vsNIFTY':>9}{'exposure':>10}")
    for r in rows:
        if r is None:
            continue
        print(f"{r.label:<28}{r.cagr:>8.1%}{r.vol:>7.1%}{r.sharpe:>7.2f}"
              f"{r.maxdd:>9.1%}{r.turnover:>8.0%}{r.beta:>7.2f}"
              f"{r.excess:>+9.1%}{r.exposure:>10.0%}")


print("loading ...", flush=True)
PANEL = rh.load_adjusted_panel()
LIQ = uv.liquidity(PANEL)
MEMBERS = uv.membership(LIQ, exclude=set(uv.DEFAULT_EXCLUSIONS))
FEAT = features.add(LIQ, "momentum", lookback=252, skip=21)
ELIG = st.restrict_to_universe(FEAT, MEMBERS)
BENCH = indices.benchmark_series(REPO / "data/raw/indices")

BASE_W = buf.buffered_long_only(ELIG, entry=ENTRY, exit_=EXIT, cadence=CADENCE)
base = evaluate(f"buffer {ENTRY}/{EXIT} {CADENCE} (frozen)", BASE_W)


# ============ P5 FIRST: what actually causes the drawdowns? ==================
print("\n" + "=" * 78)
print("P5 (RUN FIRST): drawdown attribution -- diagnose before treating")
print("=" * 78)

_bw = BASE_W.filter(pl.col("date") <= DEV_END)
base_run = engine.run(weights=_bw, panel=PANEL, capital=CAPITAL, card=CARD,
                      slippage=SLIP, tier="mid",
                      trade_dates=engine.implied_trade_dates(_bw))
eq = base_run.equity.sort("date").with_columns(
    (pl.col("capital") / pl.col("capital").cum_max() - 1).alias("dd")
)
bench_idx = BENCH.select("date", pl.col("close").alias("nifty"))
eq = eq.join(bench_idx, left_on="exit_date", right_on="date", how="left")

# Contiguous underwater stretches.
under = eq.with_columns((pl.col("dd") < -0.05).alias("u"))
under = under.with_columns(
    (pl.col("u") != pl.col("u").shift(1)).cum_sum().alias("grp")
)
episodes = (
    under.filter(pl.col("u"))
    .group_by("grp")
    .agg(
        pl.col("date").min().alias("start"),
        pl.col("date").max().alias("end"),
        pl.col("dd").min().alias("depth"),
        pl.col("nifty").first().alias("nifty_start"),
        pl.col("nifty").last().alias("nifty_end"),
        pl.len().alias("sessions"),
    )
    .with_columns(
        (pl.col("nifty_end") / pl.col("nifty_start") - 1).alias("nifty_move")
    )
    .sort("depth")
    .head(10)
)
print(f"\n{'start':<12}{'end':<12}{'sessions':>9}{'depth':>9}{'NIFTY over same window':>26}")
market_driven = 0
for r in episodes.iter_rows(named=True):
    nm = r["nifty_move"]
    tag = ""
    if nm is not None and nm < -0.10:
        tag = "  <- market fell too"
        market_driven += 1
    print(f"{str(r['start']):<12}{str(r['end']):<12}{r['sessions']:>9}"
          f"{r['depth']:>9.1%}{(nm if nm is not None else float('nan')):>20.1%}{tag}")
print(f"\n{market_driven} of {episodes.height} worst drawdowns coincided with a "
      f">10% NIFTY decline.")
print(f"portfolio beta {base.beta:.2f}  |  NIFTY's own max drawdown was -38.4%")


# ============ P1: volatility targeting ======================================
vol_rows = [base]
unlev = base_run.equity.select("date", "net_return")
best_vol = None
for target in (0.08, 0.10, 0.12, 0.15):
    lev = pf.vol_scalar(unlev, target=target, lookback=60, max_leverage=1.0)
    r = evaluate(f"vol target {target:.0%}", pf.scale(BASE_W, lev))
    vol_rows.append(r)
table("P1: volatility targeting (no leverage, so it can only de-risk)", vol_rows)


# ============ P2: market trend filter =======================================
def regime_dates(window: int) -> set:
    reg = BENCH.sort("date").with_columns(
        pl.col("close").rolling_mean(window_size=window).alias("ma")
    )
    return set(reg.filter(pl.col("close") > pl.col("ma"))["date"].to_list())


trend_rows = [base]
for label, window in (("NIFTY > 200DMA", 200), ("NIFTY > 10mo MA", 210),
                      ("NIFTY > 12mo MA", 250)):
    keep = regime_dates(window)
    trend_rows.append(
        evaluate(label, BASE_W.filter(pl.col("date").is_in(list(keep))))
    )
table("P2: market trend filter  [UNDER AUDIT for look-ahead]", trend_rows)


# ============ P3: stock-level trend filter ==================================
dma = LIQ.sort(["symbol", "date"]).with_columns(
    pl.col("close").rolling_mean(window_size=200).over("symbol").alias("dma200"),
    pl.col("close").rolling_mean(window_size=150).over("symbol").alias("dma150"),
)
elig_dma = st.restrict_to_universe(
    features.add(dma, "momentum", lookback=252, skip=21), MEMBERS
)
stock_rows = [base]
for label, col in (("stock > 200DMA", "dma200"), ("stock > 150DMA", "dma150")):
    sub = elig_dma.filter(pl.col("close") > pl.col(col))
    stock_rows.append(evaluate(
        label, buf.buffered_long_only(sub, entry=ENTRY, exit_=EXIT, cadence=CADENCE)
    ))
table("P3: stock-level trend filter", stock_rows)


# ============ P4: position-level volatility scaling =========================
risked = st.risk_column(ELIG, lookback=60)
wt_rows = [base]

# Inverse volatility, then a capped variant, applied to the frozen book.
rk = risked.select("date", "symbol", "risk")
inv = BASE_W.join(rk, on=["date", "symbol"], how="left").filter(
    pl.col("risk").is_not_null() & (pl.col("risk") > 0)
)
inv_w = inv.with_columns(
    ((1 / pl.col("risk")) / (1 / pl.col("risk")).sum().over("date")).alias("weight")
).select("date", "symbol", "weight")
wt_rows.append(evaluate("inverse-vol weighting", inv_w))

capped = inv_w.pipe(lambda d: pf.cap_weights(d, max_weight=2.0 / ENTRY))
wt_rows.append(evaluate("inverse-vol, capped 2x EW", capped))

# Equal-risk-contribution approximation: weight ~ 1/vol is the ERC solution
# when correlations are assumed equal, so the sqrt variant sits between EW and
# full inverse-vol.
sqrt_w = inv.with_columns(
    ((1 / pl.col("risk")).sqrt() / (1 / pl.col("risk")).sqrt().sum().over("date"))
    .alias("weight")
).select("date", "symbol", "weight")
wt_rows.append(evaluate("sqrt inverse-vol (ERC approx)", sqrt_w))
table("P4: position weighting", wt_rows)


# ============ P6: combine the winners, no re-optimisation ===================
def pick(rows, *, need_dd=None):
    """Best net Sharpe among variants, optionally subject to a drawdown cap."""
    live = [r for r in rows[1:] if r is not None]
    if need_dd is not None:
        live = [r for r in live if abs(r.maxdd) <= need_dd] or live
    return max(live, key=lambda r: r.sharpe) if live else None


print("\n" + "=" * 78)
print("P6: combined -- frozen buffer + best weighting + best trend + vol target")
print("=" * 78)
best_wt = pick(wt_rows)
best_trend = pick(trend_rows)
print(f"  weighting: {best_wt.label if best_wt else 'none'}")
print(f"  trend    : {best_trend.label if best_trend else 'none'}")

combo = inv_w if (best_wt and "inverse-vol" in best_wt.label
                  and "capped" not in best_wt.label) else capped
if best_trend:
    win = {"NIFTY > 200DMA": 200, "NIFTY > 10mo MA": 210, "NIFTY > 12mo MA": 250}
    combo = combo.filter(pl.col("date").is_in(list(regime_dates(win[best_trend.label]))))

combo_rows = [base, evaluate("combined (no vol target)", combo)]
_cw = combo.filter(pl.col("date") <= DEV_END)
tmp = engine.run(weights=_cw, panel=PANEL,
                 capital=CAPITAL, card=CARD, slippage=SLIP, tier="mid",
                 trade_dates=engine.implied_trade_dates(_cw))
if not tmp.equity.is_empty():
    for target in (0.10, 0.15):
        lev = pf.vol_scalar(tmp.equity.select("date", "net_return"),
                            target=target, lookback=60, max_leverage=1.0)
        combo_rows.append(evaluate(f"combined + vol {target:.0%}",
                                   pf.scale(combo, lev)))
table("P6: combined portfolio", combo_rows)

log = REPO / "config" / "trials.jsonl"
with log.open("a", encoding="utf-8") as fh:
    fh.write(json.dumps({
        "kind": "exploration",
        "note": "risk sprint: drawdown attribution, vol targeting, trend filters, "
                "position weighting, combined portfolio",
        "configs_explored": configs,
        "registered_as_trials": 0,
    }, sort_keys=True) + "\n")
print(f"\n{configs} configs explored; recorded as exploration in trials.jsonl")
