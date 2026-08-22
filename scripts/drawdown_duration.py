#!/usr/bin/env python
"""Duration profile of every completed drawdown episode. DIAGNOSTIC ONLY.

§11l measured drawdown depth and shape. This measures **how long**: peak to
trough to recovery, per episode, bucketed by length, for momentum against NIFTY.

Depth is what a backtest reports; duration is what an investor lives through. A
-30% fall that recovers in four months and a -30% fall that takes two years are
the same number and completely different experiences.

Window is a parameter because the archive crosses the holdout boundary
(2022-01-01). Default is dev only. `--through-holdout` extends to the end of the
archive and **is an irreversible look at held-out data** -- 2026 is the only bear
year in the sample (§11i), so this is both the most informative and the most
contaminating window available. It prints a warning and records the look.

    python scripts/drawdown_duration.py
    python scripts/drawdown_duration.py --through-holdout
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import polars as pl

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from algotrade.backtest import drawdown as dd, engine  # noqa: E402
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

# Buckets are on PEAK-TO-RECOVERY calendar duration, which is the span the
# investor is actually underwater -- not peak-to-trough, which stops measuring
# at the worst moment and ignores the climb back.
BUCKETS = [
    ("< 3 months", 0, 91),
    ("3 - 6 months", 91, 182),
    ("6 - 12 months", 182, 365),
    ("12 - 24 months", 365, 730),
    ("> 24 months", 730, 10**9),
]


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--through-holdout", action="store_true",
        help="extend past 2021-12-31 to the end of the archive (IRREVERSIBLE)",
    )
    ap.add_argument("--min-depth", type=float, default=0.05,
                    help="ignore episodes shallower than this (default 5%%)")
    return ap.parse_args()


def build(end: date | None):
    panel = rh.load_adjusted_panel()
    liq = uv.liquidity(panel)
    members = uv.membership(liq, exclude=set(uv.DEFAULT_EXCLUSIONS))
    elig = st.restrict_to_universe(
        features.add(liq, "momentum", lookback=252, skip=21), members
    )
    w = buf.buffered_long_only(elig, entry=ENTRY, exit_=EXIT, cadence=CADENCE)
    if end is not None:
        w = w.filter(pl.col("date") <= end)
    r = engine.run(weights=w, panel=panel, capital=CAPITAL, card=CARD,
                   slippage=SLIP, tier="mid",
                   trade_dates=engine.implied_trade_dates(w))
    mom = r.equity.select(
        pl.col("exit_date").alias("date"), pl.col("net_return").alias("r")
    ).drop_nulls("date").sort("date")

    bench = indices.benchmark_series(REPO / "data/raw/indices")
    nifty = bench.drop_nulls("ret").sort("date").select(
        "date", pl.col("ret").alias("r")
    )
    if end is not None:
        nifty = nifty.filter(pl.col("date") <= end)

    # One common index, so "percentage of total time" is comparable. Without it
    # the two books are measured over different spans and the shares do not mean
    # the same thing.
    common = mom.select("date").join(nifty.select("date"), on="date", how="inner")
    curves = {}
    for name, s in (("Momentum", mom), ("NIFTY", nifty)):
        curves[name] = (
            common.join(s, on="date", how="left").sort("date")
            .with_columns((CAPITAL * (1 + pl.col("r")).cum_prod()).alias("capital"))
        )
    return curves


def cal_days(e) -> int:
    """Peak-to-recovery in calendar days. Completed episodes only."""
    return (e.recovery - e.start).days


def main() -> int:
    args = parse_args()
    end = None if args.through_holdout else DEV_END

    if args.through_holdout:
        print("=" * 92)
        print("!!  LOOKING AT HELD-OUT DATA (2022-01-01 onward). This cannot be")
        print("!!  undone. 2026 is the only bear year in the archive (§11i), so")
        print("!!  what follows is the most informative and most contaminating")
        print("!!  window available. Recorded in trials.jsonl as a holdout look.")
        print("=" * 92)

    print("loading ...", flush=True)
    curves = build(end)
    span = curves["Momentum"]
    lo, hi = span["date"].min(), span["date"].max()
    total_days = (hi - lo).days
    print(f"  {span.height:,} common sessions, {lo} .. {hi} "
          f"({total_days / 365.25:.2f} years)")

    EPS, DONE = {}, {}
    for name, c in curves.items():
        eps = [e for e in dd.episodes(c) if abs(e.depth) >= args.min_depth]
        EPS[name] = eps
        # "Completed" is the question asked: an episode still underwater has no
        # recovery date and no duration, and including it with the sample end
        # substituted would invent a recovery that never happened.
        DONE[name] = [e for e in eps if e.recovered]

    # ---------------------------------------------------- every episode ------
    for name in curves:
        done, open_ = DONE[name], [e for e in EPS[name] if not e.recovered]
        print(f"\n{'='*92}\n{name}: every COMPLETED episode >= "
              f"{args.min_depth:.0%}  (n={len(done)})\n{'='*92}")
        print(f"{'peak':<12}{'trough':<12}{'recovery':<12}{'max DD':>9}"
              f"{'cal days':>10}{'sessions':>10}{'to trough':>11}{'to recover':>12}")
        for e in sorted(done, key=lambda x: x.start):
            print(f"{str(e.start):<12}{str(e.trough):<12}{str(e.recovery):<12}"
                  f"{e.depth:>9.1%}{cal_days(e):>10}{e.duration:>10}"
                  f"{e.to_trough:>11}{e.to_recover:>12}")
        for e in open_:
            print(f"{str(e.start):<12}{str(e.trough):<12}{'ONGOING':<12}"
                  f"{e.depth:>9.1%}{'--':>10}{e.duration:>10}"
                  f"{e.to_trough:>11}{'--':>12}")
        if open_:
            print(f"  ({len(open_)} episode(s) still underwater at the sample "
                  f"end, excluded from every duration statistic below)")

    # -------------------------------------------------------- buckets --------
    print(f"\n{'='*92}\nDURATION BUCKETS -- peak to recovery, completed episodes"
          f"\n{'='*92}")
    print("Counts are cumulative-free (each episode lands in exactly one bucket).")
    print("'% of total time' is the share of the whole sample spent inside "
          "episodes of that length.\n")
    for name in curves:
        done = DONE[name]
        print(f"--- {name} ---")
        print(f"{'bucket':<18}{'count':>7}{'share of eps':>14}"
              f"{'days in bucket':>16}{'% of total time':>18}")
        for label, lo_d, hi_d in BUCKETS:
            hits = [e for e in done if lo_d <= cal_days(e) < hi_d]
            days = sum(cal_days(e) for e in hits)
            print(f"{label:<18}{len(hits):>7}{len(hits)/max(len(done),1):>14.0%}"
                  f"{days:>16,}{days/total_days:>18.1%}")
        underwater = sum(cal_days(e) for e in done)
        print(f"{'TOTAL (completed)':<18}{len(done):>7}{'':>14}"
              f"{underwater:>16,}{underwater/total_days:>18.1%}\n")

    print("Episodes lasting AT LEAST each threshold (the framing asked for):\n")
    print(f"{'threshold':<16}" + "".join(f"{n:>14}" for n in curves))
    for label, d in (("3 months", 91), ("6 months", 182),
                     ("12 months", 365), ("24 months", 730)):
        row = f"{'>= ' + label:<16}"
        for name in curves:
            row += f"{sum(1 for e in DONE[name] if cal_days(e) >= d):>14}"
        print(row)

    # ----------------------------------------------------- comparison --------
    print(f"\n{'='*92}\nDURATION COMPARISON (calendar days, peak to recovery)"
          f"\n{'='*92}")
    print(f"{'strategy':<14}{'n':>5}{'average':>10}{'median':>9}{'90th pct':>11}"
          f"{'longest':>10}{'total underwater':>19}{'% of sample':>14}")
    for name in curves:
        ds = sorted(cal_days(e) for e in DONE[name])
        if not ds:
            continue
        n = len(ds)
        p90 = ds[min(int(0.90 * n), n - 1)]
        print(f"{name:<14}{n:>5}{sum(ds)/n:>10.0f}{ds[n//2]:>9}{p90:>11}"
              f"{ds[-1]:>10}{sum(ds):>19,}{sum(ds)/total_days:>14.1%}")

    print(f"\n{'strategy':<14}{'n':>5}{'average':>10}{'median':>9}{'90th pct':>11}"
          f"{'longest':>10}   (TRADING days)")
    for name in curves:
        ds = sorted(e.duration for e in DONE[name])
        if not ds:
            continue
        n = len(ds)
        print(f"{name:<14}{n:>5}{sum(ds)/n:>10.0f}{ds[n//2]:>9}"
              f"{ds[min(int(0.90*n), n-1)]:>11}{ds[-1]:>10}")

    # -------------------------------------------------- long episodes --------
    print(f"\n{'='*92}\nEVERY EPISODE LONGER THAN 6 MONTHS, by duration"
          f"\n{'='*92}")
    rows = [(name, e) for name in curves for e in DONE[name] if cal_days(e) >= 182]
    if not rows:
        print("none")
    else:
        print(f"{'strategy':<12}{'peak':<12}{'trough':<12}{'recovery':<12}"
              f"{'max DD':>9}{'cal days':>10}{'months':>9}{'to trough':>11}"
              f"{'to recover':>12}")
        for name, e in sorted(rows, key=lambda x: -cal_days(x[1])):
            print(f"{name:<12}{str(e.start):<12}{str(e.trough):<12}"
                  f"{str(e.recovery):<12}{e.depth:>9.1%}{cal_days(e):>10}"
                  f"{cal_days(e)/30.44:>9.1f}{e.to_trough:>11}{e.to_recover:>12}")

    with (REPO / "config" / "trials.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "kind": "exploration",
            "note": ("drawdown DURATION diagnostic, momentum vs NIFTY"
                     + (" -- LOOKED AT HOLDOUT DATA (2022+)"
                        if args.through_holdout else " -- dev only")),
            "configs_explored": 0, "registered_as_trials": 0,
            "touched_holdout": bool(args.through_holdout),
        }, sort_keys=True) + "\n")
    print("\n0 new configs; measurement only, denominator unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
