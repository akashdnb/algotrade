#!/usr/bin/env python
"""Drawdown-profile diagnostic for the four finalists (§11k). DIAGNOSTIC ONLY.

Max drawdown is one number describing one day. It cannot distinguish a book that
falls 50% and recovers in four months from one that falls 50% and takes three
years, and it says nothing about how *often* the investor is underwater. This
measures the shape.

Same period, same costs, same mark-to-market engine and the same return series
as §11k: dev only, one common window, blends rebalanced monthly and charged for
it. Nothing is optimised, no parameter is searched, no holdout budget is spent
and no trial is registered.

    python scripts/drawdown_profile.py
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

DEV_END = date(2021, 12, 31)
CAPITAL = 200_000.0
ENTRY, EXIT, CADENCE = 30, 60, "monthly"
CARD = costs.load_rate_cards(REPO / "config/costs.yaml")["delivery"]
SLIP = costs.load_slippage(REPO / "config/costs.yaml")
BENCH_NAME = "NIFTY buy & hold"


# --------------------------------------------------------- stream building ----
def sleeve(kind: str, *, long_side: str, **params) -> pl.DataFrame:
    """One long-only book on the audited engine. Returns realisation-dated
    net returns, which is what streams must be aligned on (§11d)."""
    elig = st.restrict_to_universe(features.add(LIQ, kind, **params), MEMBERS)
    w = buf.buffered_long_only(
        elig, entry=ENTRY, exit_=EXIT, cadence=CADENCE,
        descending=(long_side == "high"),
    ).filter(pl.col("date") <= DEV_END)
    r = engine.run(weights=w, panel=PANEL, capital=CAPITAL, card=CARD,
                   slippage=SLIP, tier="mid",
                   trade_dates=engine.implied_trade_dates(w))
    return r.equity.select(
        pl.col("exit_date").alias("date"), pl.col("net_return").alias("r")
    ).drop_nulls("date").sort("date")


def blend(a: pl.DataFrame, b: pl.DataFrame, wa: float, *, cost_bps: float):
    """Two sleeves side by side, drifting between monthly rebalances, charged
    for the correction. Identical to §11k's construction."""
    j = a.join(b, on="date", how="inner", suffix="_b").sort("date")
    rebal = buf.rebalance_dates(j["date"].to_list(), CADENCE)
    ca, cb = CAPITAL * wa, CAPITAL * (1 - wa)
    rows = []
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
mom = sleeve("momentum", long_side="high", lookback=252, skip=21)
lv = sleeve("realised_vol", long_side="low", lookback=60)
nifty = (
    BENCH.drop_nulls("ret").filter(pl.col("date") <= DEV_END).sort("date")
    .select("date", pl.col("ret").alias("r"))
)

STREAMS = {
    BENCH_NAME: nifty,
    "Momentum": mom,
    "25/75 Mom + LowVol": blend(mom, lv, 0.25, cost_bps=39.2),
    "25/75 Mom + NIFTY": blend(mom, nifty, 0.25, cost_bps=31.5),
}

common = None
for s in STREAMS.values():
    d = s.select("date")
    common = d if common is None else common.join(d, on="date", how="inner")
common = common.sort("date")

CURVES: dict[str, pl.DataFrame] = {}
for name, s in STREAMS.items():
    CURVES[name] = (
        common.join(s, on="date", how="left")
        .sort("date")
        .with_columns((CAPITAL * (1 + pl.col("r")).cum_prod()).alias("capital"))
    )

NB = CURVES[BENCH_NAME]
years = (common["date"].max() - common["date"].min()).days / 365.25
print(f"  {common.height:,} common sessions, "
      f"{common['date'].min()} .. {common['date'].max()} ({years:.2f} years)")
print("  DIAGNOSTIC ONLY -- no parameters searched, no holdout touched, "
      "no trial registered")

EPS = {name: dd.episodes(c) for name, c in CURVES.items()}
# Reporting threshold: 5% or deeper. Every count below is explicit about its own
# threshold, but the per-episode tables would otherwise be hundreds of one-day
# dips and unreadable.
MATERIAL = {name: [e for e in eps if abs(e.depth) >= 0.05]
            for name, eps in EPS.items()}


def head(n, title: str) -> None:
    print(f"\n{'='*100}\n{n}. {title}\n{'='*100}")


