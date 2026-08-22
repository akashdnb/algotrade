"""Tests for diffing the current book against a target book.

Ordering is load-bearing: sells must precede buys because Indian equity
settles T+1 and sale proceeds are not spendable the same day. A plan that
lists buys first is not executable.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from algotrade.domain import money, reconcile
from algotrade.domain.models import Action, Holding, ItemStatus, Lot

D = date
R = money.rupees


def _holding(symbol: str, qty: int, price: str = "100.00") -> Holding:
    return Holding(
        portfolio_id="p", symbol=symbol,
        lots=[Lot(lot_id=f"L-{symbol}", acquired_on=D(2026, 1, 1),
                  quantity=qty, price=R(price))],
    )


def test_a_new_name_becomes_a_buy():
    plan = reconcile.plan(
        holdings=[], target={"A": 10}, prices={"A": R("100.00")}
    )
    assert len(plan.items) == 1
    item = plan.items[0]
    assert (item.action, item.symbol, item.quantity) == (Action.BUY, "A", 10)
    assert item.status == ItemStatus.PENDING


def test_a_dropped_name_becomes_a_full_sell():
    plan = reconcile.plan(
        holdings=[_holding("A", 10)], target={}, prices={"A": R("100.00")}
    )
    assert len(plan.items) == 1
    assert (plan.items[0].action, plan.items[0].quantity) == (Action.SELL, 10)


def test_an_unchanged_position_produces_no_instruction():
    """The rank buffer exists so ordinary drift is NOT traded. Emitting a
    zero-delta item would charge for doing nothing."""
    plan = reconcile.plan(
        holdings=[_holding("A", 10)], target={"A": 10}, prices={"A": R("100.00")}
    )
    assert plan.items == []


def test_an_increase_buys_only_the_difference():
    plan = reconcile.plan(
        holdings=[_holding("A", 10)], target={"A": 15}, prices={"A": R("100.00")}
    )
    assert (plan.items[0].action, plan.items[0].quantity) == (Action.BUY, 5)


def test_a_decrease_sells_only_the_difference():
    plan = reconcile.plan(
        holdings=[_holding("A", 15)], target={"A": 10}, prices={"A": R("100.00")}
    )
    assert (plan.items[0].action, plan.items[0].quantity) == (Action.SELL, 5)


def test_sells_are_sequenced_before_buys_for_t_plus_one_settlement():
    plan = reconcile.plan(
        holdings=[_holding("OLD", 10)],
        target={"NEW": 10},
        prices={"OLD": R("100.00"), "NEW": R("100.00")},
    )
    actions = [i.action for i in plan.items]
    assert actions == [Action.SELL, Action.BUY]
    assert [i.seq for i in plan.items] == [1, 2]


def test_buys_are_ordered_by_descending_value_so_a_partial_fill_lands_closest_to_target():
    plan = reconcile.plan(
        holdings=[],
        target={"SMALL": 10, "BIG": 10},          # 1,000 vs 5,000; both clear the minimum
        prices={"SMALL": R("100.00"), "BIG": R("500.00")},
    )
    assert [i.symbol for i in plan.items] == ["BIG", "SMALL"]


def test_sells_are_ordered_by_descending_value_to_free_cash_fastest():
    plan = reconcile.plan(
        holdings=[_holding("SMALL", 10), _holding("BIG", 10, "500.00")],
        target={},
        prices={"SMALL": R("100.00"), "BIG": R("500.00")},
    )
    assert [i.symbol for i in plan.items] == ["BIG", "SMALL"]


def test_seq_is_dense_and_one_based_across_the_whole_plan():
    plan = reconcile.plan(
        holdings=[_holding("S1", 5), _holding("S2", 5)],
        target={"B1": 5, "B2": 5},
        prices={s: R("100.00") for s in ("S1", "S2", "B1", "B2")},
    )
    assert [i.seq for i in plan.items] == [1, 2, 3, 4]


def test_a_trade_below_the_minimum_value_is_not_emitted():
    """Consistent with construction and with engine.run's min_trade_value:
    a ₹200 top-up costs more in charges than it corrects."""
    plan = reconcile.plan(
        holdings=[_holding("A", 100)],
        target={"A": 102},
        prices={"A": R("100.00")},
        min_trade_value=R("500.00"),
    )
    assert plan.items == []
    assert "A" in plan.skipped


def test_a_sell_of_a_name_with_no_price_is_still_emitted():
    """A delisted or suspended name must still be surfaced -- it is exactly the
    position you need to know about. It is priced at zero for ordering only."""
    plan = reconcile.plan(
        holdings=[_holding("GONE", 10)], target={}, prices={}
    )
    assert len(plan.items) == 1
    assert plan.items[0].action == Action.SELL
    assert plan.items[0].reference_price == money.ZERO


def test_a_buy_of_a_name_with_no_price_is_refused_not_guessed():
    plan = reconcile.plan(holdings=[], target={"A": 10}, prices={})
    assert plan.items == []
    assert "price" in plan.skipped["A"].lower()


def test_turnover_is_traded_value_over_book_value():
    plan = reconcile.plan(
        holdings=[_holding("A", 10)],
        target={"A": 5},
        prices={"A": R("100.00")},
    )
    # sell 5 x 100 = 500 traded against a 1,000 book
    assert plan.turnover(book_value=R("1000.00")) == pytest.approx(0.50)


def test_turnover_of_an_empty_book_is_zero_not_an_error():
    plan = reconcile.plan(holdings=[], target={}, prices={})
    assert plan.turnover(book_value=money.ZERO) == 0.0


def test_reasons_are_attached_so_the_audit_trail_explains_every_trade():
    plan = reconcile.plan(
        holdings=[_holding("OUT", 10)],
        target={"IN": 10},
        prices={"OUT": R("100.00"), "IN": R("100.00")},
        ranks={"IN": 14, "OUT": 71},
        entry=30, exit_=60,
    )
    by_symbol = {i.symbol: i for i in plan.items}
    assert "71" in by_symbol["OUT"].reason and "60" in by_symbol["OUT"].reason
    assert "14" in by_symbol["IN"].reason and "30" in by_symbol["IN"].reason
