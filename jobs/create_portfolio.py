#!/usr/bin/env python
"""Create a paper portfolio. Idempotent.

    python -m jobs.create_portfolio
    python -m jobs.create_portfolio --portfolio-id paper_momentum_v1_001 \
        --label "Paper Momentum 30" --capital 200000
    python -m jobs.create_portfolio --dry-run

Re-running is safe: the portfolio id is the primary key, so an existing
portfolio is returned unchanged. It is an ERROR, not a no-op, if the existing
portfolio disagrees about capital or strategy parameters -- silently accepting
that would let a track record be reset by a typo.

`is_live` is deliberately NOT a field: it is derived from the id's mode
(`paper_`/`live_`/`shadow_`). A stored flag could contradict the id, and then
neither would be trustworthy.

Exit codes: 0 created or already correct, 1 conflict, 2 configuration error.
"""

from __future__ import annotations

import argparse
import logging
import sys
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from algotrade.config import settings as settings_mod  # noqa: E402
from algotrade.config import validation  # noqa: E402
from algotrade.domain import ids, money  # noqa: E402
from algotrade.domain.models import BenchmarkSleeve, ExecutionMode  # noqa: E402
from algotrade.platform import clock  # noqa: E402
from algotrade.repositories import client  # noqa: E402
from algotrade.repositories.audit import AuditRepository  # noqa: E402
from algotrade.repositories.portfolios import PortfolioRepository  # noqa: E402
from algotrade.services.portfolio import PortfolioConflict, PortfolioService  # noqa: E402
from algotrade.services.signals import StrategySpec  # noqa: E402

log = logging.getLogger("jobs.create_portfolio")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--portfolio-id", default=None,
                    help="default: DEFAULT_PORTFOLIO_ID from configuration")
    ap.add_argument("--label", default="Paper Momentum 30")
    ap.add_argument("--capital", type=Decimal, default=Decimal("200000"))
    ap.add_argument("--monthly-contribution", type=Decimal, default=Decimal("0"))
    ap.add_argument("--benchmark-weight", type=float, default=0.0,
                    help="synthetic NIFTYBEES sleeve weight; 0 disables it")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    s = settings_mod.load()
    logging.basicConfig(level=getattr(logging, s.log_level, logging.INFO),
                        format="%(levelname)-7s %(message)s")

    report = validation.validate(s)
    blocking = [f for f in report.errors
                if f.key.startswith(("MONGODB", "ENVIRONMENT", "strategy_config"))]
    if blocking:
        for finding in blocking:
            print(finding, file=sys.stderr)
        return 2

    portfolio_id = args.portfolio_id or s.default_portfolio_id
    if not portfolio_id:
        print("no --portfolio-id and DEFAULT_PORTFOLIO_ID is not set",
              file=sys.stderr)
        return 2

    # Real money may only be operated from production, checked here as well as
    # at startup because the id can arrive on the command line.
    try:
        validation.guard_portfolio(s, portfolio_id)
    except settings_mod.ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2

    spec = StrategySpec.from_yaml(s.strategy_config)
    params_hash = spec.params_hash()

    print(f"portfolio    {portfolio_id}")
    print(f"label        {args.label}")
    print(f"strategy     {spec.version}  ({params_hash})")
    print(f"capital      Rs {args.capital:,}")
    print(f"mode         {ids.parse(portfolio_id).mode}  "
          f"(is_live={ids.parse(portfolio_id).is_live})")
    print(f"database     {s.mongodb_database}")

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return 0

    db = client.connect(s.mongodb_uri, database=s.mongodb_database)
    service = PortfolioService(
        repository=PortfolioRepository(db), audit=AuditRepository(db)
    )

    sleeve = None
    if args.benchmark_weight > 0:
        sleeve = BenchmarkSleeve(
            enabled=True, index="NIFTY_50",
            target_weight=args.benchmark_weight,
        )

    try:
        result = service.create(
            portfolio_id=portfolio_id,
            label=args.label,
            strategy_params_hash=params_hash,
            initial_capital=money.rupees(args.capital),
            monthly_contribution=money.rupees(args.monthly_contribution),
            now=clock.SystemClock().now(),
            execution_mode=ExecutionMode.MANUAL,
            benchmark_sleeve=sleeve,
            cost_model=str(s.cost_config.name),
            code_commit=s.code_commit,
            account_note=(
                "Momentum sleeve only. The NIFTYBEES half of the account is "
                "held manually and is never traded or rebalanced here."
            ),
        )
    except PortfolioConflict as exc:
        print(f"\nCONFLICT: {exc}", file=sys.stderr)
        return 1
    except ids.InvalidPortfolioId as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 2

    p = result.portfolio
    print(f"\n{'created' if result.created else 'already exists (unchanged)'}")
    print(f"  cash    Rs {p.cash:,}")
    print(f"  units   {p.units}")
    print(f"  status  {p.status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
