#!/usr/bin/env python
"""Measure what excluding NSE's `BE` series costs the momentum backtest.

**Read-only. Writes nothing, touches no database, rebuilds no cache.**

`io/bhavcopy.read_file` keeps `SERIES == 'EQ'` only. That filter exists for a
real reason -- DHFL appears eight times on 2018-01-01, once as EQ and seven
times as debt series (N6, N8, NC, NF, NL, NN, NP) -- but **`BE` is not a debt
series.** It is the same equity in NSE's trade-for-trade settlement segment,
entered under the ASM/GSM surveillance framework, and a symbol is never in both
EQ and BE on the same day, so including it cannot double-count anything.

Two reasons the exclusion is not neutral for a momentum strategy:

* **It is momentum-selective.** Names moved EQ->BE during 2026 had a median
  trailing 60-session return of +23.7% against the market's +2.0%; restricted
  to names clearing the strategy's own Rs 5 crore/day floor, +44.4%. NSE moves
  winners into BE -- exactly the right tail this strategy selects from.
* **The engine deletes the trade rather than marking it down.** A name that
  moves to BE has no further EQ rows, so `fill_open`/`exit_open` go null and
  `engine.run` filters the whole period out. The position vanishes from the
  blotter instead of being valued.

This runs the identical pipeline on two panels built from the SAME raw files --
one EQ-only, one EQ+BE -- and reports the difference.

    python scripts/measure_series_filter.py
    python scripts/measure_series_filter.py --sessions-only   # committed data

**A caution about how to read the output.** Choosing the data definition that
backtests better is a researcher degree of freedom, and §8's pre-registration
discipline exists to refuse exactly that. If the EQ+BE arm looks better, that
is not evidence for adopting it -- the case for BE rests on it being the same
equity with a momentum-shaped hole, which was true before this ran. Use these
numbers to size the change, never to justify it.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

import polars as pl

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from algotrade.backtest import engine, metrics  # noqa: E402
from algotrade.core import actions as ca  # noqa: E402
from algotrade.core import adjust as adj  # noqa: E402
from algotrade.core import buffering as buf  # noqa: E402
from algotrade.core import costs, features, integrity as ig  # noqa: E402
from algotrade.core import reconcile as rc, strategies as st, universe as uv  # noqa: E402
from algotrade.io import actions as astore, archive  # noqa: E402

CAPITAL = 200_000.0
ENTRY, EXIT, CADENCE = 30, 60, "monthly"
LOOKBACK, SKIP = 252, 21

#: The committed sessions alone cover ~2024 onward. The pre-2024 history lives
#: in these two directories, which are NOT in git and NOT in the
#: `market-archive-v1` release (which does not exist -- see
#: claude_fixes/2026-09-04-archive-release-missing.md). Without them the test
#: window is ~10 months after momentum's 273-session warm-up, which is too
#: short to conclude anything.
FULL_DIRS = [
    REPO / "data" / "sessions",
    REPO / "data" / "raw" / "nse-data-bank" / "data",
    REPO / "data" / "raw" / "backfill",
]


def adjusted(dirs, series) -> tuple[pl.DataFrame, int]:
    """The same adjusted panel `run_hypothesis.load_adjusted_panel` builds,
    but from raw files with an explicit series filter rather than from the
    EQ-only curated store."""
    raw, report = archive.load_panel(dirs, series=series)
    feed_raw = astore.load(REPO / "data/raw/actions")
    feed = ca.classify(feed_raw).filter(pl.col("price_factor").is_not_null())
    chains = rc.merge_chains(
        rc.resolve_chains(ig.find_renames(raw)),
        rc.chains_from_isin(astore.isin_map(feed_raw)),
    )
    result = rc.reconcile(ig.find_actions(raw), feed, chains=chains)
    panel = adj.adjust(raw, adj.factors_from_reconciliation(result))
    panel = adj.drop_windows(
        panel,
        adj.unadjusted_windows(result, sessions=raw["date"].unique().sort()))
    return panel, len(report.rejected)


def arm(dirs, series) -> dict:
    panel, rejected = adjusted(dirs, series)
    liq = uv.liquidity(panel)
    members = uv.membership(liq, exclude=set(uv.DEFAULT_EXCLUSIONS))
    elig = st.restrict_to_universe(
        features.add(liq, "momentum", lookback=LOOKBACK, skip=SKIP), members)
    weights = buf.buffered_long_only(
        elig, entry=ENTRY, exit_=EXIT, cadence=CADENCE, descending=True)
    result = engine.run(
        weights=weights, panel=panel, capital=CAPITAL,
        card=costs.load_rate_cards(REPO / "config/costs.yaml")["delivery"],
        slippage=costs.load_slippage(REPO / "config/costs.yaml"), tier="mid",
        trade_dates=engine.implied_trade_dates(weights))

    # The engine's own drop rule, replicated so the loss is countable. This is
    # the mechanism BE exclusion acts through: no exit price, no trade.
    joined = weights.join(engine._fill_prices(panel), on=["date", "symbol"],
                          how="left")
    kept = joined.filter(
        pl.col("fill_open").is_not_null() & pl.col("exit_open").is_not_null()
        & (pl.col("fill_open") > 0))

    equity = result.equity
    return {
        "panel": panel, "weights": weights, "equity": equity,
        "sessions": panel["date"].n_unique(),
        "rejected": rejected,
        "symbols": panel["symbol"].n_unique(),
        "weight_rows": weights.height,
        "dropped": joined.height - kept.height,
        "trades": result.blotter.height,
        "periods": equity.height,
        "final_capital": float(equity["capital"][-1]) if equity.height else 0.0,
        "total_return": (float(equity["capital"][-1] / CAPITAL - 1)
                         if equity.height else 0.0),
        "max_dd": float(metrics.max_drawdown(equity["capital"]))
                  if equity.height else 0.0,
        "cost": float(equity["cost"].sum()) if equity.height else 0.0,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sessions-only", action="store_true",
                    help="use only committed data/sessions (~2024 onward)")
    args = ap.parse_args()

    dirs = [REPO / "data" / "sessions"] if args.sessions_only else FULL_DIRS
    for d in dirs:
        if not d.exists():
            print(f"missing source directory: {d}", file=sys.stderr)
            return 1

    print("building EQ only ...", flush=True)
    a = arm(dirs, ("EQ",))
    print("building EQ + BE ...", flush=True)
    b = arm(dirs, ("EQ", "BE"))

    print(f"\n{'':<22}{'EQ only':>16}{'EQ + BE':>16}")
    print("-" * 54)
    for key, label, fmt in (
        ("sessions", "sessions", "{:,}"), ("symbols", "symbols", "{:,}"),
        ("weight_rows", "weight rows", "{:,}"),
        ("dropped", "dropped weights", "{:,}"),
        ("trades", "trades", "{:,}"), ("periods", "periods", "{:,}"),
        ("final_capital", "final capital", "{:,.0f}"),
        ("total_return", "total return", "{:+.1%}"),
        ("max_dd", "max drawdown", "{:.1%}"),
        ("cost", "cost drag", "{:,.0f}"),
    ):
        print(f"{label:<22}{fmt.format(a[key]):>16}{fmt.format(b[key]):>16}")

    span = a["equity"]
    if span.height:
        print(f"\nwindow {span['date'].min()} .. {span['date'].max()}")

    # How much of the difference is BE names actually held, versus the universe
    # reshuffling because liquidity is now ranked over more symbols?
    raw_all, _ = archive.load_panel(dirs, series=("EQ", "BE"))
    tagged = b["weights"].join(
        raw_all.select(["date", "symbol", "series"]), on=["date", "symbol"],
        how="left")
    in_be = tagged.filter(pl.col("series") == "BE")
    ka = set(zip(a["weights"]["date"].to_list(), a["weights"]["symbol"].to_list()))
    kb = set(zip(b["weights"]["date"].to_list(), b["weights"]["symbol"].to_list()))
    print(f"\nweight rows held while in BE : {in_be.height:,} "
          f"({100 * in_be.height / max(b['weight_rows'], 1):.2f}%), "
          f"{in_be['symbol'].n_unique()} distinct names")
    print(f"rows only in EQ arm          : {len(ka - kb):,}")
    print(f"rows only in EQ+BE arm       : {len(kb - ka):,}")
    print(f"rows common to both          : {len(ka & kb):,}")

    # Paired, because the two books overlap heavily. Reported with the caveat
    # that equity rows are per-session while holdings are constant within a
    # month, so the periods are not independent and |t| is overstated -- divide
    # n by roughly a month of sessions before believing it.
    x, y = a["equity"]["net_return"].to_list(), b["equity"]["net_return"].to_list()
    if len(x) == len(y) and len(x) > 2:
        d = [q - p for p, q in zip(x, y)]
        t = statistics.mean(d) / (statistics.stdev(d) / len(d) ** 0.5)
        print(f"\npaired mean diff/period      : {statistics.mean(d):+.6f}")
        print(f"t on {len(d)} overlapping periods : {t:+.2f}  "
              f"(effective n is nearer {len(d) // 21} months -- see docstring)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
