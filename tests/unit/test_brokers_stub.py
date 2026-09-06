"""Tests for the stub broker (design §12).

The stub's own correctness matters more than it looks: every rehearsal
conclusion rests on it. If the stub silently filled orders it should have
rejected, a rehearsal would report success for a path that fails in production.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from algotrade.brokers import base
from algotrade.brokers.stub import StubBroker


def _buy(quantity: int = 10, tag: str = "set:1") -> base.OrderRequest:
    return base.OrderRequest(symbol="RELIANCE", side="buy", quantity=quantity,
                             order_type=base.LIMIT, tag=tag,
                             price=Decimal("2402.75"))


def test_the_stub_satisfies_the_broker_protocol():
    """If this fails, the live client and the stub have diverged."""
    assert isinstance(StubBroker(), base.BrokerClient)


# -------------------------------------------------------------- filling ---

def test_an_order_fills_completely_at_the_requested_price():
    broker = StubBroker()
    order_id = broker.place(_buy())

    order = broker.orders(tag="set:1")[0]
    assert order.status == "COMPLETE"
    assert order.filled_quantity == 10
    assert broker.trades(order_id=order_id)[0].price == Decimal("2402.75")


def test_fills_carry_no_slippage():
    """The shadow's whole value is being a zero-slippage control. A stub that
    invented slippage would make rehearsal numbers look real and be fiction."""
    broker = StubBroker()
    order_id = broker.place(_buy())
    assert broker.trades(order_id=order_id)[0].price == Decimal("2402.75")


def test_order_ids_are_unique_per_order():
    broker = StubBroker()
    first = broker.place(_buy(tag="set:1"))
    second = broker.place(_buy(tag="set:2"))
    assert first != second


# ------------------------------------------------------------ rejecting ---

def test_an_armed_rejection_fires_once_and_only_once():
    broker = StubBroker()
    broker.fail_next("margin")

    broker.place(_buy(tag="set:1"))
    broker.place(_buy(tag="set:2"))

    rejected = broker.orders(tag="set:1")[0]
    accepted = broker.orders(tag="set:2")[0]
    assert rejected.status == "REJECTED"
    assert rejected.filled_quantity == 0
    assert accepted.status == "COMPLETE"


def test_a_rejected_order_produces_no_trades():
    """A rejection that left a trade behind would credit shares never bought."""
    broker = StubBroker()
    broker.fail_next("surveillance")
    order_id = broker.place(_buy())
    assert broker.trades(order_id=order_id) == []


def test_a_rejection_carries_the_brokers_reason():
    broker = StubBroker()
    broker.fail_next("freeze")
    broker.place(_buy())
    assert "freeze limit" in broker.orders()[0].status_message


def test_an_unknown_rejection_reason_is_refused():
    """Typos must not silently arm nothing and pass the rehearsal."""
    with pytest.raises(ValueError, match="unknown rejection"):
        StubBroker().fail_next("nonsense")


# -------------------------------------------------------------- partial ---

def test_a_partial_fill_is_terminal_but_incomplete():
    broker = StubBroker()
    broker.partially_fill_next(4)
    order_id = broker.place(_buy(quantity=10))

    order = broker.orders()[0]
    assert order.terminal and order.partial
    assert order.filled_quantity == 4
    assert broker.trades(order_id=order_id)[0].quantity == 4


def test_a_partial_fill_arming_does_not_persist():
    broker = StubBroker()
    broker.partially_fill_next(4)
    broker.place(_buy(quantity=10, tag="set:1"))
    broker.place(_buy(quantity=10, tag="set:2"))
    assert broker.orders(tag="set:2")[0].filled_quantity == 10


# --------------------------------------------------------------- reads ---

def test_orders_can_be_looked_up_by_tag():
    """The tag is the idempotency join key -- matching on symbol and quantity
    would be ambiguous when the same name is traded twice."""
    broker = StubBroker()
    broker.place(_buy(tag="set:1"))
    broker.place(_buy(tag="set:2"))
    assert len(broker.orders(tag="set:1")) == 1
    assert len(broker.orders()) == 2


def test_margins_and_holdings_report_what_they_were_given():
    broker = StubBroker(
        funds=base.BrokerFunds(available=Decimal("47000.00"),
                               used=Decimal("3000.00")),
        demat=[base.BrokerHolding(symbol="HFCL", isin="INE548A01028",
                                  quantity=30,
                                  average_price=Decimal("224.17"))],
    )
    assert broker.margins().available == Decimal("47000.00")
    assert broker.holdings()[0].symbol == "HFCL"


# ------------------------------------------------------------- mirroring ---

def _holding(symbol="HFCL", isin="INE548A01028", lots=((20, "220.00"), (10, "230.00"))):
    from datetime import date

    from algotrade.domain.models import Holding, Lot
    return Holding(portfolio_id="p", symbol=symbol, isin=isin,
                   lots=[Lot(lot_id=f"{symbol}:{i}", quantity=q,
                             price=Decimal(p), acquired_on=date(2026, 9, 1))
                         for i, (q, p) in enumerate(lots, 1)])


def test_a_mirroring_stub_holds_what_the_book_holds():
    """Gives the reconciliation two sides, so a deliberate mismatch can be
    introduced against a baseline that agrees."""
    from algotrade.brokers.stub import mirroring

    held = mirroring([_holding()]).holdings()
    assert [(h.symbol, h.quantity) for h in held] == [("HFCL", 30)]


def test_the_mirrored_average_is_weighted_across_lots():
    """What a broker reports. Taking one lot's price would misstate it."""
    from algotrade.brokers.stub import mirroring

    # (20*220 + 10*230) / 30 = 223.33
    assert mirroring([_holding()]).holdings()[0].average_price == Decimal("223.33")


def test_the_isin_carries_through_so_matching_still_works_on_it():
    from algotrade.brokers.stub import mirroring

    assert mirroring([_holding()]).holdings()[0].isin == "INE548A01028"


def test_a_closed_position_is_not_mirrored():
    from algotrade.brokers.stub import mirroring

    assert mirroring([_holding(lots=())]).holdings() == []


def test_mirroring_does_not_invent_funds():
    """Cash at a broker is not derivable from positions, and a made-up
    balance would let a rehearsal size a basket the real account could never
    afford."""
    from algotrade.brokers.stub import mirroring

    assert mirroring([_holding()]).margins().available == Decimal("0")


def test_the_universe_seeds_the_instrument_dump():
    from algotrade.brokers.stub import mirroring

    rows = [{"tradingsymbol": "HFCL", "exchange": "NSE"}]
    assert mirroring([], universe=rows).instruments() == rows


def test_an_unseeded_stub_maps_nothing_rather_than_inventing_a_universe():
    """An invented universe would let build_symbol_map appear to work against
    symbols that do not exist."""
    assert StubBroker().instruments() == []
