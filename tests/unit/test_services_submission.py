"""Tests for placing one order at most once (design §8).

Most of this file is crash injection. The sequence -- write intent, submit,
confirm -- exists entirely so that a failure at any point leaves a recoverable
position, and the only way to know it does is to fail at each point on purpose.

The case that matters most is a crash **between submit and confirm**: the order
exists at the broker and nothing here knows it. Retrying must find it, not
place a second one.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from algotrade.brokers.base import (
    BrokerError,
    BrokerOrder,
    LIMIT,
    OrderRequest,
)
from algotrade.brokers.stub import StubBroker
from algotrade.services import submission

NOW = datetime(2026, 10, 1, 9, 20)
SET_ID = "paper_momentum_v1_001:2026-10:r1"


class FakeStore:
    """The submissions collection, in memory."""

    def __init__(self) -> None:
        self.rows: dict[str, submission.Record] = {}
        self.writes: list[str] = []

    def get(self, tag):
        return self.rows.get(tag)

    def record_intent(self, *, tag, set_id, seq, symbol, side, quantity, now):
        self.writes.append(f"intent:{tag}")
        self.rows[tag] = submission.Record(tag=tag, state=submission.INTENT)

    def mark_submitted(self, *, tag, broker_order_id, now):
        self.writes.append(f"submitted:{tag}")
        self.rows[tag] = submission.Record(
            tag=tag, state=submission.SUBMITTED,
            broker_order_id=broker_order_id)


class RefusingBroker(StubBroker):
    """A broker that cannot answer `orders`."""

    def orders(self, *, tag=None):
        raise BrokerError("connection reset")


def _request(quantity: int = 10) -> OrderRequest:
    return OrderRequest(symbol="RELIANCE", side="buy", quantity=quantity,
                        order_type=LIMIT, tag="", price=Decimal("2402.75"))


def _submit(client, store, seq: int = 1, **kwargs):
    return submission.submit(_request(**kwargs), set_id=SET_ID, seq=seq,
                             client=client, store=store, now=NOW)


# ------------------------------------------------------------------ tag ---

def test_the_tag_is_deterministic():
    """A random tag would make the recovery query impossible, and the
    recovery query is the entire mechanism."""
    assert submission.tag_for(SET_ID, 3) == submission.tag_for(SET_ID, 3)


def test_different_instructions_get_different_tags():
    assert submission.tag_for(SET_ID, 1) != submission.tag_for(SET_ID, 2)
    assert submission.tag_for("other:2026-10:r1", 1) != submission.tag_for(
        SET_ID, 1)


def test_the_tag_fits_the_brokers_limit_and_is_alphanumeric():
    """Kite accepts at most 20 alphanumeric characters, so the natural key --
    `paper_momentum_v1_001:2026-10:r1:3` -- does not fit."""
    tag = submission.tag_for(SET_ID, 3)
    assert len(tag) <= 20
    assert tag.isalnum()


def test_a_non_positive_seq_is_refused():
    with pytest.raises(ValueError, match="seq"):
        submission.tag_for(SET_ID, 0)


# --------------------------------------------------------- happy path ---

def test_a_first_submission_writes_intent_before_sending():
    """The ordering is the whole design. Submitting first would leave an order
    nobody knows about when the process dies in between."""
    store, broker = FakeStore(), StubBroker()
    out = _submit(broker, store)
    assert store.writes[0].startswith("intent:")
    assert store.writes[1].startswith("submitted:")
    assert out.placed and out.state == submission.SUBMITTED
    assert out.broker_order_id


def test_the_order_is_sent_carrying_its_tag():
    store, broker = FakeStore(), StubBroker()
    out = _submit(broker, store)
    assert broker.orders()[0].tag == out.tag


# ----------------------------------------------------- already finished ---

def test_a_closed_submission_sends_nothing_and_reports_the_first_result():
    """What makes a double click harmless."""
    store, broker = FakeStore(), StubBroker()
    first = _submit(broker, store)
    second = _submit(broker, store)

    assert second.broker_order_id == first.broker_order_id
    assert not second.placed
    assert len(broker.placed) == 1


@pytest.mark.parametrize("state", sorted(submission.CLOSED))
def test_every_closed_state_stops_a_resend(state):
    store, broker = FakeStore(), StubBroker()
    tag = submission.tag_for(SET_ID, 1)
    store.rows[tag] = submission.Record(tag=tag, state=state,
                                        broker_order_id="existing")
    out = _submit(broker, store)
    assert broker.placed == []
    assert out.broker_order_id == "existing" and not out.placed


# ------------------------------------------------------ crash injection ---

def test_a_crash_after_intent_but_before_the_order_landed_sends_it_once():
    """The previous attempt died before the broker saw anything. Asking by tag
    returns nothing, so sending now places the order once, not twice."""
    store, broker = FakeStore(), StubBroker()
    tag = submission.tag_for(SET_ID, 1)
    store.rows[tag] = submission.Record(tag=tag, state=submission.INTENT)

    out = _submit(broker, store)
    assert out.placed
    assert len(broker.placed) == 1


def test_a_crash_between_submit_and_confirm_adopts_the_existing_order():
    """The dangerous case: the order exists and nothing here knows it.
    Retrying must FIND it. Placing a second one doubles the position."""
    store, broker = FakeStore(), StubBroker()
    tag = submission.tag_for(SET_ID, 1)

    # Simulate the crash: the broker has the order, the store is still at
    # `intent` because the confirming write never happened.
    order_id = broker.place(OrderRequest(
        symbol="RELIANCE", side="buy", quantity=10, order_type=LIMIT,
        tag=tag, price=Decimal("2402.75")))
    store.rows[tag] = submission.Record(tag=tag, state=submission.INTENT)

    out = _submit(broker, store)
    assert out.broker_order_id == order_id
    assert not out.placed
    assert len(broker.placed) == 1, "a second order was placed"


def test_recovery_refuses_when_the_broker_will_not_answer():
    """"We could not ask" and "it is not there" lead to opposite actions.
    Treating the first as the second is how one order becomes two."""
    store, broker = FakeStore(), RefusingBroker()
    tag = submission.tag_for(SET_ID, 1)
    store.rows[tag] = submission.Record(tag=tag, state=submission.INTENT)

    with pytest.raises(submission.SubmissionError, match="already placed"):
        _submit(broker, store)
    assert broker.placed == []


def test_a_broker_id_that_comes_back_blank_leaves_the_row_recoverable():
    """Nothing to reconcile against, so the row must stay at `intent` and the
    next attempt must ask rather than assume."""
    class Blank(StubBroker):
        def place(self, req):
            super().place(req)
            return ""

    store, broker = FakeStore(), Blank()
    with pytest.raises(submission.SubmissionError, match="no id"):
        _submit(broker, store)
    assert store.rows[submission.tag_for(SET_ID, 1)].state == submission.INTENT


# --------------------------------------------------------- rejections ---

def test_a_rejected_order_is_an_outcome_not_an_exception():
    """A rejection is what the broker decided; it is recorded, not raised.
    Only "we do not know what happened" stops a human."""
    store, broker = FakeStore(), StubBroker()
    broker.fail_next("margin")

    out = _submit(broker, store)
    assert out.placed
    assert broker.orders(tag=out.tag)[0].status == "REJECTED"


def test_a_rejected_order_still_records_its_id_so_it_is_never_resent():
    store, broker = FakeStore(), StubBroker()
    broker.fail_next("surveillance")
    first = _submit(broker, store)
    second = _submit(broker, store)
    assert len(broker.placed) == 1
    assert second.broker_order_id == first.broker_order_id


# ------------------------------------------------------------ isolation ---

def test_two_instructions_in_one_set_do_not_collide():
    store, broker = FakeStore(), StubBroker()
    one = _submit(broker, store, seq=1)
    two = _submit(broker, store, seq=2)
    assert one.tag != two.tag
    assert one.broker_order_id != two.broker_order_id
    assert len(broker.placed) == 2


def test_an_order_tagged_by_someone_else_is_not_adopted():
    """`orders(tag=...)` is filtered by the broker; a tag we did not compute
    must never satisfy our recovery query."""
    store, broker = FakeStore(), StubBroker()
    broker.place(OrderRequest(symbol="OTHER", side="buy", quantity=1,
                              order_type=LIMIT, tag="somebodyelse",
                              price=Decimal("1")))
    tag = submission.tag_for(SET_ID, 1)
    store.rows[tag] = submission.Record(tag=tag, state=submission.INTENT)

    out = _submit(broker, store)
    assert out.placed
    assert len(broker.placed) == 2
