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
from fractions import Fraction
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from algotrade.config import settings as settings_mod  # noqa: E402
from algotrade.domain.corporate_actions import (  # noqa: E402
    Spinoff,
    apply_spinoff,
)
from algotrade.repositories.base import AlreadyExists  # noqa: E402
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
    ap.add_argument(
        "--acknowledge", metavar="SYMBOL:YYYY-MM-DD:KIND",
        help="mark one refused action as dealt with (e.g. RELIANCE:2026-07-20:demerger)",
    )
    ap.add_argument("--note", default="", help="what you did, for the record")

    spin = ap.add_argument_group(
        "recording a demerger",
        "NSE's feed says 'Demerger' and nothing else, so both numbers come "
        "from the company's scheme and you have to go and read them. The "
        "arithmetic on top of them is then reproducible, which is why this is "
        "an event rather than an edit.",
    )
    spin.add_argument("--spinoff", metavar="PARENT:CHILD:YYYY-MM-DD",
                      help="e.g. ITC:ITCHOTELS:2025-01-06")
    spin.add_argument("--entitlement", metavar="N:M",
                      help="child shares per parent share, e.g. 1:10")
    spin.add_argument("--basis-fraction", metavar="N:M",
                      help="share of COST moving to the child (s.49(2C)/(2D)), "
                           "e.g. 18:100")
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

    if args.spinoff:
        if not (args.entitlement and args.basis_fraction):
            print("--spinoff needs --entitlement and --basis-fraction; neither "
                  "is in the feed, both are in the scheme", file=sys.stderr)
            return 2
        try:
            parent_symbol, child_symbol, raw = args.spinoff.split(":")
            ex_date = date.fromisoformat(raw)
            entitlement = Fraction(*(int(x) for x in args.entitlement.split(":")))
            basis = Fraction(*(int(x) for x in args.basis_fraction.split(":")))
        except (ValueError, TypeError, ZeroDivisionError) as exc:
            print(f"could not read the spin-off arguments: {exc}", file=sys.stderr)
            return 2

        holdings_repo = HoldingRepository(db)
        actions_repo = CorporateActionRepository(db)
        recorded = 0
        for portfolio_id in portfolio_ids:
            parent = holdings_repo.get_holding(portfolio_id, parent_symbol)
            if parent is None or parent.is_empty:
                print(f"{portfolio_id}: holds no {parent_symbol}, skipped")
                continue

            spinoff = Spinoff(
                action_id=actions_repo.key(
                    portfolio_id, parent_symbol, ex_date, "spinoff"),
                symbol=parent_symbol, child_symbol=child_symbol,
                ex_date=ex_date, entitlement=entitlement, basis_fraction=basis,
                subject=args.note,
            )
            # Shown BEFORE it is written, and written only on --yes-style
            # intent: the two numbers came from a human reading a PDF, and a
            # transposed digit is not something the system can catch.
            _, child, preview = apply_spinoff(parent, spinoff)
            print(f"{portfolio_id}: {parent_symbol} {parent.quantity} shares "
                  f"-> {child_symbol} {preview.child_quantity} shares")
            print(f"  basis {preview.cost_before} -> parent "
                  f"{preview.cost_before - preview.basis_moved} + child "
                  f"{preview.basis_moved}")
            if preview.fractional_shares_dropped:
                print(f"  entitlement dropped: "
                      f"{preview.fractional_shares_dropped} share "
                      f"-- cash in lieu is NOT modelled")
            if child is None:
                print("  the entitlement rounds to nothing; the parent keeps "
                      "its whole basis")

            if args.dry_run:
                continue
            try:
                actions_repo.record_spinoff(
                    portfolio_id=portfolio_id, spinoff=spinoff, now=now,
                    parent_quantity=preview.parent_quantity,
                    child_quantity=preview.child_quantity, note=args.note,
                )
            except AlreadyExists:
                print("  already recorded")
                continue
            audit.record(
                now=now, actor="human", action="corporate_actions.spinoff",
                entity_type="portfolio", entity_id=portfolio_id,
                portfolio_id=portfolio_id,
                after={"parent": parent_symbol, "child": child_symbol,
                       "ex_date": str(ex_date),
                       "entitlement": str(entitlement),
                       "basis_fraction": str(basis),
                       "child_quantity": preview.child_quantity,
                       "note": args.note},
                severity="warning",
            )
            recorded += 1

        if not args.dry_run and recorded:
            print(f"\nrecorded on {recorded} portfolio(s). Now run:\n"
                  f"  python -m jobs.value_portfolio --all-active "
                  f"--rebuild-holdings")
        return 0

    if args.acknowledge:
        try:
            symbol, raw, kind = args.acknowledge.split(":")
            ex_date = date.fromisoformat(raw)
        except ValueError:
            print("--acknowledge wants SYMBOL:YYYY-MM-DD:KIND", file=sys.stderr)
            return 2
        done = 0
        for portfolio_id in portfolio_ids:
            if service.acknowledge(portfolio_id, symbol=symbol, ex_date=ex_date,
                                   kind=kind, now=now, note=args.note):
                print(f"{portfolio_id}: acknowledged {symbol} {ex_date} ({kind})")
                audit.record(
                    now=now, actor="human",
                    action="corporate_actions.acknowledged",
                    entity_type="portfolio", entity_id=portfolio_id,
                    portfolio_id=portfolio_id,
                    after={"symbol": symbol, "ex_date": str(ex_date),
                           "kind": kind, "note": args.note},
                    severity="info",
                )
                done += 1
        if not done:
            print(f"no unacknowledged refusal matching {args.acknowledge}",
                  file=sys.stderr)
            return 1
        return 0

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
