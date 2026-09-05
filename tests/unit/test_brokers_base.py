"""Tests for the broker value types (design §4).

`OrderRequest` validates in `__post_init__` because it is the last place we
control before a request becomes an irreversible instruction. The two rules
that exist for real reasons rather than tidiness: a LIMIT order without a price
is meaningless, and a MARKET order without market protection is *rejected by
Kite* -- so building one is a bug we should find in a test, not in a rejection
on rebalance morning.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from algotrade.brokers import base


def _limit(**kwargs):
    args = dict(symbol="RELIANCE", side="buy", quantity=3,
                order_type=base.LIMIT, tag="set:1", price=Decimal("2402.75"))
    args.update(kwargs)
    return base.OrderRequest(**args)


def _market(**kwargs):
    args = dict(symbol="RELIANCE", side="sell", quantity=3,
                order_type=base.MARKET, tag="set:1",
                market_protection=Decimal("3"))
    args.update(kwargs)
    return base.OrderRequest(**args)


# --------------------------------------------------------- OrderRequest ---

def test_a_valid_limit_order_is_accepted():
    req = _limit()
    assert req.price == Decimal("2402.75")
    assert req.order_type == base.LIMIT


def test_a_limit_order_without_a_price_is_refused():
    with pytest.raises(ValueError, match="needs a price"):
        _limit(price=None)


def test_a_market_order_without_protection_is_refused():
    """Kite rejects unprotected market orders, so we must not build one."""
    with pytest.raises(ValueError, match="market_protection"):
        _market(market_protection=None)


def test_a_market_order_with_zero_protection_is_refused():
    """Zero is the value Kite specifically treats as unprotected."""
    with pytest.raises(ValueError, match="market_protection"):
        _market(market_protection=Decimal("0"))


def test_an_unknown_side_is_refused():
    with pytest.raises(ValueError, match="buy or sell"):
        _limit(side="short")


def test_a_non_positive_quantity_is_refused():
    with pytest.raises(ValueError, match="quantity"):
        _limit(quantity=0)


# ----------------------------------------------------------- BrokerOrder ---

def _order(**kwargs):
    args = dict(order_id="251001000123456", symbol="RELIANCE", side="buy",
                quantity=10, filled_quantity=10, status="COMPLETE")
    args.update(kwargs)
    return base.BrokerOrder(**args)


def test_a_complete_order_is_terminal_and_not_partial():
    order = _order()
    assert order.terminal
    assert not order.partial


def test_an_open_order_is_not_terminal():
    assert not _order(status="OPEN", filled_quantity=0).terminal


def test_a_partly_filled_terminal_order_is_partial():
    """The case that must not read as success: cancelled after 4 of 10."""
    order = _order(status="CANCELLED", filled_quantity=4)
    assert order.terminal
    assert order.partial


def test_a_rejected_order_filled_nothing_and_is_not_partial():
    """Zero filled is a rejection, not a partial fill -- they are handled
    differently, so the distinction has to hold."""
    order = _order(status="REJECTED", filled_quantity=0)
    assert order.terminal
    assert not order.partial


# ------------------------------------------------------------ BrokerFunds ---

def test_total_funds_is_available_plus_used():
    funds = base.BrokerFunds(available=Decimal("47000.00"),
                             used=Decimal("3000.00"))
    assert funds.total == Decimal("50000.00")


# ---------------------------------------------------------- BrokerTrade ---

def test_a_trade_carries_the_price_actually_paid():
    trade = base.BrokerTrade(trade_id="t1", order_id="o1", symbol="RELIANCE",
                             quantity=3, price=Decimal("2404.10"),
                             filled_at=datetime(2026, 10, 1, 9, 20))
    assert trade.price == Decimal("2404.10")
