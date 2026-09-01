"""Recording executions, by running the trading system's own services.

**This module writes to the book.** It is the only one that does, and it
deliberately contains no book-keeping logic of its own. Every step below is
the same call `jobs/record_execution.py` makes, in the same order, because the
correctness of a recorded fill lives in `ExecutionService`, `HoldingsService`
and `ReconciliationService` -- not in whatever calls them.

The order is not arbitrary and must not be rearranged:

1. **record** -- validates against the instruction, refuses over-fills, writes
   the execution and its signed cash-ledger entry *together*, and treats a
   repeated `broker_order_id` as a no-op rather than an error.
2. **rebuild holdings** -- from the entire execution history, never patched.
   A patched book cannot be checked against anything; a rebuilt one can.
3. **refresh cash** -- the cached `portfolios.cash` is overwritten *from* the
   ledger sum, never incremented. The ledger is the truth.
4. **reconcile** -- and if it fails, say so loudly. A book that disagrees with
   its own trade record makes every downstream number wrong, and the daily job
   will fail on it tomorrow anyway.

Skipping step 2 or 3 leaves the database internally inconsistent in a way the
next scheduled run will detect and alert on -- which is why this is one
function rather than something a route assembles.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from algotrade.domain import money
from algotrade.domain.models import ItemStatus, RecommendationSet
from algotrade.repositories.audit import AuditRepository
from algotrade.repositories.cash import CashRepository
from algotrade.repositories.executions import ExecutionRepository
from algotrade.repositories.holdings import HoldingRepository
from algotrade.repositories.portfolios import PortfolioRepository
from algotrade.repositories.recommendations import RecommendationRepository
from algotrade.repositories.snapshots import SnapshotRepository
from algotrade.services.execution import (
    ExecutionRejected,
    ExecutionService,
    FillRequest,
)
from algotrade.services.holdings import HoldingsService
from algotrade.services.reconciliation import ReconciliationService

log = logging.getLogger("dashboard.recording")


class RecordingError(RuntimeError):
    """The submission was refused before anything was written."""


@dataclass(slots=True)
class RecordOutcome:
    applied: int = 0
    skipped: int = 0
    duplicates: int = 0
    cash_delta: Decimal = money.ZERO
    cash_balance: Decimal = money.ZERO
    positions: int = 0
    realised_pnl: Decimal = money.ZERO
    anomalies: list[str] = field(default_factory=list)
    reconciled: bool = True
    reconciliation_errors: list[str] = field(default_factory=list)
    batch_id: str = ""

    @property
    def ok(self) -> bool:
        return self.reconciled and not self.anomalies


def parse_money(raw: str, *, field_name: str) -> Decimal:
    """Refuse anything that is not a clean decimal.

    `money.rupees` deliberately rejects floats, so the string goes straight to
    Decimal. A price silently coerced from `1,234.5` or `12 34` would be a
    wrong number recorded with total confidence.
    """
    text = (raw or "").strip().replace(",", "")
    if not text:
        raise RecordingError(f"{field_name} is required")
    try:
        value = Decimal(text)
    except (InvalidOperation, ValueError):
        raise RecordingError(f"{field_name}: {raw!r} is not a number") from None
    if value < 0:
        raise RecordingError(f"{field_name} cannot be negative")
    return money.rupees(text)


def parse_quantity(raw: str, *, field_name: str) -> int:
    text = (raw or "").strip()
    if not text:
        return 0
    try:
        value = int(text)
    except ValueError:
        raise RecordingError(f"{field_name}: {raw!r} is not a whole number") from None
    if value < 0:
        raise RecordingError(f"{field_name} cannot be negative")
    return value


def already_applied(db, batch_id: str) -> bool:
    """Has any execution from this submission already been written?

    Paper fills carry no `broker_order_id`, so the unique index that makes a
    single fill idempotent does not protect a *batch*. Without this, a
    double-tapped Submit -- or a mobile connection that retries -- records
    thirty fills twice, and the second copy is indistinguishable from a real
    second trade.
    """
    return db["executions"].count_documents({"batch_id": batch_id}, limit=1) > 0


def build_fills(
    rec: RecommendationSet, form: dict, *, batch_id: str
) -> list[FillRequest]:
    """Turn submitted form fields into fills, ignoring untouched rows.

    A row is submitted only when its quantity is non-zero: the form lists every
    instruction, but a rebalance is routinely partly executed and an untouched
    row must mean "not traded", never "traded zero".
    """
    by_seq = {item.seq: item for item in rec.items}
    fills: list[FillRequest] = []

    for key in form:
        if not key.startswith("qty_"):
            continue
        seq_text = key[4:]
        try:
            seq = int(seq_text)
        except ValueError:
            continue
        item = by_seq.get(seq)
        if item is None:
            raise RecordingError(f"seq {seq} is not part of {rec.set_id}")

        quantity = parse_quantity(form.get(key, ""), field_name=f"{item.symbol} quantity")
        if quantity <= 0:
            continue

        outstanding = item.quantity - item.executed_quantity
        if quantity > outstanding:
            raise RecordingError(
                f"{item.symbol}: {quantity} exceeds the {outstanding} still "
                f"outstanding on this instruction"
            )

        price = parse_money(form.get(f"price_{seq}", ""),
                            field_name=f"{item.symbol} price")
        charges_raw = form.get(f"charges_{seq}", "") or "0"
        charges = parse_money(charges_raw, field_name=f"{item.symbol} charges")

        order_id = (form.get(f"order_{seq}", "") or "").strip()
        note = (form.get(f"note_{seq}", "") or "").strip()

        fills.append(FillRequest(
            seq=seq,
            quantity=quantity,
            price=price,
            charges=charges,
            broker_order_id=order_id,
            note=" ".join(filter(None, [note, f"batch:{batch_id}"])),
        ))

    if not fills:
        raise RecordingError("Nothing to record — every quantity was blank or zero.")
    return fills


def record_batch(
    db,
    *,
    set_id: str,
    fills: list[FillRequest],
    batch_id: str,
    now: datetime,
    business_date: date | None = None,
) -> RecordOutcome:
    """The whole transaction. See this module's docstring for why the order
    of these four steps is load-bearing."""
    recs = RecommendationRepository(db)
    rec = recs.get_set(set_id)
    if rec is None:
        raise RecordingError(f"{set_id} does not exist")

    if already_applied(db, batch_id):
        raise RecordingError(
            "This submission has already been recorded. Reload the "
            "recommendation to see its current state."
        )

    executions = ExecutionRepository(db)
    cash = CashRepository(db)
    audit = AuditRepository(db)
    holdings_repo = HoldingRepository(db)
    portfolios = PortfolioRepository(db)

    # 1. Execution + cash ledger entry, written together.
    service = ExecutionService(
        executions=executions, recommendations=recs, cash=cash, audit=audit)
    try:
        result = service.record(
            set_id=set_id, fills=fills, now=now,
            business_date=business_date, mode="manual",
        )
    except ExecutionRejected as exc:
        raise RecordingError(str(exc)) from exc

    outcome = RecordOutcome(
        applied=result.applied,
        skipped=len(result.skipped),
        duplicates=len(result.duplicates),
        cash_delta=result.cash_delta,
        batch_id=batch_id,
    )

    # Stamp the batch onto exactly the rows just written, by id.
    #
    # NOT `{"batch_id": {"$exists": False}}` -- that would also stamp every
    # execution recorded earlier through record-execution.yml, which sets no
    # batch id, and falsely attribute months of prior trades to this
    # submission. Stamped after the fact rather than before, so that rows which
    # failed to apply do not block a corrected resubmission.
    written = [e.execution_id for e in result.recorded]
    if written:
        executions.collection.update_many(
            {"_id": {"$in": written}}, {"$set": {"batch_id": batch_id}},
        )

    # 2. Holdings are DERIVED: replayed from the whole history, never patched.
    holdings_service = HoldingsService(
        executions=executions, holdings=holdings_repo, audit=audit)
    rebuild = holdings_service.rebuild(rec.portfolio_id, now=now)
    outcome.positions = len(rebuild.holdings)
    outcome.realised_pnl = rebuild.realised_pnl
    outcome.anomalies = [str(a) for a in rebuild.anomalies]

    # 3. The cached balance is refreshed FROM the ledger, never incremented.
    balance = cash.balance(rec.portfolio_id)
    portfolios.set_cash(rec.portfolio_id, balance)
    outcome.cash_balance = balance

    # 4. If the book no longer agrees with its trade record, say so now rather
    #    than letting tomorrow's daily run be the one to find out.
    consistency = ReconciliationService(
        portfolios=portfolios, holdings_service=holdings_service, cash=cash,
        executions=executions, recommendations=recs,
        snapshots=SnapshotRepository(db),
    ).check(rec.portfolio_id)
    outcome.reconciled = consistency.ok
    outcome.reconciliation_errors = [f.message for f in consistency.errors]

    if not outcome.ok:
        log.error("recorded batch %s but the book is inconsistent: %s",
                  batch_id, outcome.reconciliation_errors or outcome.anomalies)
    return outcome


def open_items(rec: RecommendationSet) -> list:
    """Instructions still actionable, sells first.

    T+1 settlement funds the buys, so a list that mixes them is not executable
    top to bottom -- the same ordering the recommendation itself uses.
    """
    items = [i for i in rec.items if i.is_open]
    return sorted(items, key=lambda i: (i.action != "sell", i.seq))


def outstanding_quantity(item) -> int:
    return max(0, item.quantity - item.executed_quantity)
