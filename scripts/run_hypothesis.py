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
from algotrade.backtest import walkforward as wf  # noqa: E402
from algotrade.core import actions as ca  # noqa: E402
from algotrade.core import adjust as adj  # noqa: E402
from algotrade.core import costs, features, integrity as ig  # noqa: E402
from algotrade.core import portfolio as pf  # noqa: E402
from algotrade.core import reconcile as rc, strategies as st, universe as uv  # noqa: E402
from algotrade.io import actions as astore, indices, store  # noqa: E402

DEV_END = date(2021, 12, 31)
HOLDOUT_START = date(2022, 1, 1)
# A benchmark covering less of the window than this is refused: gap-spanning
# returns inflate its CAGR and vol, and understate beta by the same factor.
MIN_BENCH_COVERAGE = 0.90


def _load_exclusions(path: Path) -> set[str]:
    """Symbols listed in config/exclusions.yaml, if the file exists."""
    if not path.exists():
        return set()
    raw = yaml.safe_load(path.read_text()) or {}
    return {str(s) for s in (raw.get("unexplained_moves") or [])}


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
    # Session grid, so padding is in sessions rather than calendar days.
    return adj.drop_windows(
        panel,
        adj.unadjusted_windows(result, sessions=raw["date"].unique().sort()),
    )


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
    # exclusions.yaml is deliberately NOT applied to the universe. Wiring it in
    # looked like closing a gap and instead introduced look-ahead: it drops a
    # symbol's ENTIRE history on the strength of one unexplained move, so a 2019
    # event removes the name from the 2011 universe, and 50 of the 127 symbols
    # are excluded on events dated >= 2022 -- holdout information conditioning
    # the dev universe. The file's own header says "for review only", and
    # `adjust.unadjusted_windows` already masks these events point-in-time,
    # which is the correct treatment.
    members = uv.membership(liq, exclude=set(uv.DEFAULT_EXCLUSIONS))

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
    # rf=0 throughout: a dollar-neutral long/short book's return is already a
    # self-financing spread. Using the default 6.5% here while the gates used 0.0
    # made the printed Sharpe (0.13), the gated Sharpe (0.60) and the deflated
    # Sharpe three different numbers for the same run.
    m = metrics.compute(res.equity, risk_free=0.0)
    gate_values = metrics.gate_inputs(res.equity, risk_free=0.0)

    # Prefer a MEASURED benchmark over the 0.12 assumption. Uses the NIFTY 50
    # index level, never an ETF -- §11d records why NIFTYBEES cannot be trusted.
    # Gate on OVERLAP, not series length. Gating on length let a benchmark with
    # 43% session coverage through, whose gap-spanning returns produced a
    # nonsense 23.6% "CAGR" -- worse than the NIFTYBEES bug §11d was written to
    # prevent, and in the direction that makes the gate unpassable.
    bench = indices.benchmark_series(REPO / "data/raw/indices")
    bench_info: dict = {}
    if not bench.is_empty():
        candidate = metrics.vs_benchmark_series(res.equity, bench)
        coverage = candidate["overlapping_periods"] / max(m.periods, 1)
        if candidate["usable"] and coverage >= MIN_BENCH_COVERAGE:
            bench_info = candidate
            gate_values["beats_benchmark"] = bench_info["beats_benchmark"]
            gate_values["beta"] = bench_info["beta"]
        else:
            print(f"\n  index coverage {coverage:.0%} of strategy sessions "
                  f"(need {MIN_BENCH_COVERAGE:.0%}) -- falling back to the 0.12 "
                  f"assumption rather than trusting a gappy series")
    # Stability across time -- §8.1's regime gate. Purged by the feature's own
    # lookback so a fold's features cannot read across its left boundary.
    lookback = int(spec["signal"].get("lookback", 0)) + int(
        spec["signal"].get("skip", 0)
    )
    stability: wf.Report | None = None
    try:
        stability = wf.evaluate(
            res.equity, n_folds=5, purge=lookback, risk_free=0.0
        )
        gate_values["positive_fold_fraction"] = (
            stability.positive_folds / len(stability.folds)
            if stability.folds
            else 0.0
        )
    except ValueError as exc:
        print(f"\n  fold evaluation skipped: {exc}")
    checks = reg.check_gates(gate_values, gates)

    years = m.periods / metrics.TRADING_DAYS
    # ALL registrations, not the dataset-scoped count. A holdout run scoped to
    # `dataset="holdout"` gives n_trials=1, whose noise threshold is 0.0 by
    # definition -- zeroing the denominator at exactly the irreplaceable look.
    # The candidate reached the holdout only because N dev trials selected it,
    # and that selection burden is what the deflation exists to charge.
    deflated = reg.deflate(m.sharpe, n_trials=reg.trial_count(log), years=years)

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
        # Says "not used" rather than "not cached": the series may be present and
        # simply too short to cover this strategy's window.
        print("\n(measured benchmark not used -- see above; gate uses the 0.12 "
              "assumption)")
    if stability is not None and stability.folds:
        print()
        print(stability.summary())
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
