#!/usr/bin/env python
"""Record fills against a recommendation set, then rebuild and reconcile.

    # paper: fill every pending item at its reference price
    python -m jobs.record_execution --set-id <id> --auto-fill

    # manual: report what you actually got
    python -m jobs.record_execution --set-id <id> --fills '[
      {"seq": 1, "quantity": 300, "price": "2402.75", "charges": "49.05",
       "broker_order_id": "251001000123456"},
      {"seq": 2, "skip": "insufficient funds after T+1 settlement"}
    ]'

Idempotent: a fill carrying a `broker_order_id` already recorded is a no-op, so
re-running the same command cannot double-count cash or shares.

After recording, holdings are REBUILT from the full execution history (never
patched in place) and the result is reconciled. A reconciliation failure exits
non-zero, because a book that disagrees with its trade record makes every
downstream valuation wrong.

Exit codes: 0 recorded and consistent, 1 rejected or inconsistent, 2 config.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from algotrade.config import settings as settings_mod  # noqa: E402
from algotrade.config import validation  # noqa: E402
from algotrade.domain import money  # noqa: E402
from algotrade.domain.models import ItemStatus  # noqa: E402
from algotrade.platform import clock  # noqa: E402
from algotrade.repositories import client  # noqa: E402
from algotrade.repositories.audit import AuditRepository  # noqa: E402
from algotrade.repositories.cash import CashRepository  # noqa: E402
from algotrade.repositories.executions import ExecutionRepository  # noqa: E402
from algotrade.repositories.holdings import HoldingRepository  # noqa: E402
from algotrade.repositories.portfolios import PortfolioRepository  # noqa: E402
from algotrade.repositories.recommendations import RecommendationRepository  # noqa: E402
from algotrade.repositories.snapshots import SnapshotRepository  # noqa: E402
from algotrade.services.execution import (  # noqa: E402
    ExecutionRejected,
    ExecutionService,
    FillRequest,
)
from algotrade.services.holdings import HoldingsService  # noqa: E402
from algotrade.services.reconciliation import ReconciliationService  # noqa: E402

log = logging.getLogger("jobs.record_execution")


def _parse_fills(raw: str) -> list[FillRequest]:
    out = []
    for entry in json.loads(raw):
        out.append(FillRequest(
            seq=int(entry["seq"]),
            quantity=int(entry.get("quantity", 0)),
            price=money.rupees(str(entry["price"])) if entry.get("price") else None,
            charges=money.rupees(str(entry.get("charges", "0"))),
            broker_order_id=str(entry.get("broker_order_id", "")),
            note=str(entry.get("note", "")),
            skip=str(entry.get("skip", "")),
        ))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--set-id", required=True)
    ap.add_argument("--fills", default=None, help="JSON array of fills")
    # Single-fill form, for the workflow_dispatch UI. Symbol+side is resolved
    # to the item's seq, because an execution is recorded against an
    # INSTRUCTION, not a bare symbol -- that is what lets partial fills
    # aggregate and rollups reconcile.
    ap.add_argument("--symbol", default=None)
    ap.add_argument("--side", default=None, choices=["buy", "sell", None])
    ap.add_argument("--quantity", default=None)
    ap.add_argument("--price", default=None)
    ap.add_argument("--charges", default="0")
    ap.add_argument("--broker-order-id", default="")
    ap.add_argument("--execution-date", default="")
    ap.add_argument("--note", default="")
    ap.add_argument("--auto-fill", action="store_true",
                    help="paper mode: fill every pending item at reference price")
    ap.add_argument("--confirm", default=None,
                    help="repeat the set id to confirm (required for live)")
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

    db = client.connect(s.mongodb_uri, database=s.mongodb_database)
    recs = RecommendationRepository(db)
    rec = recs.get_set(args.set_id)
    if rec is None:
        print(f"no recommendation set {args.set_id!r}", file=sys.stderr)
        return 1

    try:
        validation.guard_portfolio(s, rec.portfolio_id)
    except settings_mod.ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2

    # Real money requires an explicit confirmation of the set id.
    from algotrade.domain import ids

    if ids.parse(rec.portfolio_id).is_live and args.confirm != args.set_id:
        print("live portfolio: pass --confirm <set-id> to proceed", file=sys.stderr)
        return 2

    if args.auto_fill:
        # The note says which book this was, because on a shadow the auto-fill
        # is the point -- perfect obedience is what it measures -- while on
        # paper it is a stand-in for a fill nobody got round to entering. A
        # single wording would make those look like the same event later.
        mode = ids.parse(rec.portfolio_id).mode
        fills = [
            FillRequest(seq=i.seq, quantity=i.quantity - i.executed_quantity,
                        price=i.reference_price,
                        charges=i.estimated_charges,
                        note=f"{mode} auto-fill at reference price")
            for i in rec.items
            if i.status in (ItemStatus.PENDING, ItemStatus.PARTIAL)
            and i.quantity > i.executed_quantity
        ]
    elif args.fills:
        fills = _parse_fills(args.fills)
        mode = "manual"
    elif args.symbol:
        if not (args.side and args.quantity and args.price):
            print("--symbol needs --side, --quantity and --price", file=sys.stderr)
            return 2
        matches = [
            i for i in rec.items
            if i.symbol.upper() == args.symbol.upper()
            and i.action.value == args.side
        ]
        if not matches:
            available = ", ".join(
                f"{i.symbol}({i.action.value})" for i in rec.items[:12])
            print(f"no {args.side} instruction for {args.symbol!r} in "
                  f"{rec.set_id}.\n  available: {available}", file=sys.stderr)
            return 1
        if len(matches) > 1:
            # Cannot happen for a well-formed set (one instruction per symbol
            # per side), but guessing would be worse than refusing.
            print(f"{args.symbol} appears {len(matches)} times as a "
                  f"{args.side}; use --fills with an explicit seq",
                  file=sys.stderr)
            return 1
        fills = [FillRequest(
            seq=matches[0].seq,
            quantity=int(args.quantity),
            price=money.rupees(str(args.price)),
            charges=money.rupees(str(args.charges or "0")),
            broker_order_id=args.broker_order_id or "",
            note=args.note or "",
        )]
        mode = "manual"
    else:
        print("pass --fills, --auto-fill, or --symbol/--side/--quantity/--price",
              file=sys.stderr)
        return 2

    print(f"set        {rec.set_id}   status {rec.status}")
    print(f"portfolio  {rec.portfolio_id}")
    print(f"fills      {len(fills)}  (mode={mode})")
    if args.dry_run:
        for f in fills:
            what = f.skip or f"{f.quantity} @ {f.price}"
            print(f"  seq {f.seq:>2}: {what}")
        print("\n--dry-run: nothing written.")
        return 0

    now = clock.SystemClock().now()
    business_date = (
        datetime.strptime(args.execution_date, "%Y-%m-%d").date()
        if args.execution_date else None
    )
    executions = ExecutionRepository(db)
    cash = CashRepository(db)
    audit = AuditRepository(db)
    holdings_repo = HoldingRepository(db)

    service = ExecutionService(
        executions=executions, recommendations=recs, cash=cash, audit=audit)
    try:
        result = service.record(set_id=args.set_id, fills=fills, now=now,
                                business_date=business_date, mode=mode)
    except ExecutionRejected as exc:
        print(f"\nREJECTED: {exc}", file=sys.stderr)
        return 1

    print(f"\nrecorded   {result.applied}")
    print(f"skipped    {len(result.skipped)}")
    print(f"duplicates {len(result.duplicates)} (already recorded; no-op)")
    print(f"cash delta Rs {result.cash_delta:,}")

    # Holdings are DERIVED: rebuilt from the whole history, never patched.
    holdings_service = HoldingsService(
        executions=executions, holdings=holdings_repo, audit=audit)
    rebuild = holdings_service.rebuild(rec.portfolio_id, now=now)
    print(f"\nholdings   {len(rebuild.holdings)} position(s) from "
          f"{rebuild.executions_replayed} execution(s)")
    print(f"realised   Rs {rebuild.realised_pnl:,}")
    if rebuild.anomalies:
        print(f"ANOMALIES  {len(rebuild.anomalies)}", file=sys.stderr)
        for a in rebuild.anomalies:
            print(f"  {a}", file=sys.stderr)

    # The cached cash field is refreshed FROM the ledger, never incremented.
    portfolios = PortfolioRepository(db)
    balance = cash.balance(rec.portfolio_id)
    portfolios.set_cash(rec.portfolio_id, balance)
    print(f"cash       Rs {balance:,}  (from the ledger)")

    consistency = ReconciliationService(
        portfolios=portfolios, holdings_service=holdings_service, cash=cash,
        executions=executions, recommendations=recs,
        snapshots=SnapshotRepository(db),
    ).check(rec.portfolio_id)

    print("\nreconciliation:")
    for finding in consistency.findings:
        print(f"  {finding}")
    if not consistency.ok:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
