#!/usr/bin/env python
"""Phase 0a exit gate: reproduce a known published effect (PROJECT.md §7).

Before trusting the backtester on unknown strategies, check it against one whose
sign and rough magnitude are already documented. Short-term cross-sectional
reversal is the natural choice -- it is among the most robust anomalies in the
literature and is confirmed on NSE.

Same principle as putting a thermometer in ice water. If a documented effect
cannot be reproduced, the instrument is broken, and that is far better learned
now than after betting on it.

**This is not a strategy proposal.** Reversal is explicitly listed in §4 as
cost-unfriendly: high turnover against a ~30 bps delivery round trip. The point
is calibration, and the gross-vs-net gap here is itself the lesson.

    python scripts/calibrate_backtester.py
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

import polars as pl

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from algotrade.backtest import engine, metrics  # noqa: E402
from algotrade.core import actions as ca  # noqa: E402
from algotrade.core import adjust as adj  # noqa: E402
from algotrade.core import costs, integrity as ig, portfolio as pf  # noqa: E402
from algotrade.core import reconcile as rc, universe as uv  # noqa: E402
from algotrade.io import actions as astore  # noqa: E402
from algotrade.io import store  # noqa: E402


def build_weights(
    panel: pl.DataFrame, members: pl.DataFrame, *, lookback: int, n_side: int
) -> pl.DataFrame:
    """Long the worst `n_side` performers, short the best, equal weight.

    Parameters come from reasoning, not optimisation (§8.4): a 5-session
    lookback because that is a week. No sweep is run here -- that would spend
    trial budget on a calibration exercise.
    """
    # Universe membership is monthly; forward-fill it onto every session.
    monthly = members.select("date", "symbol").rename({"date": "rebal"})
    sessions = panel.select("date").unique().sort("date")
    span = sessions.join(
        monthly.select(pl.col("rebal")).unique(), how="cross"
    ).filter(pl.col("rebal") <= pl.col("date"))
    latest = span.group_by("date").agg(pl.col("rebal").max())
    eligible = latest.join(monthly, on="rebal").select("date", "symbol")

    scored = (
        panel.join(eligible, on=["date", "symbol"], how="inner")
        .sort(["symbol", "date"])
        .with_columns(
            (pl.col("close") / pl.col("close").shift(lookback).over("symbol") - 1)
            .alias("past_return")
        )
        .filter(pl.col("past_return").is_not_null())
    )

    ranked = scored.with_columns(
        pl.col("past_return").rank("ordinal").over("date").alias("rank"),
        pl.len().over("date").alias("n"),
    ).filter(pl.col("n") >= 4 * n_side)

    longs = ranked.filter(pl.col("rank") <= n_side).with_columns(
        pl.lit(0.5 / n_side).alias("weight")
    )
    shorts = ranked.filter(pl.col("rank") > pl.col("n") - n_side).with_columns(
        pl.lit(-0.5 / n_side).alias("weight")
    )
    return pl.concat([longs, shorts]).select("date", "symbol", "weight").sort(
        ["date", "symbol"]
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lookback", type=int, default=5)
    ap.add_argument("--n-side", type=int, default=15)
    ap.add_argument("--capital", type=float, default=200_000)
    ap.add_argument("--dev-end", type=date.fromisoformat, default=date(2021, 12, 31))
    args = ap.parse_args()

    print("loading panel ...", flush=True)
    raw = store.read(REPO / "data/curated")
    feed = ca.classify(astore.load(REPO / "data/raw/actions"))
    feed = feed.filter(pl.col("price_factor").is_not_null())

    print("adjusting for corporate actions ...", flush=True)
    chains = rc.merge_chains(
        rc.resolve_chains(ig.find_renames(raw)),
        rc.chains_from_isin(astore.isin_map(astore.load(REPO / "data/raw/actions"))),
    )
    result = rc.reconcile(ig.find_actions(raw), feed, chains=chains)
    factors = adj.factors_from_reconciliation(result)
    panel = adj.adjust(raw, factors)
    panel = adj.drop_windows(panel, adj.unadjusted_windows(result))
    print(f"  {factors.height} factors applied, {panel.height:,} rows retained")

    print("selecting universe ...", flush=True)
    liq = uv.liquidity(panel)
    members = uv.membership(liq)
    print(f"  {members['date'].n_unique()} rebalances, "
          f"{members['symbol'].n_unique():,} distinct names")

    print("building weights ...", flush=True)
    weights = build_weights(
        liq, members, lookback=args.lookback, n_side=args.n_side
    )
    print(f"  {weights.height:,} positions over {weights['date'].n_unique():,} sessions")

    card = costs.load_rate_cards(REPO / "config/costs.yaml")["delivery"]
    slip = costs.load_slippage(REPO / "config/costs.yaml")

    # DEVELOPMENT SET ONLY. The 2022-2026 holdout is not touched here -- it has
    # 3-5 looks for the entire project (§8.3) and a calibration run is not one.
    dev = weights.filter(pl.col("date") <= args.dev_end)
    print(f"\ndevelopment set only: {dev['date'].min()} .. {dev['date'].max()}")
    print("(the 2022-2026 holdout is deliberately untouched -- section 8.3)")

    # Turnover control is the decisive lever (section 11a), so the cadences are
    # compared directly rather than asserted.
    for label, cadence in (("daily", "daily"), ("weekly", "weekly"),
                           ("monthly", "monthly")):
        sized = pf.resample(dev, cadence)
        res = engine.run(
            liq, sized, capital=args.capital, card=card, slippage=slip, tier="mid",
            trade_dates=engine.implied_trade_dates(sized),
        )
        m = metrics.compute(res.equity)
        conc = metrics.trade_concentration(res.equity)
        bench = metrics.vs_benchmark(res.equity)
        print(f"\n--- rebalance: {label} ---")
        print(m.summary())
        print(f"gross CAGR    {m.cagr + m.cost_drag:>8.1%}")
        print(f"beats NIFTYBEES: {bench['beats_benchmark']} "
              f"(excess {bench['excess']:+.1%})")
        print(f"survives dropping best 5 days: {conc['survives']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
