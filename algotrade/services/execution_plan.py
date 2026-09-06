"""Turning a frozen basket line into an order, and deciding what may go next.

Two decisions live here rather than in a route, because both are about
correctness rather than presentation:

**What the order actually is.** A stored line carries a side, a quantity and
the price it was approved at. Becoming an `OrderRequest` adds only the things
the broker requires -- a tag, and market protection on a MARKET order, which
Kite rejects outright when it is zero.

**Whether it may be sent yet.** Sells fund buys, and at T+1 part of the
proceeds may not be usable until tomorrow, so no buy is offered until every
sell has reached a terminal state (design §7). That is a rule about money, not
about the order of a table.

Pure: no repository, no clock, no network.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Mapping

from algotrade.brokers.base import LIMIT, MARKET, OrderRequest
from algotrade.services.submission import (
    CLOSED,
    INTENT,
    SETTLED,
    SUBMITTED,
    tag_for,
)

SELL = "sell"
BUY = "buy"

#: States in which an instruction still has work to do.
OPEN_STATES: frozenset[str] = frozenset({INTENT, SUBMITTED})


class NotReady(RuntimeError):
    """This instruction may not be sent yet, and why."""


def order_for(
    line: Mapping,
    *,
    set_id: str,
    market_protection: Decimal,
) -> OrderRequest:
    """The order to place for one frozen basket line.

    The price comes from the basket, never from a fresh quote: the whole
    purpose of freezing it was that the number sent is the number approved.
    """
    order_type = str(line.get("order_type") or LIMIT).upper()
    if order_type not in (LIMIT, MARKET):
        raise NotReady(f"unknown order type {order_type!r}")

    seq = int(line["seq"])
    return OrderRequest(
        symbol=line["symbol"],
        side=str(line["action"]),
        quantity=int(line["quantity"]),
        order_type=order_type,
        tag=tag_for(set_id, seq),
        # A LIMIT order carries the approved price. A MARKET order carries
        # none -- naming one would imply a control it does not have.
        price=Decimal(str(line["price"])) if order_type == LIMIT else None,
        market_protection=market_protection if order_type == MARKET else None,
    )


@dataclass(frozen=True, slots=True)
class Gate:
    """Whether an instruction may be sent, and what to say when it may not."""

    allowed: bool
    reason: str = ""


def gate(
    line: Mapping,
    *,
    states: Mapping[int, str],
    lines: Iterable[Mapping],
) -> Gate:
    """May this line be sent now?

    `states` maps seq to the submission state already recorded for it.

    Three refusals, in the order they matter:

    * **already sent** -- resending is prevented by `submission.submit`
      anyway, but offering the button implies it would do something;
    * **a sell is still open** -- buys wait, because proceeds fund them and at
      T+1 part of that money is not yet usable;
    * nothing else. A rejected sell does not block: it is terminal, the money
      simply did not arrive, and the affordability recheck handles it.
    """
    seq = int(line["seq"])
    state = states.get(seq, "")
    if state in CLOSED:
        return Gate(False, "already sent")
    if state in OPEN_STATES:
        return Gate(False, "in flight — confirm it before sending again")

    if str(line["action"]) == BUY:
        # SETTLED, not CLOSED. A sell that has merely been `submitted` has
        # not produced any money yet, and releasing the buys on it is how a
        # rehearsal placed a purchase its funding sale had not made.
        pending = [
            int(other["seq"]) for other in lines
            if str(other["action"]) == SELL
            and states.get(int(other["seq"]), "") not in SETTLED
        ]
        if pending:
            return Gate(
                False,
                f"{len(pending)} sell{'' if len(pending) == 1 else 's'} not "
                "settled — proceeds fund the buys, and an order that has only "
                "been sent has not produced any money yet",
            )
    return Gate(True)


def remaining(lines: Iterable[Mapping], states: Mapping[int, str]) -> int:
    """How many instructions are still to be sent."""
    return sum(1 for line in lines
               if states.get(int(line["seq"]), "") not in CLOSED)
