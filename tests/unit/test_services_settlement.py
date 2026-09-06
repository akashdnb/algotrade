"""Tests for turning broker outcomes into recordable fills (design §8, §9).

An unrecorded fill is a portfolio that quietly stops describing reality --
every number downstream is computed from executions -- so the failure modes
that matter are the ones where this reports something that did not happen, or
stays silent about something that did.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from algotrade.brokers.base import BrokerError, BrokerOrder, BrokerTrade
from algotrade.services import settlement
from algotrade.services.submission import (
    CANCELLED,
    FILLED,
    PARTIAL,
    REJECTED,
    SUBMITTED,
)

FILLED_AT = datetime(2026, 10, 1, 9, 20)


class FakeBroker:
    def __init__(self, orders, trades=None, boom=False):
        self._orders = orders
        self._trades = trades or {}
        self._boom = boom

    def orders(self, *, tag=None):
        if self._boom:
            raise BrokerError("connection reset")
        return list(self._orders)

    def trades(self, *, order_id):
        return list(self._trades.get(order_id, ()))


def _order(tag="t1", status="COMPLETE", quantity=10, filled=10,
           order_id="o1", side="buy", message="", avg="100.00"):
    return BrokerOrder(order_id=order_id, symbol="RELIANCE", side=side,
                       quantity=quantity, filled_quantity=filled,
                       status=status, tag=tag,
                       average_price=Decimal(avg), status_message=message)


def _trade(qty, price, order_id="o1", tid="tr1"):
    return BrokerTrade(trade_id=tid, order_id=order_id, symbol="RELIANCE",
                       quantity=qty, price=Decimal(price), filled_at=FILLED_AT)


def _row(tag="t1", seq=1):
    return {"_id": tag, "item_seq": seq, "symbol": "RELIANCE",
            "state": SUBMITTED}


def _charges(value, side):
    return Decimal("15.34")


# ------------------------------------------------------------- complete ---

def test_a_completed_order_becomes_a_fill():
    got = settlement.settle(
        [_row()], client=FakeBroker([_order()], {"o1": [_trade(10, "100.00")]}),
        charge_for=_charges)
    assert len(got.fills) == 1
    fill = got.fills[0]
    assert (fill.seq, fill.quantity, fill.price) == (1, 10, Decimal("100.00"))
    assert fill.broker_order_id == "o1"
    assert got.states["t1"] == FILLED


def test_the_price_is_quantity_weighted_across_partial_trades():
    """An order fills in pieces at different prices. First or last would
    misstate the cost basis, and tax and every P&L figure come from it."""
    got = settlement.settle(
        [_row()],
        client=FakeBroker([_order(filled=10)],
                          {"o1": [_trade(4, "100.00"), _trade(6, "110.00")]}))
    # (4*100 + 6*110) / 10 = 106.00
    assert got.fills[0].price == Decimal("106.00")


def test_charges_come_from_the_rate_card_when_one_is_given():
    got = settlement.settle([_row()],
                            client=FakeBroker([_order()],
                                              {"o1": [_trade(10, "100.00")]}),
                            charge_for=_charges)
    assert got.fills[0].charges == Decimal("15.34")


def test_without_a_rate_card_charges_are_zero_rather_than_guessed():
    got = settlement.settle([_row()],
                            client=FakeBroker([_order()],
                                              {"o1": [_trade(10, "100.00")]}))
    assert got.fills[0].charges == Decimal("0.00")


# -------------------------------------------------------------- partial ---

def test_a_partly_filled_order_settles_as_partial():
    """Neither filled nor rejected: the strategy asked for a position it did
    not get, which the next rebalance has to see."""
    got = settlement.settle(
        [_row()],
        client=FakeBroker([_order(status="CANCELLED", quantity=10, filled=4)],
                          {"o1": [_trade(4, "100.00")]}))
    assert got.states["t1"] == PARTIAL
    assert got.fills[0].quantity == 4


# ------------------------------------------------------------- rejected ---

def test_a_rejected_order_becomes_a_skip_with_the_brokers_reason():
    """A zero-quantity execution would be a trade that never happened; a skip
    is what actually occurred and carries why."""
    got = settlement.settle(
        [_row()],
        client=FakeBroker([_order(status="REJECTED", filled=0,
                                  message="Insufficient funds")]))
    assert got.states["t1"] == REJECTED
    fill = got.fills[0]
    assert fill.is_skip and fill.skipped == "Insufficient funds"
    assert fill.quantity == 0


def test_a_cancelled_order_with_no_fill_is_a_skip():
    got = settlement.settle(
        [_row()], client=FakeBroker([_order(status="CANCELLED", filled=0)]))
    assert got.states["t1"] == CANCELLED
    assert got.fills[0].is_skip


# --------------------------------------------------------------- pending ---

def test_an_open_order_is_pending_not_settled():
    """No final quantity or price yet. Recording one would write a fill that
    later changes, and executions are never patched in place."""
    got = settlement.settle([_row()],
                            client=FakeBroker([_order(status="OPEN", filled=0)]))
    assert got.fills == []
    assert "open at the broker" in got.pending["t1"]
    assert not got.complete


def test_a_submission_absent_from_the_order_book_is_pending():
    got = settlement.settle([_row()], client=FakeBroker([]))
    assert got.pending["t1"] == "not in the order book yet"


def test_orders_without_a_tag_are_ignored():
    """Someone else's order must never be matched to our instruction."""
    got = settlement.settle([_row()],
                            client=FakeBroker([_order(tag="")]))
    assert got.fills == []
    assert "t1" in got.pending


# ---------------------------------------------------------- unreachable ---

def test_an_unreachable_broker_is_reported_not_read_as_nothing_filled():
    """An unreachable broker and an empty order book look identical here and
    mean opposite things."""
    got = settlement.settle([_row()], client=FakeBroker([], boom=True))
    assert got.unreachable
    assert got.fills == [] and got.states == {}
    assert not got.complete


# ------------------------------------------------------------- multiple ---

def test_each_submission_is_settled_against_its_own_order():
    got = settlement.settle(
        [_row("t1", 1), _row("t2", 2)],
        client=FakeBroker(
            [_order(tag="t1", order_id="o1"),
             _order(tag="t2", order_id="o2", side="sell")],
            {"o1": [_trade(10, "100.00")],
             "o2": [_trade(10, "200.00", order_id="o2", tid="tr2")]}))
    assert {f.seq: f.price for f in got.fills} == {
        1: Decimal("100.00"), 2: Decimal("200.00")}


def test_a_settlement_with_nothing_outstanding_is_complete():
    got = settlement.settle(
        [_row()], client=FakeBroker([_order()], {"o1": [_trade(10, "100.00")]}))
    assert got.complete
