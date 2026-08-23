"""Tests for rebuilding holdings from execution history.

The inversion under test: holdings are a DERIVED view and executions are
authoritative. A rebuild must be a pure function of the fills, so running it
twice produces the same book, and `verify` must detect a stored book that has
drifted from the trade record.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from algotrade.domain import money
from algotrade.domain.models import Action, Execution, Holding, Lot, TaxStatus
from algotrade.services.holdings import HoldingsService
from tests.unit.fakes import FakeAudit, FakeExecutions, FakeHoldings

D = date
R = money.rupees
PID = "paper_momentum_v1_001"
BASE = datetime(2026, 1, 2, 4, 0, tzinfo=timezone.utc)


def _ex(n, symbol, action, qty, price, day_offset=0, charges="0.00") -> Execution:
    return Execution(
        execution_id=f"set:1:{n}", portfolio_id=PID, set_id="set",
        item_seq=n, symbol=symbol, action=action, quantity=qty,
        price=R(price), charges=R(charges),
        executed_at=BASE + timedelta(days=day_offset),
    )


def _service():
    ex, holdings, audit = FakeExecutions(), FakeHoldings(), FakeAudit()
    return HoldingsService(executions=ex, holdings=holdings, audit=audit), ex, holdings, audit


def _load(ex, executions):
    for e in executions:
        ex.record(e, business_date=e.executed_at.date())


# ------------------------------------------------------------- derivation ---


def test_an_empty_history_produces_an_empty_book():
    service, _, _, _ = _service()
    result = service.derive([], portfolio_id=PID)
    assert result.holdings == []
    assert result.realised_pnl == money.ZERO


def test_buys_accumulate_into_lots():
    service, _, _, _ = _service()
    result = service.derive([
        _ex(1, "TCS", Action.BUY, 30, "975.10"),
        _ex(2, "TCS", Action.BUY, 17, "992.55", day_offset=150),
    ], portfolio_id=PID)

    (holding,) = result.holdings
    assert holding.quantity == 47
    assert len(holding.lots) == 2
    assert holding.cost_basis == R("46126.35")


def test_lot_ids_are_derived_from_executions_so_rebuilds_are_identical():
    """An unstable lot id would break reproducibility of every downstream
    tax calculation."""
    service, _, _, _ = _service()
    executions = [_ex(1, "TCS", Action.BUY, 30, "975.10")]
    first = service.derive(executions, portfolio_id=PID)
    second = service.derive(executions, portfolio_id=PID)
    assert [l.lot_id for l in first.holdings[0].lots] == \
           [l.lot_id for l in second.holdings[0].lots] == ["set:1:1"]


def test_a_sell_consumes_the_oldest_lot_first():
    service, _, _, _ = _service()
    result = service.derive([
        _ex(1, "TCS", Action.BUY, 10, "100.00"),
        _ex(2, "TCS", Action.BUY, 10, "200.00", day_offset=100),
        _ex(3, "TCS", Action.SELL, 10, "300.00", day_offset=200),
    ], portfolio_id=PID)

    (holding,) = result.holdings
    assert holding.quantity == 10
    # The 100-cost lot went first, so 10 x (300 - 100) = 2,000 realised.
    assert result.realised_pnl == R("2000.00")
    assert holding.lots[0].price == R("200.00")


def test_selling_out_removes_the_position_entirely():
    service, _, _, _ = _service()
    result = service.derive([
        _ex(1, "TCS", Action.BUY, 10, "100.00"),
        _ex(2, "TCS", Action.SELL, 10, "110.00", day_offset=10),
    ], portfolio_id=PID)
    assert result.holdings == []
    assert result.realised_pnl == R("100.00")


def test_realised_pnl_is_net_of_charges_on_both_sides():
    service, _, _, _ = _service()
    result = service.derive([
        _ex(1, "TCS", Action.BUY, 10, "100.00", charges="15.00"),
        _ex(2, "TCS", Action.SELL, 10, "110.00", day_offset=10, charges="20.00"),
    ], portfolio_id=PID)
    # 1,100 proceeds - (1,000 + 15 basis) - 20 sell charges
    assert result.realised_pnl == R("65.00")


def test_execution_order_is_stable_when_timestamps_tie():
    """Two fills can share a timestamp; an unstable order would produce
    different lot ids run to run."""
    service, _, _, _ = _service()
    a = _ex(1, "TCS", Action.BUY, 10, "100.00")
    b = _ex(2, "TCS", Action.BUY, 10, "200.00")     # same timestamp
    forward = service.derive([a, b], portfolio_id=PID)
    reverse = service.derive([b, a], portfolio_id=PID)
    assert [l.lot_id for l in forward.holdings[0].lots] == \
           [l.lot_id for l in reverse.holdings[0].lots]


def test_an_oversell_is_surfaced_rather_than_clamped():
    """A consistent history cannot produce this, so it must not pass quietly."""
    service, _, _, _ = _service()
    result = service.derive([
        _ex(1, "TCS", Action.BUY, 5, "100.00"),
        _ex(2, "TCS", Action.SELL, 10, "110.00", day_offset=1),
    ], portfolio_id=PID)
    assert result.anomalies and "cannot sell 10" in result.anomalies[0]
    assert result.holdings[0].quantity == 5, "the book is left untouched"


def test_tax_lots_survive_the_rebuild():
    service, _, _, _ = _service()
    result = service.derive([
        _ex(1, "TCS", Action.BUY, 10, "100.00"),                    # 2 Jan 2026
        _ex(2, "TCS", Action.BUY, 10, "200.00", day_offset=400),    # 2027
    ], portfolio_id=PID)
    (holding,) = result.holdings
    assert holding.tax_status(D(2027, 6, 1)) == TaxStatus.LONG_TERM
    assert holding.lots[1].tax_status(D(2027, 6, 1)) == TaxStatus.SHORT_TERM


# ---------------------------------------------------------------- rebuild ---


def test_rebuild_materialises_the_derived_book():
    service, ex, store, audit = _service()
    _load(ex, [_ex(1, "TCS", Action.BUY, 10, "100.00"),
               _ex(2, "INFY", Action.BUY, 5, "200.00")])

    result = service.rebuild(PID, now=BASE)
    assert result.symbols == ["INFY", "TCS"]
    assert {h.symbol for h in store.book(PID)} == {"INFY", "TCS"}
    assert "holdings.rebuilt" in audit.actions()


def test_rebuild_is_idempotent():
    service, ex, store, _ = _service()
    _load(ex, [_ex(1, "TCS", Action.BUY, 10, "100.00")])
    first = service.rebuild(PID, now=BASE)
    second = service.rebuild(PID, now=BASE)
    assert first.symbols == second.symbols
    assert len(store.book(PID)) == 1


def test_rebuild_removes_a_position_that_no_longer_exists():
    """A stale row would have to be filtered on every read, and the one place
    that forgets reports a closed position as held."""
    service, ex, store, _ = _service()
    store.save(Holding(portfolio_id=PID, symbol="STALE",
                       lots=[Lot("L", D(2026, 1, 1), 10, R("100.00"))]))
    _load(ex, [_ex(1, "TCS", Action.BUY, 10, "100.00")])

    service.rebuild(PID, now=BASE)
    assert {h.symbol for h in store.book(PID)} == {"TCS"}


def test_an_anomalous_rebuild_is_audited_as_a_warning():
    service, ex, _, audit = _service()
    _load(ex, [_ex(1, "TCS", Action.BUY, 5, "100.00"),
               _ex(2, "TCS", Action.SELL, 10, "110.00", day_offset=1)])
    service.rebuild(PID, now=BASE)
    entry = next(e for e in audit.entries if e["action"] == "holdings.rebuilt")
    assert entry["severity"] == "warning"


# ----------------------------------------------------------------- verify ---


def test_verify_passes_on_a_freshly_rebuilt_book():
    service, ex, _, _ = _service()
    _load(ex, [_ex(1, "TCS", Action.BUY, 10, "100.00")])
    service.rebuild(PID, now=BASE)
    assert service.verify(PID) == []


def test_verify_detects_a_tampered_quantity():
    """A reconstruction that reads the stored book cannot detect a corrupt
    stored book -- so this derives independently (§9.1)."""
    service, ex, store, _ = _service()
    _load(ex, [_ex(1, "TCS", Action.BUY, 10, "100.00")])
    service.rebuild(PID, now=BASE)

    store.save(Holding(portfolio_id=PID, symbol="TCS",
                       lots=[Lot("L", D(2026, 1, 1), 999, R("100.00"))]))
    problems = service.verify(PID)
    assert problems and "999" in problems[0] and "10" in problems[0]


def test_verify_detects_a_position_with_no_executions_behind_it():
    service, ex, store, _ = _service()
    store.save(Holding(portfolio_id=PID, symbol="GHOST",
                       lots=[Lot("L", D(2026, 1, 1), 10, R("100.00"))]))
    problems = service.verify(PID)
    assert problems and "GHOST" in problems[0]


def test_verify_detects_a_missing_position():
    service, ex, store, _ = _service()
    _load(ex, [_ex(1, "TCS", Action.BUY, 10, "100.00")])
    problems = service.verify(PID)
    assert problems and "nothing is stored" in problems[0]
