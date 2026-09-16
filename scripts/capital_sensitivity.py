#!/usr/bin/env python
"""Does the strategy's return depend on how much capital runs it? (§11g's 0.72 -> 0.92)

**Not a strategy search. One config, six capital levels.** The weights, the
universe, the buffer and the cadence are identical in every row -- only the
rupees change. No parameter is being selected, so the deflation denominator is
untouched, same as `remeasure_finalists.py` and `remeasure_vol_target.py`.

§11g measured net Sharpe 0.72 at Rs 2L rising to 0.92 at Rs 10L and then flat,
and §11's conclusion leans on it ("Rs 10L+ becomes a precondition rather than a
preference"). But that figure predates §11j and §11k, it is a *Sharpe*, and the
natural question is about CAGR. This measures both, on the audited engine.

### Why capital can matter at all

Two costs pull in opposite directions as size grows:

* **Flat fees stop mattering.** The DP charge is Rs 15.34 per scrip per sell --
  a fixed rupee amount. Across 30 positions at Rs 2L each position is ~Rs 6,700,
  so one DP charge is ~23 bps of it. At Rs 50L the same charge is ~0.9 bps. This
  is the entire "bigger is better" effect and it saturates quickly.
* **Market impact starts mattering.** `costs.slippage_bps` applies the
  square-root law, `k * vol * sqrt(value / adtv)`. Impact is negligible while
  participation is tiny and grows without bound as the book approaches the
  traded volume of the names it holds.

So the curve should rise, flatten, and eventually fall. Where it turns is a
property of the liquid-300's actual turnover, not an assumption -- which is why
it is worth measuring rather than reasoning about.

**Slippage is an assumption, not a measurement** (config/costs.yaml says so).
The high-capital rows inherit that caveat and inherit it *harder*, because they
lean on the impact term that has never been calibrated against real fills.

    python scripts/capital_sensitivity.py
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
ENTRY, EXIT, CADENCE = 30, 60, "monthly"
CARD = costs.load_rate_cards(REPO / "config/costs.yaml")["delivery"]
SLIP = costs.load_slippage(REPO / "config/costs.yaml")
FREE = costs.RateCard("free", 0, None, 0, 0, 0, 0, 0, 0, 0)
STCG = 0.20

#: Deployed today, the §11g reference point, and enough beyond it to find the turn.
LEVELS = (200_000.0, 500_000.0, 1_000_000.0, 2_500_000.0,
          5_000_000.0, 10_000_000.0, 50_000_000.0)


@dataclass
class Row:
    capital: float
    cagr: float
    gross_cagr: float
    sharpe: float
    maxdd: float
    after_tax: float
    drag: float
    bps: float


def realised_bps(blotter: pl.DataFrame) -> float:
    """Cost per rupee traded. The rate card is not bps -- DP is flat per scrip --
    so this is the only honest way to state the rate (§11j)."""
    if blotter.is_empty():
        return 0.0
    traded = float(blotter["value"].sum())
    if traded <= 0:
        return 0.0
    return float((blotter["charges"] + blotter["slippage"]).sum()) / traded * 10_000


def main() -> int:
    elig = st.restrict_to_universe(features.add(LIQ, "momentum"), MEMBERS)
    weights = buf.buffered_long_only(
        elig, entry=ENTRY, exit_=EXIT, cadence=CADENCE, descending=True,
    ).filter(pl.col("date") <= DEV_END)
    trade_dates = engine.implied_trade_dates(weights)

    rows: list[Row] = []
    for capital in LEVELS:
        net = engine.run(weights=weights, panel=PANEL, capital=capital,
                         card=CARD, slippage=SLIP, tier="mid",
                         trade_dates=trade_dates)
        gross = engine.run(weights=weights, panel=PANEL, capital=capital,
                           card=FREE, slippage=None, tier="mid",
                           trade_dates=trade_dates)
        m, g = metrics.compute(net.equity, risk_free=0.0), metrics.compute(
            gross.equity, risk_free=0.0)
        daily = net.equity.select(
            pl.col("exit_date" if "exit_date" in net.equity.columns
                   else "date").alias("date"),
            "net_return",
        ).drop_nulls("date")
        rows.append(Row(
            capital=capital, cagr=m.cagr, gross_cagr=g.cagr, sharpe=m.sharpe,
            maxdd=m.max_drawdown,
            after_tax=metrics.after_tax_cagr(daily, rate=STCG),
            drag=g.cagr - m.cagr, bps=realised_bps(net.blotter),
        ))

    print(f"\nMomentum 30, buffer {ENTRY}/{EXIT}, {CADENCE} -- identical weights, "
          f"capital varied (dev set, through {DEV_END})")
    print("=" * 96)
    print(f"{'capital':>12}{'gross CAGR':>12}{'net CAGR':>10}{'cost drag':>11}"
          f"{'bps':>7}{'SR':>7}{'maxDD':>9}{'after-tax':>11}")
    for r in rows:
        print(f"{'Rs ' + f'{r.capital/100000:.0f}L':>12}{r.gross_cagr:>12.1%}"
              f"{r.cagr:>10.1%}{r.drag * 100:>10.1f}pp{r.bps:>7.1f}"
              f"{r.sharpe:>7.2f}{r.maxdd:>9.1%}{r.after_tax:>11.1%}")

    base = rows[0]
    best = max(rows, key=lambda r: r.cagr)
    print(f"\n--- versus the Rs {base.capital/100000:.0f}L the book runs today ---")
    print(f"{'capital':>12}{'net CAGR':>12}{'vs Rs 2L':>12}{'drag saved':>13}")
    for r in rows[1:]:
        print(f"{'Rs ' + f'{r.capital/100000:.0f}L':>12}{r.cagr:>12.1%}"
              f"{(r.cagr - base.cagr) * 100:>+10.1f}pp"
              f"{(base.drag - r.drag) * 100:>11.1f}pp")
    spread = max(r.gross_cagr for r in rows) - min(r.gross_cagr for r in rows)
    print(f"\nGross CAGR spread across all levels: {spread*100:.2f}pp "
          f"-- the strategy does not change, only what it pays.")
    print(f"Best net CAGR at Rs {best.capital/100000:.0f}L ({best.cagr:.1%}).")

    with (REPO / "config" / "trials.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "kind": "exploration",
            "note": "capital sensitivity of ONE existing config (momentum 30, "
                    "buffer 30/60, monthly) across 7 capital levels; cost-model "
                    "sensitivity, not a strategy search -- denominator unchanged",
            "configs_explored": 0, "registered_as_trials": 0,
        }, sort_keys=True) + "\n")
    print("\n0 new configs: one strategy, varying capital -- denominator unchanged")
    return 0


PANEL = rh.load_adjusted_panel()
LIQ = uv.liquidity(PANEL)
MEMBERS = uv.membership(LIQ, exclude=set(uv.DEFAULT_EXCLUSIONS))
BENCH = indices.benchmark_series(REPO / "data/raw/indices")


if __name__ == "__main__":
    raise SystemExit(main())