# ------------------------------------------------------------ 1. MAX DD -------
head(1, "MAXIMUM DRAWDOWN")
print(f"{'strategy':<22}{'max DD':>9}{'start':>13}{'trough':>13}{'recovery':>13}"
      f"{'sessions':>10}{'calendar days':>15}")
for name, eps in EPS.items():
    if not eps:
        continue
    w = min(eps, key=lambda e: e.depth)
    rec = str(w.recovery) if w.recovered else "NEVER"
    cal = ((w.recovery if w.recovered else common["date"].max()) - w.start).days
    print(f"{name:<22}{w.depth:>9.1%}{str(w.start):>13}{str(w.trough):>13}"
          f"{rec:>13}{w.duration:>10}{cal:>15}")


# --------------------------------------------------------- 2. FREQUENCY -------
head(2, "DRAWDOWN FREQUENCY -- how OFTEN, not how deep")
TH = (0.10, 0.20, 0.30, 0.40)
print(f"{'strategy':<22}" + "".join(f"{'>'+f'{int(t*100)}%':>10}" for t in TH)
      + f"{'episodes >5%':>15}{'per year >10%':>15}")
for name, eps in EPS.items():
    c = dd.count_deeper_than(eps, *TH)
    print(f"{name:<22}" + "".join(f"{c[t]:>10}" for t in TH)
          + f"{len(MATERIAL[name]):>15}{c[0.10]/years:>15.2f}")


# ---------------------------------------------------- 3. TIME UNDERWATER ------
head(3, "TIME UNDERWATER -- does it spend materially longer below its high?")
print(f"{'strategy':<22}{'% underwater':>14}{'avg recovery':>14}"
      f"{'median recovery':>17}{'longest recovery':>18}{'unrecovered':>13}")
