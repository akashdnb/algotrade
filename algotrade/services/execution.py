"""Recording fills. The point where intent becomes fact.

**Recommendations are intent; executions are truth.** A recommendation says
"buy 500 RELIANCE". What actually happened might be 300 at ₹2,402 on Thursday
and 200 at ₹2,409 on Friday, or 300 and then nothing. The item keeps a rollup
for display, but every number that matters is derived from the executions --
and `reconcile_rollups` mechanically checks the two agree rather than trusting
that they do.

Each fill writes **two** documents in one logical step: the execution, and the
cash-ledger entry it implies. Both are keyed deterministically off the fill, so
replaying it is a no-op rather than a double-count.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from algotrade.domain import money
from algotrade.domain.models import (
    Action,
    Execution,
    ItemStatus,
    RecommendationSet,
    reconcile_item_rollup,
)
from algotrade.platform import clock
from algotrade.repositories.base import AlreadyExists

log = logging.getLogger(__name__)


class ExecutionRejected(ValueError):
    """The fill cannot be recorded as described."""


class DuplicateExecution(RuntimeError):
    """This fill is already recorded.

    Not a failure -- it is the idempotency guard firing, and the realistic
    cause is a human re-submitting the same command.
    """


@dataclass(frozen=True, slots=True)
class FillRequest:
    """One reported fill against one recommendation item."""

    seq: int
    quantity: int = 0
    price: Decimal | None = None
    charges: Decimal = money.ZERO
    broker_order_id: str = ""
    executed_at: datetime | None = None
    note: str = ""
    skip: str = ""

    @property
    def is_skip(self) -> bool:
        return bool(self.skip)


@dataclass(slots=True)
class RecordResult:
    recorded: list[Execution] = field(default_factory=list)
    skipped: list[int] = field(default_factory=list)
    duplicates: list[int] = field(default_factory=list)
    cash_delta: Decimal = money.ZERO
    warnings: list[str] = field(default_factory=list)

    @property
    def applied(self) -> int:
        return len(self.recorded)


def _fill_date(fill: "FillRequest", now: datetime) -> date:
    """The day a fill actually happened.

    Not `rec.as_of_session`, which is what this used to fall back to. The fill
    convention is `next_open`, so you act the MORNING AFTER the signal -- every
    fill recorded through the documented workflow was therefore stamped a day
    early, and at the 1 April boundary in the previous FINANCIAL YEAR.
    `ExecutionRepository.between` is the tax-year query (S2.3), and holding
    periods are counted from this date too.

    Not `clock.session_date` either: that answers "which session is this run
    FOR", and before the 18:00 publish hour it returns YESTERDAY. Correct for
    the daily job running after the close; wrong for a fill entered at 09:30
    while the market is open.

    So: the IST calendar date, stepped back to the previous weekday if it lands
    on one. NSE did not trade on a Saturday, and a trade record should not
    carry a date on which no trade could have occurred. Anything else --
    entering a fill days later, in particular -- is what the explicit
    `business_date` argument is for.
    """
    if fill.executed_at:
        return fill.executed_at.date()
    day = now.astimezone(clock.IST).date()
    return day if not clock.is_weekend(day) else clock.previous_weekday(day)


class ExecutionService:
    def __init__(
        self, *, executions, recommendations, cash, audit=None, holdings=None
    ) -> None:
        self._executions = executions
        self._recs = recommendations
        self._cash = cash
        self._audit = audit
        self._holdings = holdings

    # ------------------------------------------------------------ record ----

    def record(
        self,
        *,
        set_id: str,
        fills: list[FillRequest],
        now: datetime,
        business_date: date | None = None,
        mode: str = "manual",
        actor: str = "user",
    ) -> RecordResult:
        """Apply fills to a recommendation set.

        Raises:
            KeyError: unknown set.
            ExecutionRejected: a fill that cannot be honest -- over-filling an
                item, a skip with no reason, or a price on a skip.
        """
        rec = self._recs.get_set(set_id)
        if rec is None:
            raise KeyError(f"no recommendation set {set_id!r}")

        by_seq = {item.seq: item for item in rec.items}
        result = RecordResult()

        for fill in fills:
            item = by_seq.get(fill.seq)
            if item is None:
                raise ExecutionRejected(
                    f"{set_id} has no item with seq {fill.seq}"
                )

            if fill.is_skip:
                self._skip(rec, item, fill, now=now, actor=actor)
                result.skipped.append(fill.seq)
                continue

            # Duplicate BEFORE over-fill: re-submitting the same command is
            # the realistic manual mistake, and it should read as "already
            # recorded", not "this would exceed the instruction".
            if fill.broker_order_id and self._executions.has_order_id(
                rec.portfolio_id, fill.broker_order_id
            ):
                log.info("broker order %s already recorded", fill.broker_order_id)
                result.duplicates.append(fill.seq)
                continue

            self._validate(rec, item, fill)
            outcome = self._apply(
                rec, item, fill, now=now,
                business_date=business_date or _fill_date(fill, now),
                mode=mode, actor=actor,
            )
            if outcome is None:
                result.duplicates.append(fill.seq)
                continue
            execution, cash_effect = outcome
            result.recorded.append(execution)
            result.cash_delta = money.rupees(result.cash_delta + cash_effect)

        self._refresh_rollups(rec)
        self._recs.update_items(rec)
        result.warnings.extend(self.reconcile_rollups(set_id))
        return result

    # ---------------------------------------------------------- internals ---

    def _validate(self, rec, item, fill: FillRequest) -> None:
        if fill.quantity <= 0:
            raise ExecutionRejected(
                f"item {fill.seq}: quantity must be > 0 (use skip= to decline)"
            )
        if fill.price is None or fill.price <= 0:
            raise ExecutionRejected(f"item {fill.seq}: a positive price is required")

        already = sum(
            e.quantity for e in self._executions.for_item(rec.set_id, item.seq)
        )
        if already + fill.quantity > item.quantity:
            # Over-filling means the instruction and reality have diverged in a
            # way the system cannot silently absorb: the book would no longer
            # match any recommendation.
            raise ExecutionRejected(
                f"item {fill.seq} ({item.symbol}): instruction is "
                f"{item.quantity}, {already} already filled, this fill of "
                f"{fill.quantity} would exceed it by "
                f"{already + fill.quantity - item.quantity}"
            )

    def _apply(self, rec, item, fill, *, now, business_date, mode, actor):
        ordinal = self._executions.next_ordinal(rec.set_id, item.seq)
        execution = Execution(
            execution_id=self._executions.key(rec.set_id, item.seq, ordinal),
            portfolio_id=rec.portfolio_id,
            set_id=rec.set_id,
            item_seq=item.seq,
            symbol=item.symbol,
            isin=item.isin,
            action=item.action,
            quantity=fill.quantity,
            price=fill.price,
            charges=fill.charges,
            reference_price=item.reference_price,
            executed_at=fill.executed_at or now,
            broker_order_id=fill.broker_order_id,
        )

        try:
            self._executions.record(execution, business_date=business_date)
        except AlreadyExists:
            # Same fill submitted twice -- the realistic manual-entry mistake.
            log.info("execution %s already recorded", execution.execution_id)
            return None

        # The cash the fill actually moved. A buy costs value plus charges; a
        # sell returns value minus charges. Written with an id derived from the
        # execution, so it cannot be double-counted either.
        signed = (
            money.rupees(-execution.net_value)
            if execution.action == Action.BUY
            else execution.net_value
        )
        self._cash.record(
            portfolio_id=rec.portfolio_id,
            kind=execution.action.value,
            amount=signed,
            on=business_date,
            now=now,
            discriminator=execution.execution_id,
            symbol=execution.symbol,
            execution_id=execution.execution_id,
            set_id=rec.set_id,
            note=fill.note,
        )

        if self._audit:
            self._audit.record(
                now=now, actor=actor, action="execution.recorded",
                entity_type="execution", entity_id=execution.execution_id,
                portfolio_id=rec.portfolio_id,
                after={
                    "symbol": execution.symbol,
                    "action": execution.action.value,
                    "quantity": execution.quantity,
                    "price": str(execution.price),
                    "charges": str(execution.charges),
                    "slippage_bps": execution.slippage_bps,
                    "mode": mode,
                    "note": fill.note,
                },
                context={"set_id": rec.set_id, "item_seq": item.seq},
            )
        return execution, signed

    def _skip(self, rec, item, fill: FillRequest, *, now, actor) -> None:
        item.status = ItemStatus.SKIPPED
        item.skip_reason = fill.skip
        if self._audit:
            self._audit.record(
                now=now, actor=actor, action="recommendation.item.skipped",
                entity_type="recommendation", entity_id=rec.set_id,
                portfolio_id=rec.portfolio_id,
                before={"status": ItemStatus.PENDING.value},
                after={"status": ItemStatus.SKIPPED.value,
                       "reason": fill.skip, "symbol": item.symbol},
                severity="info",
            )

    def _refresh_rollups(self, rec: RecommendationSet) -> None:
        """Recompute each item's display rollup FROM its executions.

        Never incremented in place: an increment can drift, a recomputation
        cannot.
        """
        for item in rec.items:
            fills = self._executions.for_item(rec.set_id, item.seq)
            if not fills:
                continue
            filled = sum(e.quantity for e in fills)
            value = money.sum_rupees(e.value for e in fills)
            item.executed_quantity = filled
            item.executed_price = (
                money.rupees(value / filled) if filled else None
            )
            item.executed_charges = money.sum_rupees(e.charges for e in fills)
            item.executed_at = max(e.executed_at for e in fills)
            item.broker_order_id = next(
                (e.broker_order_id for e in reversed(fills) if e.broker_order_id), ""
            )
            item.status = (
                ItemStatus.EXECUTED if filled >= item.quantity
                else ItemStatus.PARTIAL
            )

    # ----------------------------------------------------- reconciliation ---

    def reconcile_rollups(self, set_id: str) -> list[str]:
        """Check every item's rollup against its executions.

        The rollup exists for display and cheap queries, which means it can
        drift. Four audits and ~55 defects in this project argue for a
        mechanical check over trusting the two to stay in step.
        """
        rec = self._recs.get_set(set_id)
        if rec is None:
            return [f"no recommendation set {set_id!r}"]
        executions = self._executions.for_set(set_id)
        problems: list[str] = []
        for item in rec.items:
            problems.extend(reconcile_item_rollup(item, executions))
        return problems
