"""A deterministic broker, for exercising the submit path without a broker.

Two jobs, and the second is the one that matters:

**Prove our code runs.** Per-trade submission makes a real rejection cheap to
*discover* -- you find out on order 1 rather than after 30 -- but it does not
make the rejection-handling code ever have run. Everything downstream of a
`REJECTED` status is untested until something rejects.

**Produce the rejections a happy path never sees.** `fail_next()` arms the
broker's actual refusal taxonomy: margin shortfall, price band, freeze
quantity, and the surveillance-series restrictions that apply to a name in BE.
HFCL is in BE as this is written, so none of these are hypothetical.

Fills happen at the requested price with **zero slippage**, and partial fills
occur only when asked for. A stub that invented slippage would make the
rehearsal's numbers look real while being fiction.

Deterministic on purpose: no clock, no randomness. Order ids come from a
counter, so a test can assert on them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from algotrade.brokers.base import (
    BrokerFunds,
    BrokerHolding,
    BrokerOrder,
    BrokerTrade,
    OrderRequest,
)

def mirroring(holdings, *, universe=None) -> "StubBroker":
    """A stub whose demat matches a book's own holdings.

    For **tests and rehearsals of the reconciliation logic**, not for the
    dashboard. `/broker` reads the real account through `KiteBroker` whichever
    book is selected, and that is correct: the demat is your account, not a
    property of whichever portfolio you are looking at. Substituting a stub
    there would have the page fabricate a demat out of the book it is supposed
    to be checking against -- a reconciliation that always agrees, because both
    sides came from the same place.

    Seeded from a book, it gives the comparison two sides so a deliberate
    mismatch can be introduced: edit one quantity and see exactly what a real
    discrepancy renders as.

    It does NOT invent funds. Cash at a broker is not derivable from a book's
    positions, and a made-up balance would let a rehearsal size a basket the
    real account could never afford.
    """
    return StubBroker(
        demat=[
            BrokerHolding(symbol=h.symbol, isin=getattr(h, "isin", "") or "",
                          quantity=h.quantity,
                          average_price=_average_cost(h))
            for h in holdings if h.quantity > 0
        ],
        universe=list(universe or ()),
    )


def _average_cost(holding) -> Decimal:
    """Weighted average across the lots, which is what a broker reports."""
    total = sum(lot.quantity for lot in holding.lots)
    if total <= 0:
        return Decimal("0")
    gross = sum(lot.price * lot.quantity for lot in holding.lots)
    return (gross / total).quantize(Decimal("0.01"))


#: Refusals the real broker produces that a happy path never will.
REJECTIONS: dict[str, str] = {
    "margin": "Insufficient funds for this order",
    "price_band": "Price is outside the daily price band",
    "freeze": "Quantity exceeds the exchange freeze limit",
    "surveillance": "Scrip is under surveillance; order not permitted",
}


@dataclass
class StubBroker:
    """An in-memory broker. Satisfies `BrokerClient` minus nothing."""

    funds: BrokerFunds = field(
        default_factory=lambda: BrokerFunds(available=Decimal("0.00"),
                                            used=Decimal("0.00")))
    demat: list[BrokerHolding] = field(default_factory=list)
    #: Rows shaped like Kite's `/instruments` CSV, for the symbol map.
    universe: list[dict] = field(default_factory=list)
    #: Every order ever placed, in order.
    placed: list[BrokerOrder] = field(default_factory=list)

    _trades: dict[str, list[BrokerTrade]] = field(default_factory=dict)
    _counter: int = 0
    _fail_reason: str | None = None
    _fill_quantity: int | None = None
    _now: datetime = datetime(2026, 10, 1, 9, 20)

    # ----------------------------------------------------------- arming ---

    def fail_next(self, reason: str) -> None:
        """Reject the next order. `reason` must be a key of `REJECTIONS`."""
        if reason not in REJECTIONS:
            raise ValueError(
                f"unknown rejection {reason!r}; expected one of "
                f"{sorted(REJECTIONS)}"
            )
        self._fail_reason = reason

    def partially_fill_next(self, quantity: int) -> None:
        """Fill only `quantity` of the next order, then stop."""
        self._fill_quantity = quantity

    # ------------------------------------------------------------- reads ---

    def holdings(self) -> list[BrokerHolding]:
        return list(self.demat)

    def margins(self) -> BrokerFunds:
        return self.funds

    def instruments(self) -> list[dict]:
        """Whatever the rehearsal was seeded with, in Kite's dump shape.

        Empty by default: an invented universe would let `build_symbol_map`
        appear to work against symbols that do not exist, which is worse than
        it plainly having nothing to map.
        """
        return list(self.universe)

    def orders(self, *, tag: str | None = None) -> list[BrokerOrder]:
        if tag is None:
            return list(self.placed)
        return [o for o in self.placed if o.tag == tag]

    def trades(self, *, order_id: str) -> list[BrokerTrade]:
        return list(self._trades.get(order_id, ()))

    # ------------------------------------------------------------ writes ---

    def place(self, req: OrderRequest) -> str:
        self._counter += 1
        order_id = f"stub{self._counter:012d}"

        if self._fail_reason is not None:
            reason, self._fail_reason = self._fail_reason, None
            self.placed.append(BrokerOrder(
                order_id=order_id, symbol=req.symbol, side=req.side,
                quantity=req.quantity, filled_quantity=0, status="REJECTED",
                tag=req.tag, status_message=REJECTIONS[reason],
            ))
            return order_id

        filled = req.quantity
        status = "COMPLETE"
        if self._fill_quantity is not None:
            filled, self._fill_quantity = min(self._fill_quantity,
                                              req.quantity), None
            status = "COMPLETE" if filled == req.quantity else "CANCELLED"

        price = req.price if req.price is not None else Decimal("100.00")
        self.placed.append(BrokerOrder(
            order_id=order_id, symbol=req.symbol, side=req.side,
            quantity=req.quantity, filled_quantity=filled, status=status,
            tag=req.tag, average_price=price if filled else None,
        ))
        if filled:
            self._trades[order_id] = [BrokerTrade(
                trade_id=f"{order_id}:1", order_id=order_id, symbol=req.symbol,
                quantity=filled, price=price, filled_at=self._now,
            )]
        return order_id
