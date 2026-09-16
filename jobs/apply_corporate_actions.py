#!/usr/bin/env python
"""Find corporate actions affecting the book and record them.

    python -m jobs.apply_corporate_actions --all-active
    python -m jobs.apply_corporate_actions --since 2026-08-01 --dry-run
    python -m jobs.apply_corporate_actions --portfolio-id paper_momentum_v1_001

Runs before valuation in `daily.yml`. Recording an action does not itself
change the book -- it adds an event to the stream that `value_portfolio
--rebuild-holdings` folds in on the very next step. That split is deliberate:
this job's only side effect is an append to an append-only collection, so a
failure here leaves nothing half-applied.

Idempotent. The document key is `portfolio:symbol:ex_date:kind`, so re-running
over a window already processed writes nothing.

Exit codes: 0 nothing refused, 1 something needs a human, 2 config.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from algotrade.config import settings as settings_mod  # noqa: E402
from algotrade.config import validation  # noqa: E402
from algotrade.platform import clock  # noqa: E402
from algotrade.repositories import client  # noqa: E402
from algotrade.repositories.audit import AuditRepository  # noqa: E402
from algotrade.repositories.corporate_actions import (  # noqa: E402
    CorporateActionRepository,
)
from algotrade.repositories.holdings import HoldingRepository  # noqa: E402
from algotrade.repositories.portfolios import PortfolioRepository  # noqa: E402
from algotrade.services.corporate_actions import (  # noqa: E402
    LOOKBACK_DAYS,
    CorporateActionService,
)

log = logging.getLogger("apply_corporate_actions")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--portfolio-id")
    ap.add_argument("--all-active", action="store_true")
    ap.add_argument("--since", help="ISO date; defaults to a rolling window")
    ap.add_argument("--through", help="ISO date; defaults to today")
    ap.add_argument(
        "--lookback", type=int, default=LOOKBACK_DAYS,
        help="days back to scan when --since is not given",
    )
    ap.add_argument(
        "--dry-run", action="store_true",
        help="report what would be recorded, write nothing",
    )
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

    now = clock.SystemClock().now()
    through = date.fromisoformat(args.through) if args.through else now.date()
    since = (
        date.fromisoformat(args.since)
        if args.since
        else through - timedelta(days=args.lookback)
    )

    db = client.connect(s.mongodb_uri, database=s.mongodb_database)
    portfolios = PortfolioRepository(db)

    if args.all_active:
        portfolio_ids = portfolios.active_ids()
        if not portfolio_ids:
            print("no active portfolios", file=sys.stderr)
            return 1
    else:
        portfolio_ids = [args.portfolio_id or s.default_portfolio_id]

    for portfolio_id in portfolio_ids:
        try:
            validation.guard_portfolio(s, portfolio_id)
        except settings_mod.ConfigError as exc:
            print(exc, file=sys.stderr)
            return 2

    service = CorporateActionService(
        holdings=HoldingRepository(db),
        corporate_actions=CorporateActionRepository(db),
    )
    audit = AuditRepository(db)

    needs_attention = False
    for portfolio_id in portfolio_ids:
        result = service.discover(
            portfolio_id, start=since, end=through, now=now,
            apply=not args.dry_run,
        )
        prefix = "would record" if args.dry_run else "recorded"
        print(
            f"{portfolio_id}: {len(result.applied)} {prefix}, "
            f"{len(result.refused)} refused, {result.already_known} already "
            f"known, {result.symbols_checked} symbol(s) checked "
            f"({since} .. {through})"
        )
        for line in result.lines():
            print(f"  {line}")

        if result.refused:
            needs_attention = True

        # Audited even when nothing was found: "we looked and there was
        # nothing" is the record that makes a later surprise diagnosable.
        if not args.dry_run:
            audit.record(
                now=now, actor="system", action="corporate_actions.discovered",
                entity_type="portfolio", entity_id=portfolio_id,
                portfolio_id=portfolio_id,
                after={
                    "applied": [
                        f"{c.symbol}:{c.ex_date}:{c.ratio}"
                        for c in result.applied
                    ],
                    "refused": [
                        f"{c.symbol}:{c.ex_date}: {why}"
                        for c, why in result.refused
                    ],
                    "window": f"{since}..{through}",
                },
                severity="warning" if result.refused else "info",
            )

    # A refusal is not a crash -- the book is untouched and correct as far as
    # it goes -- but it must not pass silently, because an unapplied split is
    # how this whole class of bug hides.
    return 1 if needs_attention else 0


if __name__ == "__main__":
    raise SystemExit(main())
