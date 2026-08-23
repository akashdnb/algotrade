"""Tests for the rebalance decision.

The rank buffer is the behaviour that matters most here. Implementing the
target as "the top 30" instead of "keep until 60, refill from 30" roughly
doubles turnover, and §11j measured that class of error costing 2.6pp of CAGR.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from algotrade.domain import money
from algotrade.domain.models import Action, Holding, Lot, SignalRow, SignalSet
from algotrade.services.rebalance import RebalanceService

D = date
R = money.rupees


def _signals(symbols: list[str], as_of=D(2026, 9, 30)) -> SignalSet:
    return SignalSet(
        strategy_version="momentum_v1", date=as_of,
        rows=[SignalRow(rank=i + 1, symbol=s, feature=1.0 - i * 0.01)
              for i, s in enumerate(symbols)],
    )


def _holding(symbol: str, qty: int, price: str = "100.00") -> Holding:
    return Holding(portfolio_id="p", symbol=symbol,
                   lots=[Lot(f"L-{symbol}", D(2026, 1, 1), qty, R(price))])


def _service(entry=3, exit_=6) -> RebalanceService:
    return RebalanceService(entry=entry, exit_=exit_)


# ------------------------------------------------------------- membership ---


def test_the_first_rebalance_buys_the_top_entry_names():
    target, entering, leaving = _service().target_membership(
        held=[], signals=_signals(["A", "B", "C", "D", "E"]))
    assert target == ["A", "B", "C"]
    assert entering == ["A", "B", "C"]
    assert leaving == []


def test_a_held_name_between_entry_and_exit_is_KEPT_not_sold():
    """The buffer. 'A' has fallen to rank 5 -- outside the top 3 but inside the
    exit threshold of 6, so it stays. Selling it here is the expensive bug."""
    target, entering, leaving = _service().target_membership(
        held=["A"], signals=_signals(["B", "C", "D", "E", "A", "F"]))
    assert "A" in target
    assert leaving == []


def test_a_held_name_past_the_exit_threshold_is_sold():
    target, entering, leaving = _service().target_membership(
        held=["A"], signals=_signals(["B", "C", "D", "E", "F", "G", "A"]))
    assert "A" not in target
    assert leaving == ["A"]


def test_a_held_name_absent_from_the_ranking_entirely_is_sold():
    """Delisted, or dropped out of the liquid universe. Treated as past exit
    rather than silently retained forever."""
    target, entering, leaving = _service().target_membership(
        held=["GONE"], signals=_signals(["A", "B", "C"]))
    assert leaving == ["GONE"]
    assert "GONE" not in target


def test_kept_names_take_priority_over_new_entrants():
    """The book holds `entry` names. A kept name occupies a slot, so refills
    only fill what is left -- otherwise the book would exceed its size."""
    target, entering, leaving = _service().target_membership(
        held=["X"], signals=_signals(["A", "B", "C", "X", "D"]))
    assert len(target) == 3
    assert "X" in target
    assert target == ["A", "B", "X"] or target == ["X", "A", "B"]


def test_the_book_never_exceeds_the_entry_count():
    held = [f"H{i}" for i in range(5)]
    ranking = held + [f"N{i}" for i in range(5)]
    target, _, _ = _service(entry=3, exit_=6).target_membership(
        held=held, signals=_signals(ranking))
    assert len(target) == 3


def test_exit_below_entry_is_rejected_at_construction():
    with pytest.raises(ValueError, match="must be >="):
        RebalanceService(entry=30, exit_=10)


def test_equal_exit_and_entry_is_a_hard_cut_with_no_buffer():
    target, _, leaving = RebalanceService(entry=3, exit_=3).target_membership(
        held=["A"], signals=_signals(["B", "C", "D", "A"]))
    assert leaving == ["A"]


# ----------------------------------------------------------------- sizing ---


def _prices(symbols, price="100.00") -> dict:
    return {s: R(price) for s in symbols}


def test_the_first_rebalance_from_cash_sizes_equally():
    decision = _service().decide(
        holdings=[], signals=_signals(["A", "B", "C"]),
        cash=R("30000.00"), prices=_prices(["A", "B", "C"]),
        as_of=D(2026, 9, 30),
    )
    assert set(decision.target_quantities) == {"A", "B", "C"}
    # 30,000 less a 30 bps reserve = 29,910 investable; a third each = 9,970,
    # which floors to 99 shares (29,700). The 210 left over closes the rounding
    # gap by buying two more shares, largest fractional remainder first.
    assert sorted(decision.target_quantities.values()) == [99, 100, 100]
    assert decision.charge_reserve == R("90.00")
    assert decision.deployed == R("29900.00")
    assert decision.deployed <= decision.investable


def test_a_charge_reserve_is_withheld_so_the_last_buy_fits():
    decision = _service().decide(
        holdings=[], signals=_signals(["A"]), cash=R("10000.00"),
        prices=_prices(["A"]), as_of=D(2026, 9, 30),
    )
    assert decision.charge_reserve == R("30.00")
    assert decision.investable == R("9970.00")
    assert decision.deployed <= decision.investable


def test_the_whole_book_is_investable_not_just_the_cash():
    """A rebalance can redeploy what is already held; treating only cash as
    investable would shrink the book to nothing over time."""
    decision = _service().decide(
        holdings=[_holding("A", 100)], signals=_signals(["A", "B", "C"]),
        cash=R("0.00"), prices=_prices(["A", "B", "C"]),
        as_of=D(2026, 9, 30),
    )
    # 100 shares at 100 = 10,000 of book value, redeployed across three names.
    assert decision.investable == R("9970.00")
    assert len(decision.target_quantities) == 3


def test_cash_required_is_negative_when_sales_more_than_fund_the_buys():
    decision = _service().decide(
        holdings=[_holding("OLD", 100)], signals=_signals(["OLD"]),
        cash=R("0.00"), prices=_prices(["OLD"]),
        as_of=D(2026, 9, 30),
    )
    # Nothing changes hands, so nothing extra is needed.
    assert decision.cash_required <= money.ZERO


def test_a_name_with_no_price_is_dropped_and_reported():
    decision = _service().decide(
        holdings=[], signals=_signals(["A", "B"]), cash=R("20000.00"),
        prices={"A": R("100.00")}, as_of=D(2026, 9, 30),
    )
    assert "B" in decision.dropped
    assert "price" in decision.dropped["B"].lower()


def test_rounding_drag_is_reported():
    decision = _service().decide(
        holdings=[], signals=_signals(["A"]), cash=R("1000.00"),
        prices={"A": R("300.00")}, as_of=D(2026, 9, 30),
    )
    assert decision.rounding_drag > 0


def test_an_unchanged_book_is_a_noop():
    decision = _service().decide(
        holdings=[_holding("A", 99), _holding("B", 99), _holding("C", 99)],
        signals=_signals(["A", "B", "C"]), cash=R("0.00"),
        prices=_prices(["A", "B", "C"]), as_of=D(2026, 9, 30),
    )
    assert decision.is_noop
    assert decision.entering == [] and decision.leaving == []


# ------------------------------------------------------------------ plan ----


def test_the_plan_sells_before_it_buys():
    service = _service()
    holdings = [_holding("OLD", 100)]
    decision = service.decide(
        holdings=holdings, signals=_signals(["NEW", "OLD2", "OLD3", "OLD4",
                                             "OLD5", "OLD6", "OLD"]),
        cash=R("0.00"),
        prices=_prices(["OLD", "NEW", "OLD2", "OLD3", "OLD4", "OLD5", "OLD6"]),
        as_of=D(2026, 9, 30),
    )
    plan = service.plan(holdings=holdings, decision=decision)
    actions = [i.action for i in plan.items]
    assert actions[0] == Action.SELL
    assert Action.BUY in actions
    assert actions.index(Action.SELL) < actions.index(Action.BUY)


def test_plan_items_carry_the_rank_that_justified_them():
    service = _service()
    holdings = [_holding("OUT", 100)]
    decision = service.decide(
        holdings=holdings,
        signals=_signals(["A", "B", "C", "D", "E", "F", "OUT"]),
        cash=R("0.00"),
        prices=_prices(["OUT", "A", "B", "C", "D", "E", "F"]),
        as_of=D(2026, 9, 30),
    )
    plan = service.plan(holdings=holdings, decision=decision)
    sell = next(i for i in plan.items if i.symbol == "OUT")
    assert "7" in sell.reason and "6" in sell.reason


def test_a_noop_decision_produces_an_empty_plan():
    service = _service()
    holdings = [_holding("A", 99), _holding("B", 99), _holding("C", 99)]
    decision = service.decide(
        holdings=holdings, signals=_signals(["A", "B", "C"]),
        cash=R("0.00"), prices=_prices(["A", "B", "C"]), as_of=D(2026, 9, 30),
    )
    assert service.plan(holdings=holdings, decision=decision).items == []
