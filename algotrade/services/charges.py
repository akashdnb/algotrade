"""Correcting an estimated charge once the real one is known.

A fill is recorded the moment it happens, with charges from the published rate
card. That is the best number available at the time and it is not the true one:
Kite's trades API carries price and quantity, the contract note arrives later,
and **STT and stamp duty post at end of day**. An intraday funds difference
therefore under-reports.

**A correction is a new cash-ledger entry, never an edit to the execution.**
Executions are the authoritative trade and tax record and are never patched in
place -- the same discipline that backfilled a late audit row rather than
rewriting history. Editing one would also make the difference unrecoverable:
you could no longer say what was believed when the trade was made.

So the correction is an `adjustment`, signed, carrying its own explanation. The
execution keeps the estimate, the ledger carries the truth, and the gap between
them stays visible instead of being quietly absorbed.

Pure: no repository, no clock, no network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Mapping, Sequence

from algotrade.domain import money

#: Below this the correction costs more attention than it is worth. A paisa of
#: rounding between the rate card and the contract note is not a finding.
MATERIAL: Decimal = Decimal("0.50")


@dataclass(frozen=True, slots=True)
class Correction:
    """One execution's estimate against what was actually charged."""

    execution_id: str
    symbol: str
    estimated: Decimal
    actual: Decimal

    @property
    def delta(self) -> Decimal:
        """Signed, from the book's point of view.

        Negative means the estimate was too low and the book owes more, which
        is the direction end-of-day STT and stamp duty push it.
        """
        return money.rupees(self.estimated - self.actual)

    @property
    def material(self) -> bool:
        return abs(self.delta) >= MATERIAL

    def __str__(self) -> str:
        direction = "under" if self.delta < 0 else "over"
        return (f"{self.symbol} ({self.execution_id}): estimated "
                f"{self.estimated}, actual {self.actual} -- {direction}stated "
                f"by {abs(self.delta)}")


@dataclass
class Reconciliation:
    """What the contract note changed, and what it left alone."""

    corrections: list[Correction] = field(default_factory=list)
    #: Executions the note did not mention. Reported, never assumed correct:
    #: an execution absent from the note is one nobody verified.
    unmatched: list[str] = field(default_factory=list)

    @property
    def material(self) -> list[Correction]:
        return [c for c in self.corrections if c.material]

    @property
    def net(self) -> Decimal:
        """Total adjustment. Negative means the book owes more."""
        return money.sum_rupees(c.delta for c in self.material)

    @property
    def ok(self) -> bool:
        return not self.material and not self.unmatched


def reconcile(
    executions: Sequence[Mapping],
    actual_charges: Mapping[str, Decimal],
) -> Reconciliation:
    """Compare recorded charges against the contract note.

    `actual_charges` maps **broker order id** to the charge actually levied.
    Keyed on the broker's own identifier rather than on symbol or sequence:
    the same name can be traded twice in a session, and the two-day protocol
    makes that ordinary rather than exotic.
    """
    out = Reconciliation()
    for row in executions:
        order_id = (row.get("broker_order_id") or "").strip()
        if not order_id or order_id not in actual_charges:
            out.unmatched.append(row.get("_id", "?"))
            continue
        out.corrections.append(Correction(
            execution_id=row.get("_id", "?"),
            symbol=row.get("symbol", "?"),
            estimated=row.get("charges") or money.ZERO,
            actual=actual_charges[order_id],
        ))
    return out


def adjustment(correction: Correction) -> dict:
    """The ledger entry that carries a correction.

    An `adjustment`, signed, naming the execution it belongs to. Not a
    `charges` entry: that kind records what a trade cost when it happened, and
    reusing it would make the correction indistinguishable from the original
    -- so the same trade would appear to have been charged twice.
    """
    return {
        "kind": "adjustment",
        "amount": correction.delta,
        "symbol": correction.symbol,
        "execution_id": correction.execution_id,
        "note": f"charge correction: {correction}",
    }
