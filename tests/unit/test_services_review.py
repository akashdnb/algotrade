"""Tests for the pre-execution review (design §6).

Two properties carry the weight here:

* **membership comes from the recommendation**, reassembled from `holds` and
  the buy items, because neither half alone is the book;
* **an unpriced target blocks the basket.** Sizing around a name we could not
  price produces a 29-stock book from a 30-stock decision, and the screen looks
  entirely normal while it happens.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from algotrade.domain.models import (
    Action,
    Item,
    ItemStatus,
    RecommendationSet,
)
from algotrade.services import review

SET_ID = "paper_momentum_v1_001:2026-10:r1"


def _item(seq, action, symbol, quantity, price="100.00", charges="10.00",
          reason="") -> Item:
    return Item(seq=seq, action=action, symbol=symbol, quantity=quantity,
                reference_price=Decimal(price),
                estimated_charges=Decimal(charges), reason=reason)


def _set(items=(), holds=()) -> RecommendationSet:
    return RecommendationSet(
        set_id=SET_ID, portfolio_id="paper_momentum_v1_001",
        rebalance_run_id="r", period="2026-10",
        as_of_session=date(2026, 9, 30),
        items=list(items), holds=list(holds))


def _build(recommendation, items, **kwargs):
    args = dict(unpriced=[], dropped={}, skipped={},
                investable=Decimal("100000.00"),
                charge_reserve=Decimal("300.00"),
                deployed=Decimal("99000.00"),
                residual_cash=Decimal("1000.00"))
    args.update(kwargs)
    return review.build(recommendation=recommendation, items=items, **args)


# ----------------------------------------------------------- membership ---

def test_the_target_is_holds_plus_buys():
    """Neither half alone is the book: holds are deliberately not items,
    because a zero-delta HOLD would be counted as outstanding work."""
    rec = _set(items=[_item(1, Action.BUY, "NEW", 5)], holds=["KEPT"])
    assert review.target_membership(rec) == ["KEPT", "NEW"]


def test_sells_are_not_part_of_the_target():
    rec = _set(items=[_item(1, Action.SELL, "GONE", 5),
                      _item(2, Action.BUY, "NEW", 5)], holds=["KEPT"])
    assert review.target_membership(rec) == ["KEPT", "NEW"]


def test_a_symbol_in_both_holds_and_buys_appears_once():
    """A top-up buy on a retained name must not duplicate the position."""
    rec = _set(items=[_item(1, Action.BUY, "KEPT", 2)], holds=["KEPT"])
    assert review.target_membership(rec) == ["KEPT"]


def test_membership_order_is_stable():
    rec = _set(items=[_item(1, Action.BUY, "B", 1), _item(2, Action.BUY, "A", 1)],
               holds=["Z", "Y"])
    assert review.target_membership(rec) == ["Z", "Y", "B", "A"]


# ----------------------------------------------------------------- lines ---

def test_lines_carry_the_resized_quantity_and_the_approved_one():
    rec = _set(items=[_item(1, Action.BUY, "AAA", 10)])
    got = _build(rec, [_item(1, Action.BUY, "AAA", 9)])
    assert got.lines[0].quantity == 9
    assert got.lines[0].approved_quantity == 10
    assert got.lines[0].drift == -1


def test_an_instruction_absent_from_the_recommendation_is_new():
    """A gap can push a name over the minimum trade value that was under it at
    the close. The reviewer must see that it was not approved."""
    got = _build(_set(), [_item(1, Action.BUY, "AAA", 5)])
    assert got.lines[0].is_new
    assert got.lines[0].drift is None


def test_an_unchanged_quantity_does_not_count_as_drift():
    rec = _set(items=[_item(1, Action.BUY, "AAA", 10)])
    got = _build(rec, [_item(1, Action.BUY, "AAA", 10)])
    assert got.drifted == []


def test_a_buy_and_a_sell_of_the_same_symbol_are_matched_separately():
    """Matching on symbol alone would compare a sell against a buy's size."""
    rec = _set(items=[_item(1, Action.SELL, "AAA", 4),
                      _item(2, Action.BUY, "AAA", 9)])
    got = _build(rec, [_item(1, Action.SELL, "AAA", 4)])
    assert got.lines[0].approved_quantity == 4
    assert got.lines[0].drift == 0


