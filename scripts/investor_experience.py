#!/usr/bin/env python
"""Which candidate survives an actual investor? DIAGNOSTIC ONLY.

Not "which has the highest CAGR" -- §11k already answered that and §11l showed
the answer reverses under a pain-adjusted lens. This asks the question that
decides whether a strategy is *investable*: across every start date an investor
could plausibly have picked, how often did it work, how long did it stay wrong,
and would anyone have held on.

Three streams, full archive (2012-02 .. 2026-08, includes the holdout window
already inspected in §11m):

    1. Pure Momentum                 12-1, liquid 300, buffer 30/60, monthly
    2. 25/75 Momentum + Low Vol      monthly-rebalanced blend, charged for it
    3. NIFTY buy & hold

No factors searched, no parameters tuned, no trial registered.

Two conventions worth stating because they change conclusions:

**Relative performance is a RATIO, not a difference.** `strategy_cum - nifty_cum`
grows mechanically with elapsed time, so late episodes always look worse than
early ones regardless of what happened. The ratio strategy_wealth / nifty_wealth
is scale-free and its drawdowns mean the same thing in 2013 and 2026.

**Windows start on month ends.** Overlapping daily windows would report ~3,400
"observations" of which only ~14/horizon are independent. Monthly starts keep
the count honest and the percentiles readable; the effective independent count
is printed alongside.

    python scripts/investor_experience.py
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import polars as pl

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from algotrade.backtest import drawdown as dd, engine, metrics  # noqa: E402
from algotrade.core import buffering as buf, costs, features  # noqa: E402
from algotrade.core import strategies as st, universe as uv  # noqa: E402
from algotrade.io import indices  # noqa: E402

import importlib  # noqa: E402

rh = importlib.import_module("scripts.run_hypothesis")

CAPITAL = 200_000.0
ENTRY, EXIT, CADENCE = 30, 60, "monthly"
CARD = costs.load_rate_cards(REPO / "config/costs.yaml")["delivery"]
SLIP = costs.load_slippage(REPO / "config/costs.yaml")
MOM, BLEND, NIFTY = "Pure Momentum", "25/75 Mom + LowVol", "NIFTY buy & hold"
HORIZONS = [("1-year", 365), ("3-year", 3 * 365), ("5-year", 5 * 365),
            ("10-year", 10 * 365)]


# ----------------------------------------------------------- construction -----
def sleeve(kind: str, *, long_side: str, **params) -> tuple[pl.DataFrame, float]:
    elig = st.restrict_to_universe(features.add(LIQ, kind, **params), MEMBERS)
    w = buf.buffered_long_only(
        elig, entry=ENTRY, exit_=EXIT, cadence=CADENCE,
        descending=(long_side == "high"),
    )
    r = engine.run(weights=w, panel=PANEL, capital=CAPITAL, card=CARD,
                   slippage=SLIP, tier="mid",
                   trade_dates=engine.implied_trade_dates(w))
    traded = float(r.blotter["value"].sum())
    bps = (float((r.blotter["charges"] + r.blotter["slippage"]).sum()) / traded
           * 10_000) if traded > 0 else 0.0
    return r.equity.select(
        pl.col("exit_date").alias("date"), pl.col("net_return").alias("r")
    ).drop_nulls("date").sort("date"), bps


def blend_streams(a: pl.DataFrame, b: pl.DataFrame, wa: float, *, cost_bps: float):
    """Sleeves drift with their own returns; the monthly correction is charged."""
    j = a.join(b, on="date", how="inner", suffix="_b").sort("date")
    rebal = buf.rebalance_dates(j["date"].to_list(), CADENCE)
    ca, cb, rows = CAPITAL * wa, CAPITAL * (1 - wa), []
    for d, ra, rb in zip(j["date"].to_list(), j["r"].to_list(), j["r_b"].to_list()):
        opening = ca + cb
        ca *= 1 + ra
        cb *= 1 + rb
        total = ca + cb
        if d in rebal:
            moved = abs(total * wa - ca)
            total -= moved * cost_bps / 10_000
            ca, cb = total * wa, total * (1 - wa)
        rows.append({"date": d, "r": total / opening - 1 if opening > 0 else 0.0})
    return pl.DataFrame(rows)


print("loading ...", flush=True)
PANEL = rh.load_adjusted_panel()
LIQ = uv.liquidity(PANEL)
MEMBERS = uv.membership(LIQ, exclude=set(uv.DEFAULT_EXCLUSIONS))
BENCH = indices.benchmark_series(REPO / "data/raw/indices")

print("building streams ...", flush=True)
mom, mom_bps = sleeve("momentum", long_side="high", lookback=252, skip=21)
lv, lv_bps = sleeve("realised_vol", long_side="low", lookback=60)
nifty = BENCH.drop_nulls("ret").sort("date").select("date", pl.col("ret").alias("r"))

STREAMS = {
    MOM: mom,
    BLEND: blend_streams(mom, lv, 0.25, cost_bps=0.25 * mom_bps + 0.75 * lv_bps),
    NIFTY: nifty,
}
common = None
for s in STREAMS.values():
    d = s.select("date")
    common = d if common is None else common.join(d, on="date", how="inner")
common = common.sort("date")

W: dict[str, pl.DataFrame] = {}
for name, s in STREAMS.items():
    W[name] = (
        common.join(s, on="date", how="left").sort("date")
        .with_columns((100.0 * (1 + pl.col("r")).cum_prod()).alias("w"))
    )

DATES = common["date"].to_list()
SPAN = (DATES[-1] - DATES[0]).days / 365.25
print(f"  {len(DATES):,} common sessions, {DATES[0]} .. {DATES[-1]} "
      f"({SPAN:.2f} years)")
print("  NOTE: includes the 2022+ holdout window, already inspected in §11m")

# Month-end sessions: the start points an investor could actually have used.
STARTS = sorted(
    pl.DataFrame({"date": DATES}).group_by_dynamic("date", every="1mo")
    .agg(pl.col("date").max().alias("d")).get_column("d").to_list()
)
WEALTH = {n: dict(zip(f["date"].to_list(), f["w"].to_list())) for n, f in W.items()}
IDX = {d: i for i, d in enumerate(DATES)}


def window_return(name: str, start: date, days: int) -> float | None:
    """Total return from `start` over `days` calendar days, or None if the
    window runs past the archive."""
    end = start + __import__("datetime").timedelta(days=days)
    if end > DATES[-1]:
        return None
    # First session at or after the target end date.
    i = IDX[start]
    j = i
    while j < len(DATES) - 1 and DATES[j] < end:
        j += 1
    return WEALTH[name][DATES[j]] / WEALTH[name][start] - 1


def pct(xs: list[float], p: float) -> float:
    s = sorted(xs)
    return s[min(int(p * len(s)), len(s) - 1)]


def head(n, title: str) -> None:
    print(f"\n{'='*104}\n{n}. {title}\n{'='*104}")


def annualise(total: float, days: int) -> float:
    return (1 + total) ** (365 / days) - 1


# ------------------------------------------------------ 1. ROLLING RETURNS ----
head(1, "ROLLING RETURN ANALYSIS (month-end starts, TOTAL return over the window)")
ROLL: dict[tuple[str, str], list[float]] = {}
for label, days in HORIZONS:
    rows = {n: [] for n in W}
    for s in STARTS:
        vals = {n: window_return(n, s, days) for n in W}
        if any(v is None for v in vals.values()):
            continue
        for n, v in vals.items():
            rows[n].append(v)
    if not rows[MOM]:
        print(f"\n--- {label}: no complete windows in the archive ---")
        continue
    n_win = len(rows[MOM])
    indep = SPAN / (days / 365)
    print(f"\n--- {label} horizon: {n_win} windows "
          f"(~{indep:.1f} independent) ---")
    print(f"{'strategy':<22}{'mean':>9}{'median':>9}{'10th':>9}{'25th':>9}"
          f"{'worst':>9}{'best':>9}{'ann. median':>13}")
    for n in W:
        v = rows[n]
        ROLL[(n, label)] = v
        print(f"{n:<22}{sum(v)/len(v):>9.1%}{pct(v,0.50):>9.1%}"
              f"{pct(v,0.10):>9.1%}{pct(v,0.25):>9.1%}{min(v):>9.1%}"
              f"{max(v):>9.1%}{annualise(pct(v,0.50), days):>13.1%}")

    print(f"\n{'strategy':<22}{'beats NIFTY':>13}{'loses by >5pp':>15}"
          f"{'wins by >5pp':>14}")
    for n in W:
        if n == NIFTY:
            continue
        a, b = rows[n], rows[NIFTY]
        diff = [x - y for x, y in zip(a, b)]
        print(f"{n:<22}{sum(d > 0 for d in diff)/len(diff):>13.0%}"
              f"{sum(d < -0.05 for d in diff)/len(diff):>15.0%}"
              f"{sum(d > 0.05 for d in diff)/len(diff):>14.0%}")


# ------------------------------------------- 2 & 3. RELATIVE UNDERPERFORMANCE -
# The relative-wealth RATIO. An "underperformance streak" and a "relative
# drawdown" are the same object: a stretch during which the strategy has not
# regained its previous best standing against the index. Measuring them
# separately would double-count.
REL = {}
for n in W:
    if n == NIFTY:
        continue
    REL[n] = pl.DataFrame({
        "date": DATES,
        "capital": [WEALTH[n][d] / WEALTH[NIFTY][d] for d in DATES],
    })

head(2, "UNDERPERFORMANCE STREAKS vs NIFTY (relative-wealth drawdowns)")
print("A streak runs from the strategy's best-ever standing against the index")
print("until it regains that standing. Streaks >=1% are counted.\n")
STREAKS = {}
for n, rel in REL.items():
    eps = [e for e in dd.episodes(rel) if abs(e.depth) >= 0.01]
    STREAKS[n] = eps
    done = [e for e in eps if e.recovered]
    cal = [(e.recovery - e.start).days for e in done]
    open_ = [e for e in eps if not e.recovered]
    if open_:
        cal_open = [(DATES[-1] - e.start).days for e in open_]
    print(f"--- {n} ---")
    print(f"  streaks: {len(eps)}   completed: {len(done)}   ongoing: {len(open_)}")
    if cal:
        print(f"  average streak : {sum(cal)/len(cal):>6.0f} days")
        print(f"  median streak  : {sorted(cal)[len(cal)//2]:>6.0f} days")
        print(f"  longest streak : {max(cal):>6.0f} days (completed)")
    if open_:
        print(f"  ONGOING streak : {max(cal_open):>6.0f} days and counting")
    print(f"  streaks > 1 year : "
          f"{sum(1 for c in cal if c > 365) + sum(1 for c in (cal_open if open_ else []) if c > 365)}")
    print(f"  streaks > 2 years: "
          f"{sum(1 for c in cal if c > 730) + sum(1 for c in (cal_open if open_ else []) if c > 730)}")
    long = sorted([e for e in eps
                   if ((e.recovery - e.start).days if e.recovered
                       else (DATES[-1] - e.start).days) > 365],
                  key=lambda e: -((e.recovery - e.start).days if e.recovered
                                  else (DATES[-1] - e.start).days))
    if long:
        print(f"\n  {'from':<12}{'trough':<12}{'to':<12}{'days':>7}"
              f"{'shortfall':>11}")
        for e in long:
            days_ = ((e.recovery - e.start).days if e.recovered
                     else (DATES[-1] - e.start).days)
            print(f"  {str(e.start):<12}{str(e.trough):<12}"
                  f"{(str(e.recovery) if e.recovered else 'ONGOING'):<12}"
                  f"{days_:>7}{e.depth:>11.1%}")
    print()


head(3, "RELATIVE DRAWDOWN vs NIFTY (ratio of wealth curves)")
print(f"{'strategy':<22}{'max rel DD':>12}{'avg rel DD':>12}"
      f"{'rel ulcer':>11}{'longest recovery':>18}{'% time behind peak':>20}")
for n, rel in REL.items():
    eps = STREAKS[n]
    depths = [abs(e.depth) for e in eps]
    done = [e for e in eps if e.recovered]
    longest = max([(e.recovery - e.start).days for e in done] or [0])
    open_ = [e for e in eps if not e.recovered]
    if open_:
        longest = max(longest, max((DATES[-1] - e.start).days for e in open_))
    print(f"{n:<22}{min(e.depth for e in eps):>12.1%}"
          f"{-sum(depths)/len(depths):>12.1%}"
          f"{dd.ulcer_index(rel['capital']):>11.1f}{longest:>18}"
          f"{dd.underwater_fraction(rel['capital']):>20.1%}")

for n, eps in STREAKS.items():
    print(f"\n--- {n}: worst 10 relative episodes by shortfall ---")
    print(f"{'from':<12}{'trough':<12}{'to':<12}{'shortfall':>11}{'days':>7}")
    for e in sorted(eps, key=lambda x: x.depth)[:10]:
        days_ = ((e.recovery - e.start).days if e.recovered
                 else (DATES[-1] - e.start).days)
        print(f"{str(e.start):<12}{str(e.trough):<12}"
              f"{(str(e.recovery) if e.recovered else 'ONGOING'):<12}"
              f"{e.depth:>11.1%}{days_:>7}")


# ------------------------------------------------ 4. TERMINAL WEALTH ----------
head(4, "TERMINAL WEALTH ROBUSTNESS -- Rs 100 invested at every month end")
for label, days in HORIZONS:
    if not ROLL.get((MOM, label)):
        continue
    print(f"\n--- {label} holding period ({len(ROLL[(MOM, label)])} start months) ---")
    print(f"{'strategy':<22}{'median':>10}{'worst decile':>14}{'best decile':>13}"
          f"{'worst':>9}{'best':>9}{'P(beat NIFTY)':>15}")
    for n in W:
        v = [100 * (1 + x) for x in ROLL[(n, label)]]
        nb = [100 * (1 + x) for x in ROLL[(NIFTY, label)]]
        beat = sum(a > b for a, b in zip(v, nb)) / len(v)
        print(f"{n:<22}{pct(v,0.50):>10.0f}{pct(v,0.10):>14.0f}"
              f"{pct(v,0.90):>13.0f}{min(v):>9.0f}{max(v):>9.0f}"
              f"{('--' if n == NIFTY else f'{beat:.0%}'):>15}")


# ---------------------------------------- 4b. MONTHLY CONTRIBUTIONS (SIP) -----
head("4b", "MONTHLY CONTRIBUTIONS -- Rs 10,000 every month end")
print("Lump-sum results answer a question nobody asks. An investor contributing")
print("monthly buys MORE units during a long drawdown, so a strategy that falls")
print("far and recovers is treated very differently from one that never falls --")
print("and momentum spends 66% of its life inside multi-year drawdowns (§11m).\n")

SIP = 10_000.0


def sip(name: str, start: date, days: int):
    """Contribute SIP at each month end from `start`; return (invested, value)."""
    end = start + __import__("datetime").timedelta(days=days)
    if end > DATES[-1]:
        return None
    pts = [d for d in STARTS if start <= d <= end]
    if not pts:
        return None
    units = 0.0
    for d in pts:
        units += SIP / WEALTH[name][d]
    final = WEALTH[name][max(d for d in DATES if d <= end)]
    return SIP * len(pts), units * final


def xirr(invested_months: int, value: float, contributed: float) -> float:
    """Money-weighted annual return, solved by bisection. A time-weighted CAGR
    would credit the strategy for returns on money not yet contributed."""
    lo, hi = -0.99, 5.0
    for _ in range(200):
        mid = (lo + hi) / 2
        m = (1 + mid) ** (1 / 12) - 1
        # Future value of an annuity of SIP paid at each month end.
        fv = sum(SIP * (1 + m) ** (invested_months - 1 - i)
                 for i in range(invested_months))
        if fv < value:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


for label, days in (("5-year", 5 * 365), ("10-year", 10 * 365)):
    rows = {n: [] for n in W}
    for s in STARTS:
        got = {n: sip(n, s, days) for n in W}
        if any(v is None for v in got.values()):
            continue
        for n, (inv, val) in got.items():
            rows[n].append((inv, val))
    if not rows[MOM]:
        continue
    n_start = len(rows[MOM])
    months = int(round(days / 30.44))
    print(f"--- {label} of monthly contributions ({n_start} start months, "
          f"~{months} instalments) ---")
    print(f"{'strategy':<22}{'median value':>14}{'worst':>10}{'best':>10}"
          f"{'median XIRR':>13}{'worst XIRR':>12}{'P(beat NIFTY)':>15}")
    for n in W:
        vals = [v for _, v in rows[n]]
        nb = [v for _, v in rows[NIFTY]]
        beat = sum(a > b for a, b in zip(vals, nb)) / len(vals)
        inv = rows[n][0][0]
        n_inst = int(inv / SIP)
        med_x = xirr(n_inst, pct(vals, 0.50), inv)
        wor_x = xirr(n_inst, min(vals), inv)
        print(f"{n:<22}{pct(vals,0.50):>14,.0f}{min(vals):>10,.0f}"
              f"{max(vals):>10,.0f}{med_x:>13.1%}{wor_x:>12.1%}"
              f"{('--' if n == NIFTY else f'{beat:.0%}'):>15}")
    print(f"  (total contributed: Rs {rows[MOM][0][0]:,.0f})\n")


# ------------------------------------------- 5. INVESTOR EXPERIENCE -----------
head(5, "INVESTOR EXPERIENCE -- probability of a good outcome from a random start")
print(f"{'strategy':<22}" + "".join(
    f"{'P(gain) ' + h:>16}" for h, _ in HORIZONS[:3]))
for n in W:
    row = f"{n:<22}"
    for label, _ in HORIZONS[:3]:
        v = ROLL.get((n, label), [])
        row += f"{(sum(x > 0 for x in v)/len(v) if v else 0):>16.0%}"
    print(row)

print(f"\n{'strategy':<22}" + "".join(
    f"{'P(beat NIFTY) ' + h:>22}" for h, _ in HORIZONS[:3]))
for n in W:
    if n == NIFTY:
        continue
    row = f"{n:<22}"
    for label, _ in HORIZONS[:3]:
        a, b = ROLL.get((n, label), []), ROLL.get((NIFTY, label), [])
        row += f"{(sum(x > y for x, y in zip(a, b))/len(a) if a else 0):>22.0%}"
    print(row)


# ------------------------------------------------ 6. DECISION TABLE -----------
head(6, "FINAL DECISION TABLE")
FINAL = {}
for n, f in W.items():
    daily = f.select("date", pl.col("r").alias("net_return"))
    total = f["w"][-1] / 100.0
    cagr = total ** (1 / SPAN) - 1
    vol = float(f["r"].std() or 0.0) * (250**0.5)
    cap = f["w"]
    eps = [e for e in dd.episodes(f.rename({"w": "capital"})) if abs(e.depth) >= 0.05]
    done = [e for e in eps if e.recovered]
    longest = max([(e.recovery - e.start).days for e in done] or [0])
    for e in eps:
        if not e.recovered:
            longest = max(longest, (DATES[-1] - e.start).days)
    at = metrics.after_tax_cagr(daily, deferred=n == NIFTY, deferred_rate=0.125)
    a3, b3 = ROLL.get((n, "3-year"), []), ROLL.get((NIFTY, "3-year"), [])
    a5, b5 = ROLL.get((n, "5-year"), []), ROLL.get((NIFTY, "5-year"), [])
    rel_long = 0
    if n in STREAKS:
        for e in STREAKS[n]:
            d_ = ((e.recovery - e.start).days if e.recovered
                  else (DATES[-1] - e.start).days)
            rel_long = max(rel_long, d_)
    FINAL[n] = {
        "CAGR": f"{cagr:.1%}",
        "After-tax CAGR": f"{at:.1%}",
        # Long-only: deduct rf (see metrics.compute).
        "Sharpe": f"{(cagr - metrics.RISK_FREE)/vol:.2f}" if vol else "--",
        "Martin (after tax)": f"{dd.martin_ratio(at, cap, risk_free=metrics.RISK_FREE):.2f}",
        "Max drawdown": f"{metrics.max_drawdown(cap):.1%}",
        "Ulcer index": f"{dd.ulcer_index(cap):.1f}",
        "Longest drawdown (days)": f"{longest:,}",
        "% time underwater": f"{dd.underwater_fraction(cap):.1%}",
        "% 3Y windows beat NIFTY": (
            "--" if n == NIFTY else
            f"{sum(x>y for x,y in zip(a3,b3))/len(a3):.0%}" if a3 else "--"),
        "% 5Y windows beat NIFTY": (
            "--" if n == NIFTY else
            f"{sum(x>y for x,y in zip(a5,b5))/len(a5):.0%}" if a5 else "--"),
        "Longest underperf. streak": "--" if n == NIFTY else f"{rel_long:,}",
        "P(positive 5Y)": f"{sum(x>0 for x in a5)/len(a5):.0%}" if a5 else "--",
        "P(beat NIFTY 5Y)": (
            "--" if n == NIFTY else
            f"{sum(x>y for x,y in zip(a5,b5))/len(a5):.0%}" if a5 else "--"),
    }

keys = list(FINAL[MOM])
print(f"{'metric':<28}{MOM:>20}{BLEND:>22}{NIFTY:>20}")
print("-" * 90)
for k in keys:
    print(f"{k:<28}{FINAL[MOM][k]:>20}{FINAL[BLEND][k]:>22}{FINAL[NIFTY][k]:>20}")

with (REPO / "config" / "trials.jsonl").open("a", encoding="utf-8") as fh:
    fh.write(json.dumps({
        "kind": "exploration",
        "note": "investor-experience diagnostic: rolling windows, "
                "underperformance streaks, relative drawdown, terminal wealth. "
                "Full archive incl. already-inspected holdout. No new configs.",
        "configs_explored": 0, "registered_as_trials": 0,
        "touched_holdout": True,
    }, sort_keys=True) + "\n")
print("\n0 new configs; diagnostic only, denominator unchanged")
