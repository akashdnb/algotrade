"""FIFO lot matching and capital-gains classification.

Indian listed equity, as of this design: **STCG 20%** on holdings of 12 months
or less, **LTCG 12.5%** beyond that. §11q found the asymmetry large enough to
reverse a deployment conclusion, and found a basis bug that charged 36% on a
20% gain -- so the arithmetic here is deliberately explicit and separately
tested against hand-computed cases.

Two rules that are easy to get subtly wrong, both enforced below:

* **A sale can straddle the 12-month boundary.** One transaction may realise a
  long-term slice and a short-term slice at different rates. The matches carry
  per-slice status; a single status for the whole sale would be wrong.
* **Long-term and short-term do not net inside one sale.** They are separate
  buckets. Netting an LT loss against an ST gain understates the liability.

Loss carry-forward across transactions and across the financial year is a
*ledger* concern, not a transaction one, and is deliberately not modelled here
-- `estimated_tax()` is the liability arising from this sale alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Final

from algotrade.domain import money
from algotrade.domain.models import Holding, Lot, TaxStatus

STCG_RATE: Final[Decimal] = Decimal("0.20")
LTCG_RATE: Final[Decimal] = Decimal("0.125")


class InsufficientQuantity(ValueError):
    """Raised when a sale exceeds the position.

    Refusing beats clamping: a clamped sale books a phantom trade and leaves
    holdings inconsistent with the trade record, which is unrecoverable once
    it has happened.
    """


@dataclass(frozen=True, slots=True)
class LotMatch:
    """One lot's contribution to a sale."""

    lot_id: str
    quantity: int
    acquired_on: date
    buy_price: Decimal
    #: The lot's buy charges apportioned to `quantity`, so a partial sale
    #: carries only its share.
    buy_charges: Decimal
    tax_status: TaxStatus

    @property
    def cost_basis(self) -> Decimal:
        return money.rupees(self.buy_price * self.quantity + self.buy_charges)


@dataclass(frozen=True, slots=True)
class SaleResult:
    """The outcome of a FIFO sale: what was realised, and what is left."""

    matches: list[LotMatch]
    remaining_lots: list[Lot]
    quantity: int
    sell_price: Decimal
    sell_charges: Decimal
    sold_on: date

    @property
    def proceeds(self) -> Decimal:
        """Gross, before sell-side charges."""
        return money.rupees(self.sell_price * self.quantity)

    @property
    def cost_basis(self) -> Decimal:
        return money.sum_rupees(m.cost_basis for m in self.matches)

    @property
    def gain(self) -> Decimal:
        """Net of buy charges (in the basis) and sell charges (off proceeds)."""
        return money.rupees(self.proceeds - self.cost_basis - self.sell_charges)

    def _gain_for(self, status: TaxStatus) -> Decimal:
        """Gain attributable to one tax bucket.

        Sell charges are apportioned across buckets by quantity, so a straddling
        sale does not load the whole fee onto whichever slice is listed first.
        """
        slice_qty = sum(m.quantity for m in self.matches if m.tax_status == status)
        if slice_qty == 0:
            return money.ZERO
        proceeds = money.rupees(self.sell_price * slice_qty)
        basis = money.sum_rupees(
            m.cost_basis for m in self.matches if m.tax_status == status
        )
        charges = money.rupees(self.sell_charges * slice_qty / self.quantity)
        return money.rupees(proceeds - basis - charges)

    @property
    def short_term_gain(self) -> Decimal:
        return self._gain_for(TaxStatus.SHORT_TERM)

    @property
    def long_term_gain(self) -> Decimal:
        return self._gain_for(TaxStatus.LONG_TERM)

    def estimated_tax(self) -> Decimal:
        """Liability from THIS sale, treating each bucket independently.

        A loss contributes zero rather than a negative amount: it carries
        forward against future gains, which is a ledger-level concern outside
        this transaction.
        """
        st = max(self.short_term_gain, money.ZERO)
        lt = max(self.long_term_gain, money.ZERO)
        return money.rupees(st * STCG_RATE + lt * LTCG_RATE)


def sell_fifo(
    holding: Holding,
    *,
    quantity: int,
    price: Decimal,
    on: date,
    charges: Decimal = money.ZERO,
) -> SaleResult:
    """Consume `quantity` from `holding`, oldest lot first.

    Args:
        holding: The position being reduced. Not mutated -- the caller applies
            `remaining_lots`, so a failed persist cannot leave a half-sold book.
        quantity: Shares sold. Must not exceed the position.
        price: Realised price per share.
        on: Trade date, used for the 12-month test.
        charges: Sell-side charges, deducted from proceeds.

    Raises:
        InsufficientQuantity: if `quantity` exceeds the position.
    """
    if quantity <= 0:
        raise ValueError(f"quantity must be > 0, got {quantity}")

    available = holding.quantity
    if quantity > available:
        raise InsufficientQuantity(
            f"{holding.symbol}: cannot sell {quantity}, holding is {available}"
        )

    # Sort by date, then lot_id so equal-dated lots consume deterministically --
    # otherwise the same sale produces different lot ids run to run, and the
    # audit trail stops being reproducible.
    ordered = sorted(holding.lots, key=lambda lot: (lot.acquired_on, lot.lot_id))

    matches: list[LotMatch] = []
    remaining: list[Lot] = []
    outstanding = quantity

    for lot in ordered:
        if outstanding == 0:
            remaining.append(lot)
            continue

        taken = min(outstanding, lot.quantity)
        matches.append(
            LotMatch(
                lot_id=lot.lot_id,
                quantity=taken,
                acquired_on=lot.acquired_on,
                buy_price=lot.price,
                # Apportion the lot's charges to the slice being sold.
                buy_charges=money.rupees(lot.charges * taken / lot.quantity),
                tax_status=lot.tax_status(on),
            )
        )
        outstanding -= taken

        if taken < lot.quantity:
            left = lot.quantity - taken
            remaining.append(
                Lot(
                    lot_id=lot.lot_id,
                    acquired_on=lot.acquired_on,
                    quantity=left,
                    price=lot.price,
                    charges=money.rupees(lot.charges * left / lot.quantity),
                    source_set_id=lot.source_set_id,
                )
            )

    return SaleResult(
        matches=matches,
        remaining_lots=remaining,
        quantity=quantity,
        sell_price=price,
        sell_charges=charges,
        sold_on=on,
    )
