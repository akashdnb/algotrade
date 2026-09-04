"""Tests for valuation, price carry-forward, and reconciliation.

Two behaviours matter most: a weekend valuation must use the last trading
session's closes (correct, not approximate -- nothing traded), and the cash
figure must come from the LEDGER rather than a cached field.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import polars as pl
import pytest

from algotrade.domain import money
from algotrade.domain.models import Holding, Lot, Snapshot
from algotrade.services.valuation import (
    MAX_STALE_SESSIONS,
    NoPricesAvailable,
    ValuationService,
    prices_as_of,
    valuable_dates,
)
from tests.unit.fakes import FakeCash

D = date
R = money.rupees
NOW = datetime(2026, 10, 1, 4, 0, tzinfo=timezone.utc)
PID = "paper_momentum_v1_001"


class FakeSnapshots:
    def __init__(self) -> None:
        self.rows: dict[date, Snapshot] = {}
        self.extra: dict[date, dict] = {}

    @staticmethod
    def key(portfolio_id, day):
        return f"{portfolio_id}:{day}"

    def save(self, snapshot) -> bool:
        created = snapshot.date not in self.rows
        self.rows[snapshot.date] = snapshot
        return created

    def save_valuation(self, snapshot, valuation) -> bool:
        created = self.save(snapshot)
        self.extra[snapshot.date] = {
            "market_value": valuation.market_value,
            "total_pnl": valuation.total_pnl,
        }
        return created

    def latest(self, portfolio_id):
        return self.rows[max(self.rows)] if self.rows else None

    def peak_unit_nav(self, portfolio_id):
        return max((s.peak_unit_nav for s in self.rows.values()),
                   default=money.ZERO)


def _holding(symbol, qty, price="100.00") -> Holding:
    return Holding(portfolio_id=PID, symbol=symbol,
                   lots=[Lot(f"L-{symbol}", D(2026, 1, 1), qty, R(price))])


def _panel(days, symbols, price=100.0):
    rows = [{"date": d, "symbol": s, "close": price + i}
            for i, d in enumerate(days) for s in symbols]
    return pl.DataFrame(rows)


def _service():
    snaps, cash = FakeSnapshots(), FakeCash()
    return ValuationService(snapshots=snaps, cash=cash), snaps, cash


# ------------------------------------------------------ price resolution ----


def test_prices_come_from_the_valuation_date_when_it_is_a_session():
    days = [D(2026, 9, 29), D(2026, 9, 30)]
    prices, price_date, _ = prices_as_of(_panel(days, ["A"]), as_of=D(2026, 9, 30))
    assert price_date == D(2026, 9, 30)
    assert prices["A"] == R("101.00")


def test_a_weekend_carries_forward_the_last_session():
    """Saturday's portfolio is worth exactly what Friday's closes say. Nothing
    traded, so nothing changed -- this is correct, not approximate."""
    days = [D(2026, 10, 1), D(2026, 10, 2)]          # Thu, Fri
    prices, price_date, _ = prices_as_of(_panel(days, ["A"]), as_of=D(2026, 10, 4))
    assert price_date == D(2026, 10, 2)


def test_a_date_before_any_session_is_refused():
    days = [D(2026, 9, 30)]
    with pytest.raises(NoPricesAvailable):
        prices_as_of(_panel(days, ["A"]), as_of=D(2026, 1, 1))


def test_only_the_requested_symbols_are_returned():
    days = [D(2026, 9, 30)]
    prices, _, _ = prices_as_of(_panel(days, ["A", "B", "C"]),
                             as_of=D(2026, 9, 30), symbols={"A", "C"})
    assert set(prices) == {"A", "C"}


# --------------------------------------------------------------- valuation --


def _value(service, cash, *, holdings=None, prices=None, units="2000.000",
           contributed="200000.00", realised="0.00"):
    return service.value(
        portfolio_id=PID, as_of=D(2026, 9, 30),
        holdings=holdings if holdings is not None else [],
        prices=prices or {}, price_date=D(2026, 9, 30),
        units=Decimal(units), total_contributed=R(contributed),
        realised_pnl=R(realised),
    )


def test_total_value_is_market_value_plus_cash():
    service, _, cash = _service()
    cash.record(portfolio_id=PID, kind="opening_balance", amount=R("50000.00"),
                on=D(2026, 1, 1), now=NOW, discriminator="init")

    v = _value(service, cash, holdings=[_holding("A", 100)],
               prices={"A": R("110.00")})
    assert v.market_value == R("11000.00")
    assert v.cash == R("50000.00")
    assert v.total_value == R("61000.00")


def test_cash_comes_from_the_ledger_not_a_cached_field():
    """The ledger is the authority; this is where that matters most."""
    service, _, cash = _service()
    cash.record(portfolio_id=PID, kind="opening_balance", amount=R("200000.00"),
                on=D(2026, 1, 1), now=NOW, discriminator="init")
    cash.record(portfolio_id=PID, kind="buy", amount=R("-150000.00"),
                on=D(2026, 2, 1), now=NOW, discriminator="e1", execution_id="e1")

    assert _value(service, cash).cash == R("50000.00")


def test_unrealised_pnl_is_market_value_less_cost_basis():
    service, _, cash = _service()
    v = _value(service, cash, holdings=[_holding("A", 100, "100.00")],
               prices={"A": R("112.00")})
    assert v.invested_capital == R("10000.00")
    assert v.unrealised_pnl == R("1200.00")


def test_total_pnl_combines_realised_and_unrealised():
    service, _, cash = _service()
    v = _value(service, cash, holdings=[_holding("A", 100)],
               prices={"A": R("110.00")}, realised="500.00")
    assert v.total_pnl == R("1500.00")


def test_position_weights_are_against_total_value_so_cash_drag_is_visible():
    service, _, cash = _service()
    cash.record(portfolio_id=PID, kind="opening_balance", amount=R("10000.00"),
                on=D(2026, 1, 1), now=NOW, discriminator="init")
    v = _value(service, cash, holdings=[_holding("A", 100)],
               prices={"A": R("100.00")})
    assert v.positions[0].weight == pytest.approx(0.5)


def test_a_suspended_holding_is_carried_at_zero_and_still_listed():
    service, _, cash = _service()
    v = _value(service, cash,
               holdings=[_holding("A", 100), _holding("GONE", 50)],
               prices={"A": R("100.00")})
    symbols = {p.symbol for p in v.positions}
    assert symbols == {"A", "GONE"}
    assert next(p for p in v.positions if p.symbol == "GONE").value == money.ZERO


def test_unit_nav_tracks_performance_and_units_do_not_move():
    service, _, cash = _service()
    cash.record(portfolio_id=PID, kind="opening_balance", amount=R("220000.00"),
                on=D(2026, 1, 1), now=NOW, discriminator="init")
    v = _value(service, cash, units="2000.000")
    assert v.units == Decimal("2000.000")
    assert v.unit_nav == R("110.00")


def test_unit_nav_keeps_four_decimals():
    service, _, cash = _service()
    cash.record(portfolio_id=PID, kind="opening_balance", amount=R("284310.22"),
                on=D(2026, 1, 1), now=NOW, discriminator="init")
    v = _value(service, cash, units="2412.887")
    assert v.unit_nav.as_tuple().exponent == -4


def test_drawdown_is_measured_against_the_running_peak():
    service, snaps, cash = _service()
    snaps.rows[D(2026, 9, 1)] = Snapshot(
        portfolio_id=PID, date=D(2026, 9, 1), nav=R("240000.00"),
        cash=money.ZERO, invested_value=R("240000.00"),
        units=Decimal("2000.000"), unit_nav=R("120.00"),
        total_contributed=R("200000.00"), peak_unit_nav=R("120.00"))
    cash.record(portfolio_id=PID, kind="opening_balance", amount=R("216000.00"),
                on=D(2026, 1, 1), now=NOW, discriminator="init")

    v = _value(service, cash, units="2000.000")
    assert v.unit_nav == R("108.00")
    assert v.peak_unit_nav == R("120.00")
    assert v.drawdown == pytest.approx(-0.10)


def test_the_day_return_compares_against_the_previous_snapshot():
    service, snaps, cash = _service()
    snaps.rows[D(2026, 9, 29)] = Snapshot(
        portfolio_id=PID, date=D(2026, 9, 29), nav=R("200000.00"),
        cash=money.ZERO, invested_value=R("200000.00"),
        units=Decimal("2000.000"), unit_nav=R("100.00"),
        total_contributed=R("200000.00"), peak_unit_nav=R("100.00"))
    # 204,000 / 2,000 units = unit NAV 102.00 against yesterday's 100.00.
    cash.record(portfolio_id=PID, kind="opening_balance", amount=R("204000.00"),
                on=D(2026, 1, 1), now=NOW, discriminator="init")

    assert _value(service, cash).day_return == pytest.approx(0.02)


def test_there_is_no_day_return_on_the_first_valuation():
    service, _, cash = _service()
    assert _value(service, cash).day_return is None


# --------------------------------------------------------------- snapshot ---


def test_a_snapshot_is_written_and_re_running_replaces_it():
    service, snaps, cash = _service()
    cash.record(portfolio_id=PID, kind="opening_balance", amount=R("200000.00"),
                on=D(2026, 1, 1), now=NOW, discriminator="init")
    v = _value(service, cash, holdings=[_holding("A", 100)],
               prices={"A": R("110.00")})

    service.snapshot(v, now=NOW, total_contributed=R("200000.00"))
    service.snapshot(v, now=NOW, total_contributed=R("200000.00"))
    assert len(snaps.rows) == 1


def test_the_snapshot_records_the_price_date_so_carry_forward_is_visible():
    service, snaps, cash = _service()
    v = service.value(
        portfolio_id=PID, as_of=D(2026, 10, 4), holdings=[],
        prices={}, price_date=D(2026, 10, 2), units=Decimal("2000.000"),
        total_contributed=R("200000.00"))
    assert v.date == D(2026, 10, 4)
    assert v.price_date == D(2026, 10, 2)


def test_nav_equals_its_own_parts():
    """The invariant the reconciler asserts: a snapshot whose total is computed
    separately from its components is how a plausible wrong number ships."""
    service, _, cash = _service()
    cash.record(portfolio_id=PID, kind="opening_balance", amount=R("1234.56"),
                on=D(2026, 1, 1), now=NOW, discriminator="init")
    v = _value(service, cash, holdings=[_holding("A", 77)],
               prices={"A": R("101.23")})
    assert v.total_value == money.rupees(v.market_value + v.cash)


# ------------------------------------------------ benchmark_close decoding ---


def test_benchmark_close_is_declared_as_money():
    """Dead code stayed dead until 2026-09-01, then crashed.

    `_benchmark_return` does arithmetic on `benchmark_close`. Nothing ever
    wrote the field, so the branch never ran -- and it was missing from
    MONEY_FIELDS, so the codec left it as a raw Decimal128. The first day it
    was populated, the daily job's performance step raised
    `unsupported operand type(s) for -: 'Decimal128' and 'Decimal128'`.
    """
    from algotrade.repositories import schema

    fields = schema.MONEY_FIELDS["portfolio_snapshots"]
    assert "benchmark_close" in fields
    assert "benchmark_sleeve_value" in fields


def test_benchmark_return_computes_from_decimals():
    from decimal import Decimal

    from algotrade.services.performance import _benchmark_return

    rows = [{"benchmark_close": Decimal("100.00")},
            {"benchmark_close": Decimal("110.00")}]
    assert _benchmark_return(rows) == pytest.approx(0.10)


def test_benchmark_return_is_none_without_two_readings():
    from decimal import Decimal

    from algotrade.services.performance import _benchmark_return

    assert _benchmark_return([]) is None
    assert _benchmark_return([{"benchmark_close": Decimal("100")}]) is None
    assert _benchmark_return([{"nav": 1}, {"nav": 2}]) is None


# ------------------------------------------- per-symbol price carry-forward --
#
# `prices_as_of` carried forward by DATE -- a weekend resolves to Friday -- but
# not by SYMBOL. A name absent from the latest session was simply missing from
# the dict, and `value_positions` then carried it at zero.
#
# Found 2026-09-04: NSE moved HFCL from series EQ to BE, the panel keeps EQ
# only, and a stock trading 4 lakh shares a day at Rs 231.44 was marked to
# zero. That understated NAV by Rs 6,943 and put a fake 3.5-point drawdown into
# the permanent snapshot series -- the exact number INVESTMENT-POLICY.md's
# behavioural rules are read against.


def _gappy_panel(days, symbol_days):
    """symbol_days: {symbol: [days it traded]}."""
    rows = [{"date": d, "symbol": s, "close": 100.0 + i}
            for i, d in enumerate(days)
            for s, sd in symbol_days.items() if d in sd]
    return pl.DataFrame(rows)


WEEK = [D(2026, 9, 1), D(2026, 9, 2), D(2026, 9, 3), D(2026, 9, 4)]


def test_a_symbol_missing_from_the_latest_session_keeps_its_last_close():
    """The HFCL case. It stopped appearing after 2026-09-02 and is still worth
    what it last traded at -- certainly not zero."""
    panel = _gappy_panel(WEEK, {"A": WEEK, "HFCL": WEEK[:2]})
    prices, price_date, stale = prices_as_of(
        panel, as_of=D(2026, 9, 4), symbols={"A", "HFCL"})
    assert price_date == D(2026, 9, 4)
    assert prices["HFCL"] == R("101.00")        # its 2026-09-02 close
    assert stale == {"HFCL": D(2026, 9, 2)}


def test_a_symbol_present_on_the_latest_session_is_not_stale():
    panel = _gappy_panel(WEEK, {"A": WEEK})
    prices, _, stale = prices_as_of(panel, as_of=D(2026, 9, 4), symbols={"A"})
    assert prices["A"] == R("103.00")
    assert stale == {}


def test_carry_forward_stops_after_the_staleness_limit():
    """A price that has not printed in MAX_STALE_SESSIONS is not a price. The
    name is left unpriced so it surfaces, rather than holding a stale mark
    forever -- which is how a delisted stock would keep phantom value."""
    days = [D(2026, 1, 1) + timedelta(days=i) for i in range(MAX_STALE_SESSIONS + 5)]
    panel = _gappy_panel(days, {"A": days, "GONE": days[:1]})
    prices, _, stale = prices_as_of(
        panel, as_of=days[-1], symbols={"A", "GONE"})
    assert "GONE" not in prices
    assert "GONE" not in stale


def test_a_symbol_at_the_edge_of_the_limit_is_still_carried():
    days = [D(2026, 1, 1) + timedelta(days=i) for i in range(MAX_STALE_SESSIONS + 1)]
    panel = _gappy_panel(days, {"A": days, "EDGE": [days[0]]})
    prices, _, stale = prices_as_of(panel, as_of=days[-1],
                                    symbols={"A", "EDGE"})
    assert prices["EDGE"] == R("100.00")
    assert stale["EDGE"] == days[0]


def test_a_weekend_is_not_reported_as_staleness():
    """Nothing traded for anyone, which the DATE resolution already handles.
    Flagging every position as stale each weekend would make the signal
    worthless."""
    days = [D(2026, 10, 1), D(2026, 10, 2)]              # Thu, Fri
    prices, price_date, stale = prices_as_of(
        _panel(days, ["A"]), as_of=D(2026, 10, 4), symbols={"A"})
    assert price_date == D(2026, 10, 2)
    assert stale == {}


def test_carry_forward_is_skipped_when_no_symbols_are_requested():
    """With no holdings there is nothing to carry, and scanning the whole
    panel for 2,600 symbols would be work done for no one."""
    days = [D(2026, 9, 30)]
    prices, _, stale = prices_as_of(_panel(days, ["A", "B"]), as_of=days[0])
    assert set(prices) == {"A", "B"}
    assert stale == {}


# ------------------------------------------- nothing exists before inception --
#
# `value_portfolio --backfill N` marks the CURRENT holdings at each target
# date's prices. For a genuinely missed recent session that is right -- the book
# did not change. For a date before the book existed it invents history.
#
# Found 2026-09-05: the first daily run backfilled five sessions, producing
# snapshots for 2026-08-14..08-19 showing 30 positions and zero cash, when the
# first execution was 2026-08-20 and the portfolio held only cash. Those rows
# then anchored the drawdown peak (100.5550, on a date the portfolio did not
# exist) and gave the History page a starting unit NAV of 99.0460 instead of
# 100, which is why it disagreed with Summary about the sign of total return.


def test_dates_before_inception_are_not_valued():
    days = [D(2026, 8, 18), D(2026, 8, 19), D(2026, 8, 20), D(2026, 8, 21)]
    assert valuable_dates(days, D(2026, 8, 20)) == [D(2026, 8, 20), D(2026, 8, 21)]


def test_the_inception_date_itself_is_valued():
    """The opening balance lands on it, so the book exists that day."""
    assert valuable_dates([D(2026, 8, 20)], D(2026, 8, 20)) == [D(2026, 8, 20)]


def test_an_unfunded_portfolio_is_valued_on_no_date():
    """No ledger entry at all. Valuing it would mark holdings it cannot have
    against contributions it never received."""
    assert valuable_dates([D(2026, 8, 20), D(2026, 8, 21)], None) == []


def test_dates_are_returned_in_order_and_otherwise_untouched():
    days = [D(2026, 8, 21), D(2026, 8, 20), D(2026, 8, 24)]
    assert valuable_dates(days, D(2026, 8, 20)) == days
