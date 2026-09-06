"""The broker interface, and the value types that cross it.

Every field here is what *the broker* says, decoded into our own types at the
boundary. Nothing downstream should ever see a raw Kite dict -- a change in
their JSON must break here, in one place, and not in a template.

**`place()` is the only mutating method**, and it is deliberately the only one.
`tests/unit/test_brokers_boundary.py` fails the build if it is called from
anywhere but this package and `services/submission.py`, the same way
`test_domain_purity.py` fails the build if `domain/` imports a database.

Money is `Decimal` throughout, never float. Quantities are `int`, because a
fractional share cannot be ordered on the NSE cash segment.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

#: Terminal states -- an order in one of these will not change again.
TERMINAL: frozenset[str] = frozenset({"COMPLETE", "REJECTED", "CANCELLED"})

#: Order types we are willing to send. Anything else is a bug, not a feature.
MARKET: str = "MARKET"
LIMIT: str = "LIMIT"


class BrokerError(RuntimeError):
    """The broker refused or could not answer. Never raised for a rejected
    order -- a rejection is an *outcome*, carried on `BrokerOrder.status`."""


class BrokerAuthError(BrokerError):
    """The session is no longer accepted, whatever we believed about it.

    Separate from `BrokerError` because the remedy is different and specific:
    authorise again. A caller that cannot tell this apart can only offer
    "something went wrong", which on a page whose next action is a login
    button is a worse answer than it needs to be.

    Reachable even with a valid-looking stored expiry -- a master logout from
    Kite's web terminal kills the token immediately, and no margin predicts it.
    """


@dataclass(frozen=True, slots=True)
class BrokerHolding:
    """One line of the demat, as the broker reports it."""

    symbol: str
    isin: str
    quantity: int
    average_price: Decimal


@dataclass(frozen=True, slots=True)
class BrokerFunds:
    """Cash at the broker.

    `available` is what can be spent now; `used` is committed to open orders
    and unsettled obligations. The two are reported separately because the
    difference is exactly what the two-day protocol has to reason about.
    """

    available: Decimal
    used: Decimal

    @property
    def total(self) -> Decimal:
        return self.available + self.used


@dataclass(frozen=True, slots=True)
class OrderRequest:
    """What we ask for. Distinct from `BrokerOrder`, which is what happened."""

    symbol: str
    side: str            # "buy" | "sell"
    quantity: int
    order_type: str      # MARKET | LIMIT
    tag: str             # our idempotency key, echoed back by the broker
    price: Decimal | None = None
    #: Percent. Required non-zero on MARKET orders -- Kite rejects an order
    #: with a protection value of 0 as an unprotected market order.
    market_protection: Decimal | None = None

    def __post_init__(self) -> None:
        if self.side not in ("buy", "sell"):
            raise ValueError(f"side must be buy or sell, got {self.side!r}")
        if self.quantity < 1:
            raise ValueError(f"quantity must be positive, got {self.quantity}")
        if self.order_type == LIMIT and self.price is None:
            raise ValueError("a LIMIT order needs a price")
        if self.order_type == MARKET and not self.market_protection:
            raise ValueError(
                "a MARKET order needs a non-zero market_protection; "
                "the broker rejects unprotected market orders"
            )


@dataclass(frozen=True, slots=True)
class BrokerOrder:
    """An order as the broker currently sees it."""

    order_id: str
    symbol: str
    side: str
    quantity: int
    filled_quantity: int
    status: str
    tag: str = ""
    average_price: Decimal | None = None
    status_message: str = ""

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL

    @property
    def partial(self) -> bool:
        """Terminal, but not everything we asked for was filled."""
        return self.terminal and 0 < self.filled_quantity < self.quantity


@dataclass(frozen=True, slots=True)
class BrokerTrade:
    """One fill. An order may produce several."""

    trade_id: str
    order_id: str
    symbol: str
    quantity: int
    price: Decimal
    filled_at: datetime


@runtime_checkable
class BrokerClient(Protocol):
    """What the rest of the system is allowed to ask a broker."""

    def holdings(self) -> list[BrokerHolding]: ...

    def margins(self) -> BrokerFunds: ...

    def orders(self, *, tag: str | None = None) -> list[BrokerOrder]: ...

    def trades(self, *, order_id: str) -> list[BrokerTrade]: ...

    def instruments(self) -> list[dict]:
        """The tradeable-instrument dump, for building symbol identity.

        On the protocol so the stub must answer it too. It was omitted at
        first, which meant `build_symbol_map` could only run against a live
        session -- the one job that establishes what a symbol IS could not be
        rehearsed.
        """
        ...

    def place(self, req: OrderRequest) -> str:
        """Submit an order. Returns the broker's order id.

        The ONLY mutating call in this system. See the module docstring.
        """
        ...
