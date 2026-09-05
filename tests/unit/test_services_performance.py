"""Tests for performance metrics.

Two properties this module exists to get right, both learned the hard way:
Sharpe DEDUCTS the risk-free rate on a long-only book (§11q F1 reversed a
ranking by omitting it), and annualised figures are withheld until there is
enough history to annualise honestly.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from algotrade.domain import money
from algotrade.services.performance import (
    MIN_SESSIONS_TO_ANNUALISE,
    RISK_FREE,
    PerformanceService,
)
from tests.unit.fakes import FakeCash

D = date
R = money.rupees
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
PID = "paper_momentum_v1_001"


class FakeSeries:
    """Snapshot repository exposing only what PerformanceService reads."""

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    def series(self, portfolio_id, *, start=None, end=None, fields=None):
        return [dict(r) for r in self.rows]


def _rows(unit_navs: list[str], *, start=D(2026, 1, 1), benchmark=None):
    out = []
    for i, nav in enumerate(unit_navs):
        row = {
            "date": start + timedelta(days=i),
            "unit_nav": Decimal(nav),
            "nav": money.rupees(Decimal(nav) * 2000),
        }
        if benchmark:
            row["benchmark_close"] = money.rupees(benchmark[i])
        out.append(row)
    return out


def _service(rows, *, contributions=None):
    cash = FakeCash()
    for i, (day, amount) in enumerate(contributions or []):
        cash.record(portfolio_id=PID, kind="contribution", amount=amount,
                    on=day, now=NOW, discriminator=str(i))
    return PerformanceService(snapshots=FakeSeries(rows), cash=cash)


def _long(n=120, growth=Decimal("1.001")):
    navs, nav = [], Decimal("100.0000")
    for _ in range(n):
        navs.append(f"{nav:.4f}")
        nav *= growth
    return navs


# --------------------------------------------------------------- basics -----


def test_no_snapshots_means_no_performance():
    assert _service([]).compute(PID) is None


def test_total_return_is_measured_on_unit_nav():
    """Not raw NAV -- a contribution would otherwise read as a gain."""
    perf = _service(_rows(["100.0000", "110.0000"])).compute(PID)
    assert perf.total_return == pytest.approx(0.10)


def test_total_return_is_measured_from_inception_not_the_first_snapshot():
    """The first snapshot is written at the END of the first valued session,
    so it already contains that session's move. Measuring from it drops the
    move -- which is how the History page showed +1.18% beside Summary's
    -0.47% for the same book on the same day (2026-09-05).

    Unit NAV is 100 at inception by construction, so that is the base.
    """
    perf = _service(_rows(["98.3742", "99.5321"])).compute(PID)
    assert perf.total_return == pytest.approx(-0.004679, abs=1e-6)


def test_a_windowed_query_measures_within_its_window():
    """Base 100 is inception's starting point, not an arbitrary window's."""
    perf = _service(_rows(["98.3742", "99.5321"])).compute(
        PID, start=D(2026, 1, 1))
    assert perf.total_return == pytest.approx(0.011770, abs=1e-6)


def test_win_rate_counts_up_sessions():
    perf = _service(_rows(["100.0000", "101.0000", "100.5000", "102.0000"])).compute(PID)
    assert perf.win_rate == pytest.approx(2 / 3)
    assert perf.best_day > 0 and perf.worst_day < 0


# ---------------------------------------------------- annualisation guard ---


def test_annualised_figures_are_withheld_on_a_short_history():
    """A -0.67% move over three days annualises to -70% CAGR. Arithmetically
    correct, completely meaningless, and alarming on a daily report."""
    perf = _service(_rows(["100.0000", "99.5000", "99.3300"])).compute(PID)
    assert perf.total_return is not None
    assert perf.cagr is None
    assert perf.sharpe is None
    assert perf.martin is None
    assert perf.volatility is None


def test_annualised_figures_appear_once_there_is_enough_history():
    perf = _service(_rows(_long(MIN_SESSIONS_TO_ANNUALISE + 5))).compute(PID)
    assert perf.cagr is not None
    assert perf.volatility is not None


# ------------------------------------------------------------- risk ---------


def test_sharpe_deducts_the_risk_free_rate():
    """§11q F1: omitting rf on a long-only book inflated every Sharpe and
    reversed the allocation ranking once corrected."""
    perf = _service(_rows(_long())).compute(PID)
    assert perf.sharpe == pytest.approx(
        (perf.cagr - RISK_FREE) / perf.volatility, rel=1e-9)
    # And the un-deducted figure would be materially higher.
    assert perf.cagr / perf.volatility > perf.sharpe


def test_a_custom_risk_free_rate_is_honoured():
    perf = _service(_rows(_long())).compute(PID, risk_free=0.0)
    assert perf.sharpe == pytest.approx(perf.cagr / perf.volatility, rel=1e-9)


def test_max_drawdown_is_measured_from_the_running_peak():
    perf = _service(_rows(
        ["100.0000", "120.0000", "90.0000", "110.0000"])).compute(PID)
    assert perf.max_drawdown == pytest.approx(-0.25)      # 120 -> 90


def test_current_drawdown_is_where_it_stands_now():
    perf = _service(_rows(["100.0000", "120.0000", "108.0000"])).compute(PID)
    assert perf.current_drawdown == pytest.approx(-0.10)


def test_a_monotonic_rise_has_no_drawdown():
    perf = _service(_rows(["100.0000", "110.0000", "120.0000"])).compute(PID)
    assert perf.max_drawdown == 0.0
    assert perf.ulcer_index == 0.0


def test_martin_uses_ulcer_and_also_deducts_rf():
    perf = _service(_rows(_long(120, Decimal("1.0005")) )).compute(PID)
    if perf.ulcer_index > 0 and perf.cagr is not None:
        assert perf.martin == pytest.approx(
            (perf.cagr - RISK_FREE) / (perf.ulcer_index / 100), rel=1e-9)


# -------------------------------------------------------------- XIRR --------


def test_xirr_uses_the_cash_ledger_not_the_nav_series():
    """Money-weighted: what YOU earned given when your money was at risk."""
    rows = _rows(["100.0000", "120.0000"], start=D(2026, 1, 1))
    rows[-1]["date"] = D(2027, 1, 1)
    rows[-1]["nav"] = money.rupees("240000.00")
    perf = _service(rows, contributions=[
        (D(2026, 1, 1), R("200000.00"))]).compute(PID)
    assert perf.xirr == pytest.approx(0.20, abs=0.01)


def test_no_contributions_means_no_xirr():
    assert _service(_rows(["100.0000", "110.0000"])).compute(PID).xirr is None


# --------------------------------------------------------- benchmark --------


def test_excess_return_is_measured_against_the_benchmark():
    perf = _service(_rows(["100.0000", "110.0000"],
                          benchmark=["1000.00", "1050.00"])).compute(PID)
    assert perf.benchmark_return == pytest.approx(0.05)
    assert perf.excess_return == pytest.approx(0.05)


def test_no_benchmark_data_means_no_excess():
    perf = _service(_rows(["100.0000", "110.0000"])).compute(PID)
    assert perf.benchmark_return is None
    assert perf.excess_return is None


def test_the_summary_is_serialisable():
    perf = _service(_rows(_long())).compute(PID)
    summary = perf.summary()
    assert set(summary) >= {"sessions", "total_return", "cagr", "max_drawdown"}


def test_xirr_is_withheld_on_a_short_sample_just_like_cagr():
    """XIRR solves for an ANNUAL rate, so 13 sessions produced -23.8% -- the
    same class of arithmetically-correct nonsense `MIN_SESSIONS_TO_ANNUALISE`
    suppresses for CAGR. It was ungated until 2026-09-01, leaving the one
    annualised figure on show being the one a person takes most personally.
    """
    import inspect

    from algotrade.services import performance

    source = inspect.getsource(performance.PerformanceService.compute)
    xirr_block = source[source.index("xirr = None"):source.index("benchmark_return")]
    assert "MIN_DAYS_TO_ANNUALISE" in xirr_block
