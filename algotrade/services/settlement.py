"""Turning what the broker did into what the book records.

Placing an order is half of it. Until the fills come back and reach
`ExecutionService`, the book does not know the trade happened -- and every
number downstream is computed from executions, so an unrecorded fill is a
portfolio that quietly stops describing reality.

    orders(tag)  ->  terminal?  ->  trades(order_id)  ->  fills

**Only terminal orders are settled.** An OPEN order has no final quantity or
price; recording one would write a fill that later changes, and executions are
never patched in place.

**Charges are the rate-card estimate, not the contract note.** Kite's trades
API carries price and quantity; the real charges arrive later, and STT and
stamp duty post at end of day. The estimate goes in now and a later pass
corrects it as a separate ledger entry -- never by editing an execution
(design §9).

Pure but for the injected client: no repository, no clock, no network of its
own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Callable, Mapping, Sequence

from algotrade.brokers.base import BrokerClient, BrokerError, BrokerOrder
from algotrade.domain import money
from algotrade.services.submission import (
    CANCELLED,
    FILLED,
    PARTIAL,
    REJECTED,
)

#: Broker status -> the submission state it settles to.
_STATE_OF: dict[str, str] = {
    "COMPLETE": FILLED,
    "REJECTED": REJECTED,
    "CANCELLED": CANCELLED,
}


@dataclass(frozen=True, slots=True)
class Fill:
    """One instruction's outcome, ready for `ExecutionService`."""

    seq: int
    symbol: str
    quantity: int
    price: Decimal
    charges: Decimal
    broker_order_id: str
    filled_at: datetime | None
    #: Set when nothing was filled, so the caller records a skip with a reason
    #: rather than a zero-quantity execution.
    skipped: str = ""

    @property
    def is_skip(self) -> bool:
        return bool(self.skipped)


@dataclass
class Settlement:
    """What could be settled, and what could not."""

    fills: list[Fill] = field(default_factory=list)
    #: tag -> the state it should be recorded as.
    states: dict[str, str] = field(default_factory=dict)
    #: tag -> why it is still open. Not a failure; most of a session is spent
    #: here.
    pending: dict[str, str] = field(default_factory=dict)
    unreachable: str = ""

    @property
    def complete(self) -> bool:
        return not self.pending and not self.unreachable


def _weighted_price(trades: Sequence) -> Decimal:
    """Quantity-weighted average across an order's fills.

    An order can fill in pieces at different prices. Taking the first or the
    last would misstate the cost basis, and cost basis is what tax and every
    P&L figure is computed from.
    """
    total_qty = sum(t.quantity for t in trades)
    if total_qty <= 0:
        return money.ZERO
    gross = sum(t.price * t.quantity for t in trades)
    return money.rupees(gross / total_qty)


def settle(
    submissions: Sequence[Mapping],
    *,
    client: BrokerClient,
    charge_for: Callable[[Decimal, str], Decimal] | None = None,
) -> Settlement:
    """Fetch outcomes for submitted instructions and build fills.

    `submissions` are rows carrying `_id` (the tag), `item_seq`, `symbol` and
    `state`. Only those already sent are looked up; an `intent` row is left
    alone, because its own recovery path -- asking the broker by tag before
    resending -- is the thing that resolves it.
    """
    out = Settlement()
    try:
        orders = {o.tag: o for o in client.orders() if o.tag}
    except BrokerError as exc:
        # Reported, never treated as "nothing filled". An unreachable broker
        # and an empty order book look identical here and mean opposite
        # things.
        out.unreachable = str(exc)
        return out

    for row in submissions:
        tag = row["_id"]
        order = orders.get(tag)
        if order is None:
            out.pending[tag] = "not in the order book yet"
            continue
        if not order.terminal:
            out.pending[tag] = f"{order.status.lower()} at the broker"
            continue

        out.states[tag] = _settled_state(order)
        if order.filled_quantity <= 0:
            out.fills.append(Fill(
                seq=int(row["item_seq"]), symbol=order.symbol, quantity=0,
                price=money.ZERO, charges=money.ZERO,
                broker_order_id=order.order_id, filled_at=None,
                skipped=order.status_message or order.status.lower(),
            ))
            continue

        trades = client.trades(order_id=order.order_id)
        price = _weighted_price(trades) if trades else (
            order.average_price or money.ZERO)
        value = money.rupees(price * order.filled_quantity)
        out.fills.append(Fill(
            seq=int(row["item_seq"]),
            symbol=order.symbol,
            quantity=order.filled_quantity,
            price=price,
            charges=charge_for(value, order.side) if charge_for else money.ZERO,
            broker_order_id=order.order_id,
            filled_at=trades[0].filled_at if trades else None,
        ))
    return out


def _settled_state(order: BrokerOrder) -> str:
    """The submission state for a terminal order.

    A partial fill is neither filled nor rejected, and calling it either would
    misreport what happened -- the strategy asked for a position it did not
    get, which the next rebalance has to see.
    """
    if order.partial:
        return PARTIAL
    return _STATE_OF.get(order.status, CANCELLED)
