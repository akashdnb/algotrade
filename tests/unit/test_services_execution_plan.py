"""Tests for building an order and deciding whether it may be sent (§7, §8).

The gate is the interesting half. It encodes a rule about money, not about
table order: sells fund buys, and at T+1 part of the proceeds may not be
usable until tomorrow, so no buy is offered while a sell is still to place.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from algotrade.brokers.base import LIMIT, MARKET
from algotrade.services import submission
from algotrade.services.execution_plan import (
    NotReady,
    gate,
    order_for,
    remaining,
)

SET_ID = "paper_momentum_v1_001:2026-10:r1"
PROTECTION = Decimal("3")


def _line(seq=1, action="buy", symbol="NEW", quantity=3, price="224.17",
          order_type=None):
    line = {"seq": seq, "action": action, "symbol": symbol,
            "quantity": quantity, "price": Decimal(price)}
    if order_type:
        line["order_type"] = order_type
    return line


# ------------------------------------------------------------- order_for ---

def test_a_limit_order_carries_the_approved_price():
    """The whole purpose of freezing the basket: the number sent is the
    number approved, not a fresh quote."""
    req = order_for(_line(order_type=LIMIT), set_id=SET_ID,
                    market_protection=PROTECTION)
    assert req.order_type == LIMIT
    assert req.price == Decimal("224.17")
    assert req.market_protection is None


def test_a_market_order_carries_protection_and_no_price():
    """Kite rejects a market order whose protection is zero. Naming a price
    would imply a control the order does not have."""
    req = order_for(_line(action="sell", order_type=MARKET), set_id=SET_ID,
                    market_protection=PROTECTION)
    assert req.market_protection == PROTECTION
    assert req.price is None


def test_the_order_type_defaults_to_limit():
    """MARKET is the deliberate choice for a sell leg; defaulting to it would
    make an omission the riskier option."""
    assert order_for(_line(), set_id=SET_ID,
                     market_protection=PROTECTION).order_type == LIMIT


def test_the_tag_matches_what_submission_would_compute():
    """Order identity comes from (set_id, seq); if these disagreed, the
    recovery query would look for an order under the wrong name."""
    req = order_for(_line(seq=7), set_id=SET_ID, market_protection=PROTECTION)
    assert req.tag == submission.tag_for(SET_ID, 7)


def test_an_unknown_order_type_is_refused():
    with pytest.raises(NotReady, match="unknown order type"):
        order_for(_line(order_type="ICEBERG"), set_id=SET_ID,
                  market_protection=PROTECTION)


# ------------------------------------------------------------------ gate ---

SELLS_AND_BUYS = [
    _line(seq=1, action="sell", symbol="OLD", quantity=5),
    _line(seq=2, action="sell", symbol="GONE", quantity=2),
    _line(seq=3, action="buy", symbol="NEW", quantity=3),
]


def test_a_sell_may_go_first():
    got = gate(SELLS_AND_BUYS[0], states={}, lines=SELLS_AND_BUYS)
    assert got.allowed


def test_a_buy_waits_while_any_sell_is_unsent():
    """Proceeds fund the buys, and at T+1 part of them is not usable until
    tomorrow. This is a rule about money, not about table order."""
    got = gate(SELLS_AND_BUYS[2], states={}, lines=SELLS_AND_BUYS)
    assert not got.allowed
    assert "2 sells not settled" in got.reason


def test_a_submitted_sell_does_not_release_the_buys():
    """The bug a rehearsal found on 2026-09-06. `submitted` means the broker
    accepted the order, not that it filled -- and until it fills the proceeds
    do not exist. Releasing on it placed a purchase its funding sale had not
    made."""
    states = {1: submission.SUBMITTED, 2: submission.SUBMITTED}
    got = gate(SELLS_AND_BUYS[2], states=states, lines=SELLS_AND_BUYS)
    assert not got.allowed
    assert "only been sent" in got.reason


def test_a_buy_is_released_once_every_sell_has_settled():
    states = {1: submission.FILLED, 2: submission.FILLED}
    assert gate(SELLS_AND_BUYS[2], states=states, lines=SELLS_AND_BUYS).allowed


def test_a_partly_filled_sell_releases_the_buys():
    """It is terminal and the money that arrived is what there is. Waiting
    for the rest would wait forever."""
    states = {1: submission.PARTIAL, 2: submission.FILLED}
    assert gate(SELLS_AND_BUYS[2], states=states, lines=SELLS_AND_BUYS).allowed


def test_a_rejected_sell_does_not_block_the_buys():
    """It is terminal. The money simply did not arrive, and the affordability
    recheck is what handles that -- not an indefinite block."""
    states = {1: submission.REJECTED, 2: submission.REJECTED}
    assert gate(SELLS_AND_BUYS[2], states=states, lines=SELLS_AND_BUYS).allowed


def test_an_already_sent_instruction_is_not_offered_again():
    """submission.submit would refuse anyway, but offering the button implies
    it would do something."""
    states = {1: submission.SUBMITTED}
    got = gate(SELLS_AND_BUYS[0], states=states, lines=SELLS_AND_BUYS)
    assert not got.allowed and got.reason == "already sent"


def test_an_instruction_stuck_at_intent_says_so_rather_than_inviting_a_resend():
    """`intent` means we may have sent it. The remedy is to confirm, not to
    press again."""
    got = gate(SELLS_AND_BUYS[0], states={1: submission.INTENT},
               lines=SELLS_AND_BUYS)
    assert not got.allowed
    assert "in flight" in got.reason


def test_a_basket_of_only_buys_has_nothing_to_wait_for():
    only_buys = [_line(seq=1, action="buy"), _line(seq=2, action="buy")]
    assert gate(only_buys[0], states={}, lines=only_buys).allowed


def test_one_pending_sell_is_reported_in_the_singular():
    lines = [_line(seq=1, action="sell"), _line(seq=2, action="buy")]
    got = gate(lines[1], states={}, lines=lines)
    assert "1 sell not settled" in got.reason


# ------------------------------------------------------------- remaining ---

def test_remaining_counts_what_is_still_to_send():
    assert remaining(SELLS_AND_BUYS, {}) == 3
    assert remaining(SELLS_AND_BUYS, {1: submission.SUBMITTED}) == 2


def test_remaining_treats_every_terminal_state_as_done():
    states = {1: submission.REJECTED, 2: submission.CANCELLED,
              3: submission.FILLED}
    assert remaining(SELLS_AND_BUYS, states) == 0
