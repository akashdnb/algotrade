#!/usr/bin/env python
"""Re-measure vol targeting on the audited engine (§11g's one survivor).

**Not research. No new factors, no search, no parameter sweep.** Exactly the
same precedent as `remeasure_finalists.py`: §11k re-ran the four blend
finalists because the cost pillar moved under them, and left the deflation
denominator untouched. This does the same for the one risk lever that worked.

### Why this one config and no others

§11g tested five ways to tame momentum's drawdown. Four are dead and stay dead:

    stock > 200DMA        no effect on DD (-53%), turnover nearly doubles
    inverse-vol weights   no effect (-55%)  -- refutes "volatile names cause it"
    ERC approximation     no effect (-55%)
    market trend filter   ARTEFACT (§11h). Honest version COSTS 0.11 Sharpe.

Only **vol targeting** moved drawdown: -54% -> -23%, Sharpe 0.80 -> 0.74.

But that measurement predates two corrections that change every number:

* **§11j** -- the engine re-equalised the book daily between monthly
  rebalances, understating every net-of-cost figure.
* **§11k** -- CAGR was annualised by row count rather than calendar span, and
  the panel/index windows differ by ~135 sessions.

So the single surviving drawdown lever has never been measured on the engine
that produced the deployed numbers. That is a gap worth closing, and closing it
requires no new hypothesis.

### What is deliberately NOT done here

**No new parameter values.** The four targets below are exactly the four
`sprint_risk.py` already swept -- 8/10/12/15%. Adding a fifth would be a search
and would move the deflation denominator, which is the discipline §8.2 exists to
enforce. Four existing configs, re-run. Zero new.

**Exposure is scaled on the WEIGHTS, not on the return series.** Scaling returns
silently makes the exposure change free; scaling weights and re-running the
engine charges for the trades it causes. That is the same class of error §11j
found in the engine and §11k found one level up in `sprint_blends.py`, and it
would flatter every number here.

**Nothing touches the holdout.** Dev set only, through 2021-12-31. §11's holdout
status is explicit that 2022-2026 is *already* compromised for drawdown-shape
statistics specifically -- which is precisely what this measures -- so a
"confirmation" there would be theatre rather than evidence.

    python scripts/remeasure_vol_target.py
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
ENTRY, EXIT, CADENCE = 30, 60, "monthly"
CARD = costs.load_rate_cards(REPO / "config/costs.yaml")["delivery"]
SLIP = costs.load_slippage(REPO / "config/costs.yaml")
FREE = costs.RateCard("free", 0, None, 0, 0, 0, 0, 0, 0, 0)

STCG = 0.20

#: Exactly the four `sprint_risk.py` swept. Not a new sweep -- see the docstring.
TARGETS = (0.08, 0.10, 0.12, 0.15)
VOL_LOOKBACK = 60


@dataclass
class Row:
    label: str
    cagr: float
    sharpe: float
    maxdd: float
    after_tax_cagr: float
    exposure: float
    excess: float


def _years(daily: pl.DataFrame) -> float:
    """Calendar span, not row count (§11k)."""
    days = (daily["date"].max() - daily["date"].min()).days
    return days / 365.25 if days > 0 else daily.height / 250


def momentum_weights() -> pl.DataFrame:
    elig = st.restrict_to_universe(features.add(LIQ, "momentum"), MEMBERS)
    return buf.buffered_long_only(
        elig, entry=ENTRY, exit_=EXIT, cadence=CADENCE, descending=True,
    ).filter(pl.col("date") <= DEV_END)


def summarise(label: str, result, *, exposure: float, equity=None) -> Row:
    eq = equity if equity is not None else result.equity
    m = metrics.compute(eq, risk_free=0.0)
    b = metrics.vs_benchmark_series(eq, BENCH)
    daily = eq.select(
        pl.col("exit_date" if "exit_date" in eq.columns else "date").alias("date"),
        "net_return",
    ).drop_nulls("date")
    return Row(
        label=label,
        cagr=m.cagr,
        sharpe=m.sharpe,
        maxdd=m.max_drawdown,
        after_tax_cagr=metrics.after_tax_cagr(daily, rate=STCG),
        exposure=exposure,
        excess=b.get("excess", float("nan")),
    )


def run(weights: pl.DataFrame):
    td = engine.implied_trade_dates(weights)
    return engine.run(weights=weights, panel=PANEL, capital=CAPITAL, card=CARD,
                      slippage=SLIP, tier="mid", trade_dates=td)


def main() -> int:
    base_w = momentum_weights()
    base_run = run(base_w)
    results = [summarise("momentum (unlevered)", base_run, exposure=1.0)]

    unlev = base_run.equity.select("date", "net_return")
    for target in TARGETS:
        # `vol_scalar` shifts by two periods so the window cannot see the return
        # it is scaling -- look-ahead that still looks plausible (§11).
        #
        # max_leverage=1.0: this book has no leverage available, so vol
        # targeting can only ever de-risk. Measuring it geared up would price an
        # instrument that does not exist at Rs 2L.
        lev = pf.vol_scalar(unlev, target=target, lookback=VOL_LOOKBACK,
                            max_leverage=1.0)
        scaled_w = pf.scale(base_w, lev)
        if scaled_w.is_empty():
            continue
        r = run(scaled_w)
        exposure = float(
            lev["leverage"].drop_nulls().mean() or 1.0
        )
        results.append(
            summarise(f"+ vol target {target:.0%}", r, exposure=exposure)
        )

    # No separate NIFTY row: `vs_benchmark_series` already compares each config
    # against the index over that config's OWN window, which is the comparison
    # that matters and avoids re-deriving an equity curve from a return series.

    print("\nVol targeting re-measured on the audited engine "
          f"(dev set, through {DEV_END})")
    print("=" * 92)
    print(f"{'config':<26}{'CAGR':>8}{'SR':>7}{'maxDD':>9}"
          f"{'after-tax':>11}{'exposure':>10}{'vs NIFTY':>11}")
    for r in results:
        print(f"{r.label:<26}{r.cagr:>8.1%}{r.sharpe:>7.2f}{r.maxdd:>9.1%}"
              f"{r.after_tax_cagr:>11.1%}{r.exposure:>10.0%}{r.excess:>+11.1%}")

    base = results[0]
    print("\n--- what the de-risking buys, and what it costs ---")
    print(f"{'config':<26}{'DD cut':>10}{'CAGR':>14}{'after-tax':>14}")
    for r in results[1:]:
        print(f"{r.label:<26}{base.maxdd - r.maxdd:>+10.1%}"
              f"{(r.cagr - base.cagr) * 100:>12.1f}pp"
              f"{(r.after_tax_cagr - base.after_tax_cagr) * 100:>12.1f}pp")

    with (REPO / "config" / "trials.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "kind": "exploration",
            "note": "re-measurement of the 4 EXISTING vol-target configs "
                    "(8/10/12/15%) sprint_risk.py already swept, on the "
                    "audited engine per 11j/11k; no sweep, no new configs -- "
                    "denominator unchanged",
            "configs_explored": 0, "registered_as_trials": 0,
        }, sort_keys=True) + "\n")
    print("\n0 new configs: re-measurement of existing ones, denominator unchanged")
    return 0


PANEL = rh.load_adjusted_panel()
LIQ = uv.liquidity(PANEL)
MEMBERS = uv.membership(LIQ, exclude=set(uv.DEFAULT_EXCLUSIONS))
BENCH = indices.benchmark_series(REPO / "data/raw/indices")
BENCH_EQ = BENCH.filter(pl.col("date") <= DEV_END)


if __name__ == "__main__":
    raise SystemExit(main())