def test_sells_and_buys_are_separable_for_the_two_day_protocol():
    got = _build(_set(), [_item(1, Action.SELL, "OLD", 5),
                          _item(2, Action.BUY, "NEW", 3)])
    assert [l.symbol for l in got.sells] == ["OLD"]
    assert [l.symbol for l in got.buys] == ["NEW"]


def test_line_value_is_price_times_quantity():
    got = _build(_set(), [_item(1, Action.BUY, "AAA", 3, price="224.17")])
    assert got.lines[0].value == Decimal("672.51")


# ---------------------------------------------------------------- totals ---

def test_charges_sum_across_every_instruction():
    got = _build(_set(), [_item(1, Action.SELL, "A", 1, charges="15.34"),
                          _item(2, Action.BUY, "B", 1, charges="10.00")])
    assert got.estimated_charges == Decimal("25.34")


def test_sell_and_buy_value_are_reported_separately():
    """Day 1 sells fund day 1 buys; one net figure would hide the constraint."""
    got = _build(_set(), [_item(1, Action.SELL, "A", 10, price="100.00"),
                          _item(2, Action.BUY, "B", 5, price="100.00")])
    assert got.sell_value == Decimal("1000.00")
    assert got.buy_value == Decimal("500.00")


# ----------------------------------------------------------------- ready ---

def test_a_fully_priced_basket_is_ready():
    assert _build(_set(), [_item(1, Action.BUY, "AAA", 5)]).ready


def test_an_unpriced_target_blocks_the_basket():
    """Sizing around it produces a 29-stock book from a 30-stock decision, and
    the screen looks entirely normal while it happens."""
    got = _build(_set(), [_item(1, Action.BUY, "AAA", 5)], unpriced=["BBB"])
    assert not got.ready
    assert got.unpriced == ["BBB"]


# --------------------------------------------------------------- blocked ---

def test_a_blocked_basket_carries_no_instructions_at_all():
    """The Saturday bug. With no prices the target book empties, every holding
    falls out of it, and the plan becomes a sale of the whole portfolio at a
    reference price of zero -- rendered on the screen read immediately before
    trading. `blocked` returns the refusal instead of computing that."""
    rec = _set(items=[_item(1, Action.BUY, "AAA", 5)], holds=["KEPT"])
    got = review.blocked(rec, unpriced=["AAA", "KEPT"])
    assert got.lines == []
    assert got.sells == [] and got.buys == []
    assert not got.ready


def test_a_blocked_basket_knows_how_many_names_were_intended():
    """So the page can say "none of 30", which reads very differently from a
    bare count of failures."""
    rec = _set(items=[_item(1, Action.BUY, "AAA", 5)], holds=["KEPT", "TOO"])
    got = review.blocked(rec, unpriced=["AAA", "KEPT", "TOO"])
    assert got.target_size == 3
    assert len(got.unpriced) == got.target_size


def test_a_blocked_basket_reports_zero_totals_not_stale_ones():
    rec = _set(items=[_item(1, Action.BUY, "AAA", 5)])
    got = review.blocked(rec, unpriced=["AAA"])
    assert got.sell_value == Decimal("0.00")
    assert got.buy_value == Decimal("0.00")
    assert got.estimated_charges == Decimal("0.00")


def test_a_built_basket_also_knows_its_target_size():
    rec = _set(items=[_item(1, Action.BUY, "AAA", 5)], holds=["KEPT"])
    assert _build(rec, [_item(1, Action.BUY, "AAA", 5)]).target_size == 2


def test_a_dropped_symbol_is_reported_but_does_not_block():
    """`construction.build` drops a name below the minimum trade value on
    purpose. That is a decision, not a failure to price."""
    got = _build(_set(), [_item(1, Action.BUY, "AAA", 5)],
                 dropped={"BBB": "target 300 below minimum trade value 500"})
    assert got.ready
    assert "BBB" in got.dropped


def test_unpriced_symbols_are_sorted_so_two_runs_compare():
    got = _build(_set(), [], unpriced=["ZEE", "ACC"])
    assert got.unpriced == ["ACC", "ZEE"]


def test_an_empty_basket_is_ready_and_says_nothing_to_do():
    got = _build(_set(), [])
    assert got.ready
    assert got.lines == []
    assert got.estimated_charges == Decimal("0.00")
