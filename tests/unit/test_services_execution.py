"""Tests for execution recording.

The property under test throughout: a recommendation is INTENT, executions are
TRUTH, and the rollup between them is checked mechanically rather than trusted.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from algotrade.domain import money
from algotrade.domain.models import (
    Action,
    Item,
    ItemStatus,
    RecommendationSet,
    SetStatus,
)
from algotrade.services.execution import (
    ExecutionRejected,
    ExecutionService,
    FillRequest,
)
from tests.unit.fakes import FakeAudit, FakeCash, FakeExecutions, FakeRecommendations

D = date
R = money.rupees
NOW = datetime(2026, 10, 1, 4, 0, tzinfo=timezone.utc)
PID = "paper_momentum_v1_001"
SET = f"{PID}:2026-09:r1"


def _item(seq, action, symbol, qty, price="100.00") -> Item:
    return Item(seq=seq, action=action, symbol=symbol, quantity=qty,
                reference_price=R(price))


def _rec(items) -> RecommendationSet:
    return RecommendationSet(
        set_id=SET, portfolio_id=PID, rebalance_run_id=f"{PID}:2026-09",
        period="2026-09", as_of_session=D(2026, 9, 30), items=items,
        status=SetStatus.PENDING,
    )


def _service(items=None):
    items = items or [_item(1, Action.BUY, "RELIANCE", 500, "2400.00")]
    recs, ex, cash, audit = (
        FakeRecommendations(), FakeExecutions(), FakeCash(), FakeAudit())
    recs.sets[SET] = _rec(items)
    service = ExecutionService(
        executions=ex, recommendations=recs, cash=cash, audit=audit)
    return service, recs, ex, cash, audit


# ------------------------------------------------------- partial fills ------


def test_one_instruction_filled_by_two_executions_aggregates():
    """The worked example: BUY 500 RELIANCE, filled 300 then 200."""
    service, recs, ex, _, _ = _service()

    service.record(set_id=SET, fills=[
        FillRequest(seq=1, quantity=300, price=R("2402.00"),
                    broker_order_id="A1")], now=NOW)
    service.record(set_id=SET, fills=[
        FillRequest(seq=1, quantity=200, price=R("2409.00"),
                    broker_order_id="A2")], now=NOW + timedelta(days=1))

    fills = ex.for_item(SET, 1)
    assert [f.quantity for f in fills] == [300, 200]

    item = recs.get_set(SET).items[0]
    assert item.executed_quantity == 500
    assert item.status == ItemStatus.EXECUTED
    # Volume-weighted: (300*2402 + 200*2409) / 500
    assert item.executed_price == R("2404.80")


def test_a_partial_fill_leaves_the_item_partial():
    service, recs, _, _, _ = _service()
    service.record(set_id=SET, fills=[
        FillRequest(seq=1, quantity=300, price=R("2402.00"))], now=NOW)
    item = recs.get_set(SET).items[0]
    assert item.status == ItemStatus.PARTIAL
    assert item.executed_quantity == 300


def test_over_filling_an_instruction_is_refused():
    """The book would no longer match any recommendation."""
    service, _, _, _, _ = _service()
    service.record(set_id=SET, fills=[
        FillRequest(seq=1, quantity=400, price=R("2402.00"))], now=NOW)
    with pytest.raises(ExecutionRejected, match="exceed"):
        service.record(set_id=SET, fills=[
            FillRequest(seq=1, quantity=200, price=R("2402.00"))], now=NOW)


def test_charges_are_summed_across_fills():
    service, recs, _, _, _ = _service()
    service.record(set_id=SET, fills=[
        FillRequest(seq=1, quantity=300, price=R("2402.00"), charges=R("30.00"))
    ], now=NOW)
    service.record(set_id=SET, fills=[
        FillRequest(seq=1, quantity=200, price=R("2409.00"), charges=R("20.00"))
    ], now=NOW)
    assert recs.get_set(SET).items[0].executed_charges == R("50.00")


# ------------------------------------------------------------- cash ---------


def test_a_buy_removes_value_plus_charges_from_cash():
    service, _, _, cash, _ = _service()
    service.record(set_id=SET, fills=[
        FillRequest(seq=1, quantity=100, price=R("2400.00"), charges=R("25.00"))
    ], now=NOW)
    # 100 x 2400 = 240,000 plus 25 charges, all outgoing.
    assert cash.balance(PID) == R("-240025.00")


def test_a_sell_adds_value_minus_charges_to_cash():
    service, _, _, cash, _ = _service(
        items=[_item(1, Action.SELL, "ADANIENT", 12, "2410.00")])
    service.record(set_id=SET, fills=[
        FillRequest(seq=1, quantity=12, price=R("2402.75"), charges=R("49.05"))
    ], now=NOW)
    # 12 x 2402.75 = 28,833.00 less 49.05
    assert cash.balance(PID) == R("28783.95")


def test_every_execution_writes_exactly_one_ledger_entry():
    service, _, ex, cash, _ = _service()
    service.record(set_id=SET, fills=[
        FillRequest(seq=1, quantity=300, price=R("2402.00"))], now=NOW)
    service.record(set_id=SET, fills=[
        FillRequest(seq=1, quantity=200, price=R("2409.00"))], now=NOW)
    assert len(cash.docs) == 2
    assert cash.execution_ids(PID) == {e.execution_id for e in ex.for_set(SET)}


# -------------------------------------------------------- idempotency -------


def test_the_same_broker_order_recorded_twice_is_a_no_op():
    """The realistic manual-entry mistake: re-submitting the same command."""
    service, recs, ex, cash, _ = _service()
    fill = FillRequest(seq=1, quantity=300, price=R("2402.00"),
                       broker_order_id="SAME")
    first = service.record(set_id=SET, fills=[fill], now=NOW)
    second = service.record(set_id=SET, fills=[fill], now=NOW)

    assert first.applied == 1
    assert second.applied == 0 and second.duplicates == [1]
    assert len(ex.for_item(SET, 1)) == 1
    assert len(cash.docs) == 1, "cash must not be double-counted"


def test_paper_fills_without_an_order_id_are_each_distinct():
    """Paper mode has no broker id; two genuine fills must both land."""
    service, _, ex, _, _ = _service()
    service.record(set_id=SET, fills=[
        FillRequest(seq=1, quantity=300, price=R("2402.00"))], now=NOW,
        mode="paper")
    service.record(set_id=SET, fills=[
        FillRequest(seq=1, quantity=200, price=R("2409.00"))], now=NOW,
        mode="paper")
    assert len(ex.for_item(SET, 1)) == 2


# ------------------------------------------------------------ skips ---------


def test_a_skip_records_its_reason_and_moves_no_cash():
    service, recs, ex, cash, audit = _service()
    result = service.record(set_id=SET, fills=[
        FillRequest(seq=1, skip="insufficient funds after T+1")], now=NOW)

    item = recs.get_set(SET).items[0]
    assert result.skipped == [1]
    assert item.status == ItemStatus.SKIPPED
    assert item.skip_reason == "insufficient funds after T+1"
    assert ex.for_set(SET) == []
    assert cash.docs == {}
    assert "recommendation.item.skipped" in audit.actions()


# ------------------------------------------------------- validation ---------


def test_a_fill_for_an_unknown_item_is_refused():
    service, _, _, _, _ = _service()
    with pytest.raises(ExecutionRejected, match="no item with seq"):
        service.record(set_id=SET, fills=[
            FillRequest(seq=99, quantity=1, price=R("1.00"))], now=NOW)


def test_a_fill_needs_a_positive_price_and_quantity():
    service, _, _, _, _ = _service()
    with pytest.raises(ExecutionRejected, match="quantity"):
        service.record(set_id=SET, fills=[
            FillRequest(seq=1, quantity=0, price=R("100.00"))], now=NOW)
    with pytest.raises(ExecutionRejected, match="price"):
        service.record(set_id=SET, fills=[
            FillRequest(seq=1, quantity=10, price=None)], now=NOW)


def test_an_unknown_set_raises():
    service, _, _, _, _ = _service()
    with pytest.raises(KeyError):
        service.record(set_id="nope", fills=[], now=NOW)


# ------------------------------------------------- rollup reconciliation ----


def test_a_clean_set_reconciles():
    service, _, _, _, _ = _service()
    service.record(set_id=SET, fills=[
        FillRequest(seq=1, quantity=500, price=R("2402.00"))], now=NOW)
    assert service.reconcile_rollups(SET) == []


def test_a_tampered_rollup_is_detected():
    """The rollup is a cache and can drift; the executions cannot."""
    service, recs, _, _, _ = _service()
    service.record(set_id=SET, fills=[
        FillRequest(seq=1, quantity=300, price=R("2402.00"))], now=NOW)

    recs.get_set(SET).items[0].executed_quantity = 500      # a lie
    problems = service.reconcile_rollups(SET)
    assert problems and "500" in problems[0] and "300" in problems[0]


def test_recording_returns_any_reconciliation_warning():
    service, recs, _, _, _ = _service()
    recs.get_set(SET).items[0].executed_quantity = 99       # pre-existing drift
    result = service.record(set_id=SET, fills=[
        FillRequest(seq=1, quantity=100, price=R("2402.00"))], now=NOW)
    assert result.warnings == []      # the refresh recomputed it from truth
    assert recs.get_set(SET).items[0].executed_quantity == 100


# ------------------------------------------------------------- audit --------


def test_every_execution_is_audited_with_its_slippage():
    service, _, _, _, audit = _service()
    service.record(set_id=SET, fills=[
        FillRequest(seq=1, quantity=500, price=R("2424.00"), note="filled in two lots")
    ], now=NOW)
    entry = next(e for e in audit.entries if e["action"] == "execution.recorded")
    assert entry["after"]["symbol"] == "RELIANCE"
    assert entry["after"]["slippage_bps"] is not None
    assert entry["after"]["note"] == "filled in two lots"


def test_a_buy_above_reference_reads_as_negative_slippage():
    service, _, ex, _, _ = _service()
    service.record(set_id=SET, fills=[
        FillRequest(seq=1, quantity=500, price=R("2424.00"))], now=NOW)
    assert ex.for_item(SET, 1)[0].slippage_bps == pytest.approx(-100.0)


# ----------------------------------------------- what date a fill happened on --
#
# With no explicit date the fill was stamped `rec.as_of_session` -- the SIGNAL's
# session, not the day it was filled. The convention is next_open, so you act
# the morning after the signal: every fill recorded through the documented
# workflow landed one day early, and at the 1 April boundary in the previous
# FINANCIAL YEAR. `ExecutionRepository.between` is the tax-year query.


def test_a_fill_is_dated_the_day_it_happened_not_the_signal_session():
    service, _, ex, _, _ = _service()
    # NOW is 2026-10-01 09:30 IST; the signal was for 2026-09-30.
    service.record(set_id=SET, now=NOW,
                   fills=[FillRequest(seq=1, quantity=500, price=R("2400.00"))])
    assert list(ex.dates.values())[0] == D(2026, 10, 1)


def test_an_explicit_execution_date_still_wins():
    """Backdating a fill entered late is exactly what the flag is for."""
    service, _, ex, _, _ = _service()
    service.record(set_id=SET, now=NOW, business_date=D(2026, 9, 28),
                   fills=[FillRequest(seq=1, quantity=500, price=R("2400.00"))])
    assert list(ex.dates.values())[0] == D(2026, 9, 28)


def test_a_fill_recorded_at_the_weekend_is_dated_to_the_previous_weekday():
    """NSE did not trade on a Saturday, so stamping one into the trade record
    would put a date in it on which no trade could have occurred."""
    service, _, ex, _, _ = _service()
    saturday = datetime(2026, 10, 3, 6, 0, tzinfo=timezone.utc)   # 11:30 IST Sat
    service.record(set_id=SET, now=saturday,
                   fills=[FillRequest(seq=1, quantity=500, price=R("2400.00"))])
    assert list(ex.dates.values())[0] == D(2026, 10, 2)
