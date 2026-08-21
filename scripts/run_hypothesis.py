#!/usr/bin/env python
"""Run one pre-registered hypothesis end to end (PROJECT.md §8).

    python scripts/run_hypothesis.py config/hypotheses/momentum_v1.yaml
    python scripts/run_hypothesis.py config/hypotheses/momentum_v1.yaml --holdout

Pipeline: register -> load -> adjust -> universe -> feature -> weights ->
turnover control -> backtest -> gates -> deflate -> record.

Registration happens **before** the backtest runs, and the trial is recorded
even when it fails. That is the whole point: a trial log that only contains
successes has no denominator, and §8.2 shows a Sharpe is uninterpretable without
one.

`--holdout` spends one of five looks for the entire project and will refuse when
they are gone. **If a hypothesis fails on the holdout it is dead -- do not adjust
and retry.** That retry is how a rigorous-looking process produces fake results.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import polars as pl
import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from algotrade.backtest import engine, metrics, registry as reg  # noqa: E402
from algotrade.core import actions as ca  # noqa: E402
from algotrade.core import adjust as adj  # noqa: E402
from algotrade.core import costs, features, integrity as ig  # noqa: E402
from algotrade.core import portfolio as pf  # noqa: E402
from algotrade.core import reconcile as rc, strategies as st, universe as uv  # noqa: E402
from algotrade.io import actions as astore, indices, store  # noqa: E402

DEV_END = date(2021, 12, 31)
HOLDOUT_START = date(2022, 1, 1)


def load_adjusted_panel() -> pl.DataFrame:
    """Curated panel, corporate-action adjusted, unexplained windows dropped."""
    raw = store.read(REPO / "data/curated")
    feed_raw = astore.load(REPO / "data/raw/actions")
    feed = ca.classify(feed_raw).filter(pl.col("price_factor").is_not_null())
    chains = rc.merge_chains(
        rc.resolve_chains(ig.find_renames(raw)),
        rc.chains_from_isin(astore.isin_map(feed_raw)),
    )
    result = rc.reconcile(ig.find_actions(raw), feed, chains=chains)
    panel = adj.adjust(raw, adj.factors_from_reconciliation(result))
    return adj.drop_windows(panel, adj.unadjusted_windows(result))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("spec", type=Path, help="hypothesis YAML")
    ap.add_argument(
        "--holdout",
        action="store_true",
        help="spend one of five project-wide holdout looks (§8.3)",
    )
    ap.add_argument("--capital", type=float, default=200_000)
    args = ap.parse_args()

    spec = yaml.safe_load(args.spec.read_text())
    dataset = "holdout" if args.holdout else "dev"
    log = REPO / "config" / "trials.jsonl"

    gates = reg.Gates(**spec.get("gates", {}))
    # Registered BEFORE the run. If this raises on an exhausted holdout budget,
    # nothing has been computed and nothing has been learned -- correctly.
    trial = reg.register(
        spec["name"],
        spec["hypothesis"],
        {"signal": spec["signal"], "portfolio": spec.get("portfolio", {})},
        dataset=dataset,
        gates=gates,
        alpha_decay_note=spec.get("alpha_decay_note", ""),
        log=log,
    )
    print(f"trial #{trial.trial_id}  {spec['name']}  [{dataset}]")
    print(f"  hypothesis: {spec['hypothesis']}")
    if trial.alpha_decay_note:
        print(f"  alpha decay: {trial.alpha_decay_note}")
    if dataset == "holdout":
        print(f"  holdout looks left after this: {reg.holdout_looks_remaining(log)}")

    print("\nloading and adjusting panel ...", flush=True)
    panel = load_adjusted_panel()
    liq = uv.liquidity(panel)
    members = uv.membership(liq)

    print("computing feature ...", flush=True)
    signal = dict(spec["signal"])
    kind = signal.pop("kind")
    with_feature = features.add(liq, kind, **signal)
    eligible = st.restrict_to_universe(with_feature, members)

    port = spec.get("portfolio", {})
    risk_col = None
    if port.get("inverse_risk"):
        # Equal weight is not equal risk -- see §11a's drawdowns.
        eligible = st.risk_column(eligible, lookback=port.get("risk_lookback", 60))
        risk_col = "risk"
    weights = st.cross_sectional(
        eligible,
        n_side=port.get("n_side", 15),
        long_side=port.get("long_side", "high"),
        gross=port.get("gross", 1.0),
        risk_col=risk_col,
    )
    weights = pf.cap_weights(weights, max_weight=port.get("max_weight", 0.20))
    weights = pf.resample(weights, port.get("cadence", "monthly"))

    window = (
        (pl.col("date") >= HOLDOUT_START)
        if args.holdout
        else (pl.col("date") <= DEV_END)
    )
    weights = weights.filter(window)
    if weights.is_empty():
        print("no positions in the selected window")
        return 1

    print(f"  {weights.height:,} positions, "
          f"{weights['date'].min()} .. {weights['date'].max()}")

    card = costs.load_rate_cards(REPO / "config/costs.yaml")["delivery"]
    slip = costs.load_slippage(REPO / "config/costs.yaml")

    print("running backtest ...", flush=True)
    res = engine.run(
        liq, weights, capital=args.capital, card=card, slippage=slip, tier="mid"
    )
    m = metrics.compute(res.equity)
    gate_values = metrics.gate_inputs(res.equity)

    # Prefer a MEASURED benchmark over the 0.12 assumption. Uses the NIFTY 50
    # index level, never an ETF -- §11d records why NIFTYBEES cannot be trusted.
    bench = indices.benchmark_series(REPO / "data/raw/indices")
    bench_info: dict = {}
    if bench.height > 100:
        bench_info = metrics.vs_benchmark_series(res.equity, bench)
        gate_values["beats_benchmark"] = bench_info["beats_benchmark"]
        gate_values["beta"] = bench_info["beta"]
    checks = reg.check_gates(gate_values, gates)

    years = m.periods / metrics.TRADING_DAYS
    deflated = reg.deflate(
        m.sharpe, n_trials=reg.trial_count(log, dataset=dataset), years=years
    )

    print()
    print(m.summary())
    print(f"gross CAGR    {m.cagr + m.cost_drag:>8.1%}")
    if bench_info:
        print(f"\nbenchmark (NIFTY 50, measured over {bench_info['overlapping_periods']:,} "
              f"overlapping sessions):")
        print(f"  strategy CAGR   {bench_info['cagr']:>7.1%}")
        print(f"  index CAGR      {bench_info['benchmark_cagr']:>7.1%}")
        print(f"  excess          {bench_info['excess']:>+7.1%}")
        print(f"  beta            {bench_info['beta']:>7.2f}   "
              f"(a neutral book should be near 0)")
        print(f"  correlation     {bench_info['correlation']:>7.2f}")
    else:
        print("\n(no index data cached -- benchmark falls back to the 0.12 assumption)")
    print()
    print("gates:")
    for name, passed in checks.items():
        print(f"  {'PASS' if passed else 'FAIL'}  {name}")
    print()
    print(deflated.summary())

    passed_all = all(checks.values()) and deflated.survives
    print(f"\nVERDICT: {'PASSES' if passed_all else 'FAILS'}")
    if dataset == "holdout" and not passed_all:
        print("Holdout failure. This hypothesis is dead -- do not adjust and retry (§8.3).")

    reg.record_result(
        trial.trial_id,
        {
            **{k: round(v, 6) for k, v in gate_values.items() if isinstance(v, float)},
            "gates": checks,
            "deflated_sharpe": round(deflated.deflated_sharpe, 4),
            "noise_threshold": round(deflated.noise_threshold, 4),
            "n_trials_at_run": deflated.n_trials,
            "passed": passed_all,
        },
        log=log,
    )
    print(f"recorded as trial #{trial.trial_id} in config/trials.jsonl")
    return 0 if passed_all else 2


if __name__ == "__main__":
    raise SystemExit(main())