for name, c in CURVES.items():
    recs = [e.to_recover for e in MATERIAL[name] if e.recovered]
    open_ = [e for e in MATERIAL[name] if not e.recovered]
    longest = max(recs) if recs else 0
    if open_:
        # An open episode's recovery is unbounded below, not zero. Report the
        # elapsed time and mark it, rather than letting it vanish from the max.
        longest = max(longest, max(e.duration - e.to_trough for e in open_))
    med = sorted(recs)[len(recs) // 2] if recs else 0
    print(f"{name:<22}{dd.underwater_fraction(c['capital']):>14.1%}"
          f"{(sum(recs)/len(recs) if recs else 0):>14.0f}"
          f"{med:>17}{longest:>18}"
          f"{('yes -- ' + str(len(open_))) if open_ else 'no':>13}")
print("\n(recovery measured in SESSIONS from trough back to the prior peak, "
      "episodes >=5% only)")


# ----------------------------------------------- 4. SEVERITY DISTRIBUTION -----
head(4, "DRAWDOWN SEVERITY DISTRIBUTION (episodes >= 5%)")
print(f"{'strategy':<22}{'n':>5}{'average':>10}{'median':>10}{'90th pct':>11}"
      f"{'worst':>9}{'avg duration':>14}{'avg recovery':>14}")
for name, eps in MATERIAL.items():
    if not eps:
        continue
    depths = sorted(abs(e.depth) for e in eps)
    n = len(depths)
    p90 = depths[min(int(0.90 * n), n - 1)]
    recs = [e.to_recover for e in eps if e.recovered]
    print(f"{name:<22}{n:>5}{sum(depths)/n:>10.1%}{depths[n//2]:>10.1%}"
          f"{p90:>11.1%}{depths[-1]:>9.1%}"
          f"{sum(e.duration for e in eps)/n:>14.0f}"
          f"{(sum(recs)/len(recs) if recs else 0):>14.0f}")

for name, eps in MATERIAL.items():
    if name == BENCH_NAME or not eps:
        continue
    print(f"\n--- {name}: every episode >= 10% ---")
    print(f"{'start':<13}{'trough':<13}{'recovery':<13}{'depth':>8}"
          f"{'duration':>10}{'to trough':>11}{'to recover':>12}")
    for e in sorted(eps, key=lambda x: x.depth):
        if abs(e.depth) < 0.10:
            continue
        print(f"{str(e.start):<13}{str(e.trough):<13}"
              f"{(str(e.recovery) if e.recovered else 'NEVER'):<13}"
              f"{e.depth:>8.1%}{e.duration:>10}{e.to_trough:>11}"
              f"{(e.to_recover if e.recovered else '--'):>12}")


# --------------------------------------------- 5. RELATIVE DRAWDOWN -----------
head(5, "RELATIVE DRAWDOWN vs NIFTY -- market-driven or factor-specific?")
print("For each strategy episode, NIFTY's OWN worst peak-to-trough over the same")
print("calendar window. Point-to-point would score a round trip as harmless.\n")
for name, eps in MATERIAL.items():
    if name == BENCH_NAME:
        continue
    rows = [e for e in eps if abs(e.depth) >= 0.10]
    if not rows:
        continue
    print(f"--- {name} ---")
    print(f"{'start':<13}{'trough':<13}{'strategy DD':>13}{'NIFTY DD':>11}"
          f"{'excess DD':>12}{'verdict':>18}")
    factor = market = 0
    for e in sorted(rows, key=lambda x: x.depth):
        end = e.recovery if e.recovered else common["date"].max()
        nd = dd.benchmark_move(NB, e.start, end)
        excess = e.depth - nd
        # "Market-driven" means the index fell most of the way with it. The 60%
        # line is a judgement, stated rather than hidden.
        driven = nd < 0 and (nd / e.depth) >= 0.60
        market += driven
        factor += not driven
        print(f"{str(e.start):<13}{str(e.trough):<13}{e.depth:>13.1%}"
              f"{nd:>11.1%}{excess:>12.1%}"
              f"{('market-driven' if driven else 'FACTOR-SPECIFIC'):>18}")
    print(f"  -> {market} market-driven, {factor} factor-specific "
          f"of {market+factor}\n")


# ------------------------------------------------- 6. DOWNSIDE CAPTURE --------
head(6, "DOWNSIDE CAPTURE -- behaviour on the market's bad days")
nb_r = NB["r"]
down = int((nb_r < 0).sum())
print(f"NIFTY fell on {down:,} of {common.height:,} sessions ({down/common.height:.1%})\n")
print(f"{'strategy':<22}{'downside':>10}{'upside':>9}{'up/down':>9}"
      f"{'monthly down':>14}{'monthly up':>12}{'reading':>18}")
for name, c in CURVES.items():
    dcap = dd.downside_capture(c["r"], nb_r)
    ucap = dd.upside_capture(c["r"], nb_r)
    md = dd.capture_by_period(c["date"], c["r"], nb_r, down=True)
    mu = dd.capture_by_period(c["date"], c["r"], nb_r, down=False)
    reading = ("defensive" if dcap < 0.95 else
               "market-like" if dcap <= 1.05 else "AMPLIFIES")
    print(f"{name:<22}{dcap:>10.2f}{ucap:>9.2f}{ucap/dcap if dcap else 0:>9.2f}"
          f"{md:>14.2f}{mu:>12.2f}{reading:>18}")
print("\nDaily figures are ratios of MEAN returns on the benchmark's down days.")
print("Compounding across non-contiguous down days is degenerate -- it drives both")
print("cumulatives to -100% and returns 1.00 for every book. The monthly columns")
print("compound WITHIN contiguous months, which is legitimate, and agree.")
print("\nA low downside capture bought with an equally low upside capture is a")
print("smaller position, not a defensive one -- hence the up/down ratio.")


# ------------------------------------------------------ 7. ULCER INDEX --------
head(7, "ULCER INDEX -- depth AND duration in one number")
print(f"{'strategy':<22}{'ulcer index':>13}{'CAGR':>9}{'Martin ratio':>14}"
       f"{'Sharpe':>9}{'max DD':>9}")
SUMMARY = {}
for name, c in CURVES.items():
    total = float(c["capital"][-1]) / CAPITAL
    cagr = total ** (1 / years) - 1
    vol = float(c["r"].std() or 0.0) * (250**0.5)
    ui = dd.ulcer_index(c["capital"])
    daily = c.select("date", pl.col("r").alias("net_return"))
    # 20% STCG annually for the active books, one 12.5% LTCG event on exit for
    # buy-and-hold (§11k). Ulcer-adjusting the PRE-tax return would credit the
    # active books with money they never keep.
    at = metrics.after_tax_cagr(daily, deferred=name == BENCH_NAME,
                                deferred_rate=0.125)
    SUMMARY[name] = {
        "cagr": cagr, "after_tax": at, "sharpe": cagr / vol if vol else 0.0,
        "ulcer": ui, "martin": dd.martin_ratio(cagr, c["capital"]),
        "martin_at": dd.martin_ratio(at, c["capital"]),
        "maxdd": metrics.max_drawdown(c["capital"]),
        "underwater": dd.underwater_fraction(c["capital"]),
        "dc": dd.downside_capture(c["r"], nb_r),
    }
    s = SUMMARY[name]
    print(f"{name:<22}{ui:>13.1f}{cagr:>9.1%}{s['martin']:>14.2f}"
          f"{s['sharpe']:>9.2f}{s['maxdd']:>9.1%}")

print(f"\n{'strategy':<22}{'after-tax CAGR':>16}{'after-tax Martin':>19}")
for name in sorted(SUMMARY, key=lambda k: -SUMMARY[k]["martin_at"]):
    s = SUMMARY[name]
    print(f"{name:<22}{s['after_tax']:>16.1%}{s['martin_at']:>19.2f}")
print("\nMartin ratio = return per unit of ULCER, i.e. per unit of depth-and-")
print("duration rather than per unit of volatility. On after-tax return it is")
print("the closest single number to 'what the investor kept, per unit of pain'.")


# ------------------------------------- 7b. IS IT ONE EPISODE OR THE SHAPE? ----
head("7b", "ONE BAD EPISODE, OR A GENUINELY WORSE SHAPE?")
print("Ulcer index recomputed with each book's single worst episode excised. If")
print("the gap closes, the book is normal with one disaster; if it persists, the")
print("book is structurally more painful.\n")
print(f"{'strategy':<22}{'ulcer (full)':>14}{'ulcer (ex-worst)':>18}"
      f"{'share from worst':>18}")
for name, c in CURVES.items():
    eps = EPS[name]
    if not eps:
        continue
    w = min(eps, key=lambda e: e.depth)
    end = w.recovery if w.recovered else common["date"].max()
    kept = c.filter(~pl.col("date").is_between(w.start, end))
    # Re-chain the surviving returns: excising a window from a CAPITAL series
    # would leave a step, so rebuild from the returns.
    rebuilt = kept.with_columns(
        (CAPITAL * (1 + pl.col("r")).cum_prod()).alias("capital")
    )
    full = dd.ulcer_index(c["capital"])
    ex = dd.ulcer_index(rebuilt["capital"])
    print(f"{name:<22}{full:>14.1f}{ex:>18.1f}{1 - ex / full:>18.0%}")


# ------------------------------------------------------ 8. SCORECARD ----------
head(8, "RISK EXPERIENCE SCORECARD")
print(f"{'strategy':<22}{'max DD':>9}{'DD>20%':>8}{'median DD':>11}"
      f"{'underwater':>12}{'longest rec':>13}{'down cap':>10}{'ulcer':>8}"
      f"{'Martin(AT)':>12}")
for name in CURVES:
    s = SUMMARY[name]
    n20 = dd.count_deeper_than(EPS[name], 0.20)[0.20]
    recs = [e.to_recover for e in MATERIAL[name] if e.recovered]
    open_ = [e for e in MATERIAL[name] if not e.recovered]
    longest = max(recs) if recs else 0
    if open_:
        longest = max(longest, max(e.duration - e.to_trough for e in open_))
    depths = sorted(abs(e.depth) for e in MATERIAL[name])
    med = depths[len(depths) // 2] if depths else 0.0
    print(f"{name:<22}{s['maxdd']:>9.1%}{n20:>8}{med:>11.1%}"
          f"{s['underwater']:>12.1%}{longest:>13}{s['dc']:>10.2f}"
          f"{s['ulcer']:>8.1f}{s['martin_at']:>12.2f}")

with (REPO / "config" / "trials.jsonl").open("a", encoding="utf-8") as fh:
    fh.write(json.dumps({
        "kind": "exploration",
        "note": "drawdown-profile diagnostic on 4 EXISTING configs; measurement "
                "only, no parameters searched -- denominator unchanged",
        "configs_explored": 0, "registered_as_trials": 0,
    }, sort_keys=True) + "\n")
print("\n0 new configs explored; diagnostic only, denominator unchanged")
