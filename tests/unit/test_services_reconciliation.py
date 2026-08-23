"""Tests for the consistency checks.

Each check compares a CACHE against its AUTHORITY. What is tested is that a
deliberately-introduced disagreement is caught -- a reconciler that only passes
on clean data proves nothing.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from algotrade.domain import money
from algotrade.domain.models import (
    Action,
    Execution,
    Holding,
    Item,
    ItemStatus,
    Lot,
    RecommendationSet,
    Snapshot,
)
from algotrade.services.holdings import HoldingsService
from algotrade.services.reconciliation import (
    InconsistentState,
    ReconciliationService,
)
from tests.unit.fakes import (
    FakeAudit,
    FakeCash,
    FakeExecutions,
    FakeHoldings,
    FakeRecommendations,
    FakeRepo,
)

D = date
R = money.rupees
PID = "paper_momentum_v1_001"
SET = f"{PID}:2026-09:r1"
BASE = datetime(2026, 9, 30, 4, 0, tzinfo=timezone.utc)


class FakeSnapshots:
    def __init__(self, snapshot=None) -> None:
        self.snapshot = snapshot

    def latest(self, portfolio_id):
        return self.snapshot


def _execution(n=1, symbol="TCS", action=Action.BUY, qty=10, price="100.00",
               charges="0.00") -> Execution:
    return Execution(
        execution_id=f"{SET}:{n}:1", portfolio_id=PID, set_id=SET, item_seq=n,
        symbol=symbol, action=action, quantity=qty, price=R(price),
        charges=R(charges), reference_price=R(price), executed_at=BASE,
    )


def _rec(items) -> RecommendationSet:
    return RecommendationSet(
        set_id=SET, portfolio_id=PID, rebalance_run_id=f"{PID}:2026-09",
        period="2026-09", as_of_session=D(2026, 9, 30), items=items,
    )


def _snapshot(**over) -> Snapshot:
    """A snapshot that is internally consistent by construction:
    nav = invested + cash, positions sum to invested, unit_nav x units = nav."""
    from algotrade.domain.models import Position

    kwargs = dict(
        portfolio_id=PID, date=D(2026, 9, 30), nav=R("1500.00"),
        cash=R("500.00"), invested_value=R("1000.00"),
        units=Decimal("15.000"), unit_nav=Decimal("100.0000"),
        total_contributed=R("1500.00"),
        positions=[Position("TCS", 10, R("100.00"), R("1000.00"), 0.667)],
        peak_unit_nav=Decimal("100.0000"),
    )
    kwargs.update(over)
    return Snapshot(**kwargs)


def _wire(*, executions=None, holdings=None, cash=None, recs=None,
          portfolio=None, snapshot=None):
    ex = executions or FakeExecutions()
    hold = holdings or FakeHoldings()
    csh = cash or FakeCash()
    rec_repo = recs or FakeRecommendations()
    portfolios = FakeRepo()
    portfolios.docs[PID] = portfolio or {"_id": PID, "cash": csh.balance(PID)}

    holdings_service = HoldingsService(
        executions=ex, holdings=hold, audit=FakeAudit())
    service = ReconciliationService(
        portfolios=portfolios, holdings_service=holdings_service, cash=csh,
        executions=ex, recommendations=rec_repo,
        snapshots=FakeSnapshots(snapshot),
    )
    return service, ex, hold, csh, rec_repo, portfolios


def _record(ex, cash, execution):
    ex.record(execution, business_date=execution.executed_at.date())
    signed = (money.rupees(-execution.net_value)
              if execution.action == Action.BUY else execution.net_value)
    cash.record(portfolio_id=PID, kind=execution.action.value, amount=signed,
                on=execution.executed_at.date(), now=BASE,
                discriminator=execution.execution_id,
                execution_id=execution.execution_id)


# ------------------------------------------------------------ clean case ----


def test_a_consistent_portfolio_passes_every_check():
    ex, cash = FakeExecutions(), FakeCash()
    _record(ex, cash, _execution())
    hold = FakeHoldings()
    HoldingsService(executions=ex, holdings=hold, audit=FakeAudit()).rebuild(
        PID, now=BASE)

    service, *_ = _wire(executions=ex, holdings=hold, cash=cash,
                        snapshot=_snapshot())
    report = service.check(PID)
    assert report.ok, [str(f) for f in report.errors]


# ------------------------------------------ holdings vs executions ----------


def test_a_tampered_holding_quantity_is_caught():
    ex, cash = FakeExecutions(), FakeCash()
    _record(ex, cash, _execution(qty=10))
    hold = FakeHoldings()
    hold.save(Holding(portfolio_id=PID, symbol="TCS",
                      lots=[Lot("L", D(2026, 9, 30), 999, R("100.00"))]))

    service, *_ = _wire(executions=ex, holdings=hold, cash=cash)
    report = service.check(PID)
    assert not report.ok
    assert any(f.check == "holdings" for f in report.errors)


# ----------------------------------------------- cash vs the ledger ---------


def test_a_cached_cash_balance_that_drifted_is_caught():
    """`portfolios.cash` is a cache; the ledger is the authority."""
    cash = FakeCash()
    cash.record(portfolio_id=PID, kind="opening_balance", amount=R("1000.00"),
                on=D(2026, 9, 1), now=BASE, discriminator="init")

    service, *_ = _wire(cash=cash, portfolio={"_id": PID, "cash": R("9999.00")})
    report = service.check(PID)
    assert not report.ok
    assert any("ledger sums to" in f.message for f in report.errors)


def test_an_execution_that_moved_no_cash_is_caught():
    """Every fill must have produced exactly one ledger entry."""
    ex, cash = FakeExecutions(), FakeCash()
    ex.record(_execution(), business_date=D(2026, 9, 30))   # no ledger entry
    hold = FakeHoldings()
    HoldingsService(executions=ex, holdings=hold, audit=FakeAudit()).rebuild(
        PID, now=BASE)

    service, *_ = _wire(executions=ex, holdings=hold, cash=cash)
    report = service.check(PID)
    assert any("moved no cash" in f.message for f in report.errors)


def test_a_ledger_entry_with_no_execution_behind_it_is_caught():
    cash = FakeCash()
    cash.record(portfolio_id=PID, kind="buy", amount=R("-1000.00"),
                on=D(2026, 9, 30), now=BASE, discriminator="ghost",
                execution_id="ghost")

    service, *_ = _wire(cash=cash)
    report = service.check(PID)
    assert any("missing execution" in f.message for f in report.errors)


# ----------------------------------------- rollups vs executions ------------


def test_a_rollup_that_disagrees_with_its_executions_is_caught():
    ex, cash = FakeExecutions(), FakeCash()
    _record(ex, cash, _execution(qty=10))

    item = Item(seq=1, action=Action.BUY, symbol="TCS", quantity=10,
                reference_price=R("100.00"), status=ItemStatus.EXECUTED,
                executed_quantity=999)          # a lie
    recs = FakeRecommendations()
    recs.sets[SET] = _rec([item])

    hold = FakeHoldings()
    HoldingsService(executions=ex, holdings=hold, audit=FakeAudit()).rebuild(
        PID, now=BASE)
    service, *_ = _wire(executions=ex, holdings=hold, cash=cash, recs=recs)
    report = service.check(PID)
    assert any(f.check == "rollups" for f in report.errors)


# ---------------------------------------------- snapshot consistency --------


def test_a_snapshot_whose_nav_does_not_equal_its_parts_is_caught():
    """How a plausible wrong number ships: the total computed separately from
    the components."""
    service, *_ = _wire(snapshot=_snapshot(nav=R("9999.00")))
    report = service.check(PID)
    assert any("nav" in f.message for f in report.errors)


def test_positions_that_do_not_sum_to_invested_value_are_caught():
    from algotrade.domain.models import Position

    service, *_ = _wire(snapshot=_snapshot(
        positions=[Position("TCS", 1, R("1.00"), R("1.00"), 1.0)]))  # sums to 1, not 1000
    report = service.check(PID)
    assert any("positions sum to" in f.message for f in report.errors)


def test_unit_nav_times_units_must_equal_nav():
    """The bug this caught for real: unit_nav read back at two decimals instead
    of four, so the product missed NAV by rupees."""
    service, *_ = _wire(snapshot=_snapshot(unit_nav=Decimal("50.0000")))
    report = service.check(PID)
    assert any("unit_nav" in f.message for f in report.errors)


def test_no_snapshot_is_a_warning_not_an_error():
    service, *_ = _wire()
    report = service.check(PID)
    assert any(f.check == "snapshot" and f.severity == "warning"
               for f in report.findings)


# ------------------------------------------------------------- raising ------


def test_raise_if_inconsistent_lists_every_error():
    service, *_ = _wire(portfolio={"_id": PID, "cash": R("9999.00")},
                        snapshot=_snapshot(nav=R("1.00")))
    with pytest.raises(InconsistentState) as exc:
        service.check(PID).raise_if_inconsistent()
    assert "cash" in str(exc.value)


def test_a_clean_report_does_not_raise():
    ex, cash = FakeExecutions(), FakeCash()
    _record(ex, cash, _execution())
    hold = FakeHoldings()
    HoldingsService(executions=ex, holdings=hold, audit=FakeAudit()).rebuild(
        PID, now=BASE)
    service, *_ = _wire(executions=ex, holdings=hold, cash=cash,
                        snapshot=_snapshot())
    service.check(PID).raise_if_inconsistent()


def test_a_missing_portfolio_is_an_error():
    service, _, _, _, _, portfolios = _wire()
    portfolios.docs.clear()
    assert not service.check(PID).ok
