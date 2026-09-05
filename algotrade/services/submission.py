"""Placing one order, survivably.

This is the first irreversible write in the system. Everything else rebuilds:
holdings from executions, cash from the ledger, a bad snapshot from a repair
job. An order that reached the exchange cannot be undone by re-running
anything, so the sequence matters more than the code.

    write intent  ->  submit  ->  confirm

**Never submit-then-write.** A crash after submitting but before recording
leaves an order nobody knows about: the next attempt places it again, and the
book ends up with twice the position and no record of why.

Writing the intent first inverts the failure. A crash then leaves a row saying
*"we may have submitted this — go and ask"*, and asking is cheap: every order
carries a deterministic `tag`, so `orders(tag=...)` answers definitively
whether it exists. An unanswerable question becomes a lookup.

**The tag is the join key.** Kite has no client-supplied idempotency token, so
we make one. Matching on `(symbol, side, quantity, timestamp)` instead would be
ambiguous the moment the same name is traded twice in a session -- which a
two-day protocol makes ordinary rather than exotic.

Pure but for the injected client and store: no network of its own, no clock.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from algotrade.brokers.base import BrokerClient, BrokerError, OrderRequest

#: Kite accepts an alphanumeric tag of at most 20 characters, so the natural
#: key -- `paper_momentum_v1_001:2026-10:r1:3` -- does not fit. A digest of it
#: does, and stays deterministic, which is the property that matters.
TAG_LENGTH: int = 16

# --------------------------------------------------------------- states ----

#: Written before the broker is called. Means: we may have submitted this.
INTENT = "intent"
#: The broker accepted the request and returned an order id.
SUBMITTED = "submitted"
#: Terminal, and reconciled against the broker.
FILLED = "filled"
PARTIAL = "partial"
REJECTED = "rejected"
CANCELLED = "cancelled"

#: States that mean the work is done and must never be repeated.
CLOSED: frozenset[str] = frozenset({SUBMITTED, FILLED, PARTIAL, REJECTED,
                                    CANCELLED})


class SubmissionError(RuntimeError):
    """The submission could not be completed safely.

    Distinct from a rejected order: a rejection is an *outcome* the broker
    reports, and is recorded. This means we do not know what happened, which
    is the one situation that must stop a human rather than continue.
    """


def tag_for(set_id: str, seq: int) -> str:
    """A deterministic, broker-legal tag for one instruction.

    Deterministic so a retry after a crash computes the same value and can
    therefore find the order it may already have placed. A random tag would
    make the recovery query impossible, which is the whole mechanism.
    """
    if seq < 1:
        raise ValueError(f"seq must be positive, got {seq}")
    digest = hashlib.sha256(f"{set_id}:{seq}".encode()).hexdigest()
    return digest[:TAG_LENGTH]


@dataclass(frozen=True, slots=True)
class Record:
    """What the store knows about one instruction."""

    tag: str
    state: str
    broker_order_id: str = ""
    message: str = ""

    @property
    def closed(self) -> bool:
        return self.state in CLOSED


class Store(Protocol):
    """Persistence for submissions. Implemented by a repository."""

    def get(self, tag: str) -> Record | None: ...

    def record_intent(self, *, tag: str, set_id: str, seq: int,
                      symbol: str, side: str, quantity: int,
                      now: datetime) -> None: ...

    def mark_submitted(self, *, tag: str, broker_order_id: str,
                       now: datetime) -> None: ...


@dataclass(frozen=True, slots=True)
class Outcome:
    """What happened, and whether anything was sent this time."""

    tag: str
    state: str
    broker_order_id: str
    #: False when a prior attempt had already placed this order. The caller
    #: shows it differently: "already placed" is reassurance, not success.
    placed: bool


def submit(
    request: OrderRequest,
    *,
    set_id: str,
    seq: int,
    client: BrokerClient,
    store: Store,
    now: datetime,
) -> Outcome:
    """Place one order at most once.

    Safe to call again after any failure. The three recoverable positions:

    * **no record** -- nothing was sent; write intent and send.
    * **intent** -- we may have sent it. Ask the broker by tag before doing
      anything: found means adopt it, absent means it never landed.
    * **closed** -- already done. Returns the existing outcome and sends
      nothing, which is what makes a double click harmless.
    """
    tag = tag_for(set_id, seq)
    existing = store.get(tag)

    if existing is not None and existing.closed:
        return Outcome(tag=tag, state=existing.state,
                       broker_order_id=existing.broker_order_id, placed=False)

    if existing is not None and existing.state == INTENT:
        # The dangerous case, and the reason the intent is written first.
        adopted = _find_by_tag(client, tag)
        if adopted is not None:
            store.mark_submitted(tag=tag, broker_order_id=adopted, now=now)
            return Outcome(tag=tag, state=SUBMITTED,
                           broker_order_id=adopted, placed=False)
        # Absent at the broker: the previous attempt died before it landed,
        # so sending now places the order once, not twice.
    else:
        store.record_intent(tag=tag, set_id=set_id, seq=seq,
                            symbol=request.symbol, side=request.side,
                            quantity=request.quantity, now=now)

    order_id = client.place(_tagged(request, tag))
    if not order_id:
        # A blank id leaves nothing to reconcile against, so the row stays at
        # `intent` and the next attempt asks the broker rather than assuming.
        raise SubmissionError(
            f"{tag}: the broker accepted the order but returned no id; "
            "check the order book before retrying"
        )
    store.mark_submitted(tag=tag, broker_order_id=order_id, now=now)
    return Outcome(tag=tag, state=SUBMITTED, broker_order_id=order_id,
                   placed=True)


def _tagged(request: OrderRequest, tag: str) -> OrderRequest:
    """The request as it will be sent, carrying the tag we can find it by."""
    if request.tag == tag:
        return request
    return OrderRequest(
        symbol=request.symbol, side=request.side, quantity=request.quantity,
        order_type=request.order_type, tag=tag, price=request.price,
        market_protection=request.market_protection,
    )


def _find_by_tag(client: BrokerClient, tag: str) -> str | None:
    """The broker order id for this tag, if the order exists.

    A broker that will not answer raises rather than returning None: "we could
    not ask" and "it is not there" lead to opposite actions, and treating the
    first as the second is how one order becomes two.
    """
    try:
        orders = client.orders(tag=tag)
    except BrokerError as exc:
        raise SubmissionError(
            f"{tag}: cannot tell whether this was already placed -- the "
            f"broker did not answer ({exc}). Check the order book by hand "
            "before retrying; resending blind could double the position."
        ) from exc
    for order in orders:
        if order.order_id:
            return order.order_id
    return None
