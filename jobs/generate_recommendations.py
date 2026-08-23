#!/usr/bin/env python
"""Generate monthly rebalance recommendations. Recommendation only -- no orders.

Reconciliation-driven: `--check-only` reads STATE to decide whether a rebalance
is due, so this can run every weekday and exit in seconds. A dropped monthly
schedule then repairs itself within 24 hours.

    python -m jobs.generate_recommendations --check-only
    python -m jobs.generate_recommendations
    python -m jobs.generate_recommendations --period 2026-08 --force
    python -m jobs.generate_recommendations --verify-frozen
    python -m jobs.generate_recommendations --dry-run

Exit codes: 0 done or not due, 1 failed, 2 configuration error.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from algotrade.config import settings as settings_mod  # noqa: E402
from algotrade.config import validation  # noqa: E402
from algotrade.core.costs import load_rate_cards  # noqa: E402
from algotrade.domain import money  # noqa: E402
from algotrade.platform import clock  # noqa: E402
from algotrade.repositories import client  # noqa: E402
from algotrade.repositories.audit import AuditRepository  # noqa: E402
from algotrade.repositories.holdings import HoldingRepository  # noqa: E402
from algotrade.repositories.portfolios import PortfolioRepository  # noqa: E402
from algotrade.repositories.rebalance_runs import RebalanceRunRepository  # noqa: E402
from algotrade.repositories.recommendations import RecommendationRepository  # noqa: E402
from algotrade.repositories.signals import SignalRepository  # noqa: E402
from algotrade.services.rebalance import RebalanceService  # noqa: E402
from algotrade.services.recommendation import (  # noqa: E402
    FrozenStrategyMismatch,
    RebalanceNotDue,
    RecommendationService,
)
from algotrade.services.signals import StrategySpec  # noqa: E402

log = logging.getLogger("jobs.generate_recommendations")


def _emit(**outputs) -> None:
    if not (path := os.environ.get("GITHUB_OUTPUT")):
        return
    with open(path, "a", encoding="utf-8") as fh:
        for key, value in outputs.items():
            fh.write(f"{key}={value}\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--portfolio-id", default=None)
    ap.add_argument("--period", default=None, help="YYYY-MM (default: current)")
    ap.add_argument("--check-only", action="store_true",
                    help="report whether a rebalance is due; change nothing")
    ap.add_argument("--verify-frozen", action="store_true",
                    help="check the strategy config still matches the portfolio")
    ap.add_argument("--force", action="store_true",
                    help="regenerate as a new revision even if completed")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    s = settings_mod.load()
    logging.basicConfig(level=getattr(logging, s.log_level, logging.INFO),
                        format="%(levelname)-7s %(message)s")

    report = validation.validate(s)
    blocking = [f for f in report.errors
                if not f.key.startswith(("TELEGRAM", "HEALTHCHECKS"))]
    if blocking:
        for finding in blocking:
            print(finding, file=sys.stderr)
        return 2

    portfolio_id = args.portfolio_id or s.default_portfolio_id
    try:
        validation.guard_portfolio(s, portfolio_id)
    except settings_mod.ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2

    spec = StrategySpec.from_yaml(s.strategy_config)
    db = client.connect(s.mongodb_uri, database=s.mongodb_database)

    portfolios = PortfolioRepository(db)
    portfolio = portfolios.get(portfolio_id)
    if portfolio is None:
        print(f"no portfolio {portfolio_id!r} -- run: python -m jobs.create_portfolio",
              file=sys.stderr)
        return 1

    # --- the frozen-strategy gate -------------------------------------------
    stored_hash = portfolio.get("strategy_params_hash")
    if stored_hash != spec.params_hash():
        print(f"FROZEN STRATEGY MISMATCH\n"
              f"  portfolio was created with {stored_hash}\n"
              f"  config now hashes to      {spec.params_hash()}\n"
              f"  A parameter change forks a NEW portfolio (bump to v2); it must "
              f"never silently alter this one.", file=sys.stderr)
        return 1
    if args.verify_frozen:
        print(f"frozen strategy OK: {spec.version} {spec.params_hash()}")
        return 0

    signals_repo = SignalRepository(db)
    latest = signals_repo.latest(spec.version)
    if latest is None:
        print(f"no signals stored for {spec.version} -- run: "
              f"python -m jobs.generate_signals", file=sys.stderr)
        _emit(due="false", reason="no-signals")
        return 1

    as_of = latest.date
    period = args.period or clock.period_of(as_of)
    sessions = signals_repo.dates(spec.version)

    service = RecommendationService(
        recommendations=RecommendationRepository(db),
        rebalance_runs=RebalanceRunRepository(db),
        rebalance=RebalanceService(
            entry=spec.entry, exit_=spec.exit,
            charge_reserve_bps=s.charge_reserve_bps,
            card=load_rate_cards(s.cost_config)["delivery"],
        ),
        audit=AuditRepository(db),
        ttl_days=s.recommendation_ttl_days,
    )

    due, reason = service.is_due(
        portfolio_id=portfolio_id, period=period, sessions=sessions, as_of=as_of,
    )
    if args.force:
        due, reason = True, "forced"

    print(f"portfolio  {portfolio_id}")
    print(f"period     {period}")
    print(f"signal     {as_of}  ({len(latest.rows)} ranks)")
    print(f"due        {due}  -- {reason}")

    if args.check_only:
        _emit(due=str(due).lower(),
              portfolios=json.dumps([portfolio_id]) if due else "[]",
              period=period, reason=reason)
        return 0
    if not due:
        _emit(due="false", reason=reason)
        return 0

    holdings = HoldingRepository(db).book(portfolio_id)
    prices = {r.symbol: r.close for r in latest.rows if r.close is not None}
    # Held names must be priced too, or their value cannot count toward what is
    # investable. A held name absent from today's ranking is priced at zero,
    # which correctly makes it worth nothing to redeploy and forces a sell.
    for h in holdings:
        prices.setdefault(h.symbol, money.ZERO)

    if args.dry_run:
        decision = service._rebalance.decide(
            holdings=holdings, signals=latest, cash=portfolio["cash"],
            prices=prices, as_of=as_of,
        )
        _print_decision(decision, holdings)
        print("\n--dry-run: nothing written.")
        return 0

    try:
        result = service.generate(
            portfolio_id=portfolio_id, period=period,
            holdings=holdings, signals=latest,
            cash=portfolio["cash"], prices=prices, as_of=as_of,
            now=clock.SystemClock().now(),
            panel_manifest_hash=latest.panel_manifest_hash,
            strategy_params_hash=spec.params_hash(),
            portfolio_params_hash=stored_hash,
            code_commit=s.code_commit,
            cost_model_hash=str(s.cost_config.name),
            force=args.force,
        )
    except (FrozenStrategyMismatch, RebalanceNotDue) as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 0 if isinstance(exc, RebalanceNotDue) else 1

    rec, decision = result.recommendation, result.decision
    _print_decision(decision, holdings)
    print(f"\nstored {rec.set_id}")
    print(f"  {len(rec.items)} trade(s), {len(rec.holds)} hold(s), "
          f"expires {rec.expires_on}")
    _emit(due="true", set_id=rec.set_id, trades=len(rec.items),
          period=period, entering=len(decision.entering),
          leaving=len(decision.leaving))
    return 0


def _print_decision(decision, holdings) -> None:
    print(f"\ncurrent    {len(holdings)} position(s)")
    print(f"target     {len(decision.target_symbols)} name(s)")
    print(f"entering   {len(decision.entering)}  {', '.join(decision.entering[:8])}")
    print(f"leaving    {len(decision.leaving)}  {', '.join(decision.leaving[:8])}")
    print(f"holding    {len(decision.holding)}")
    print(f"investable Rs {decision.investable:,}  "
          f"(reserve Rs {decision.charge_reserve:,})")
    print(f"deployed   Rs {decision.deployed:,}   "
          f"residual Rs {decision.residual_cash:,}")
    print(f"cash req.  Rs {decision.cash_required:,}")
    if decision.dropped:
        print(f"dropped    {len(decision.dropped)}")
        for symbol, why in list(decision.dropped.items())[:5]:
            print(f"             {symbol}: {why}")


if __name__ == "__main__":
    raise SystemExit(main())
