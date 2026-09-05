"""Tests for the cross-checks on stored figures.

Each case reproduces a defect that actually shipped, so the test is the
regression and the check is what would have caught it a day later instead of
three weeks later.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from algotrade.services.invariants import check_monthly_returns, check_snapshots

D = date
BASE = Decimal("100.00")
PID = "paper_momentum_v1_001"


def _row(day, unit_nav, peak, drawdown=None, total_return=None, day_return=None):
    u, p = Decimal(unit_nav), Decimal(peak)
    return {
        "date": day, "unit_nav": u, "peak_unit_nav": p,
        "drawdown": float((u - p) / p) if drawdown is None and u < p
                    else (0.0 if drawdown is None else drawdown),
        "total_return": float(u / BASE - 1) if total_return is None else total_return,
        "day_return": day_return,
    }


def _series():
    a = _row(D(2026, 8, 20), "98.3742", "100.00")
    b = _row(D(2026, 8, 21), "99.3847", "100.00",
             day_return=float(Decimal("99.3847") / Decimal("98.3742") - 1))
    return [a, b]


def test_a_consistent_series_reports_nothing():
    assert check_snapshots(PID, _series(), base_unit_nav=BASE).ok


def test_total_return_measured_from_the_first_snapshot_is_caught():
    """The 2026-09-05 bug: History read +1.18% while Summary read -0.47%."""
    rows = _series()
    rows[-1]["total_return"] = float(
        Decimal("99.3847") / Decimal("98.3742") - 1)      # from the first row
    report = check_snapshots(PID, rows, base_unit_nav=BASE)
    assert not report.ok
    assert any(f.check == "total_return" for f in report.findings)


def test_a_peak_the_series_never_reached_is_caught():
    """100.5600 was recorded on 2026-08-18, a date the portfolio did not
    exist, and became the denominator of every drawdown after it."""
    rows = _series()
    for r in rows:
        r["peak_unit_nav"] = Decimal("100.5600")
        r["drawdown"] = float((r["unit_nav"] - Decimal("100.5600")) / Decimal("100.5600"))
    report = check_snapshots(PID, rows, base_unit_nav=BASE)
    assert any(f.check == "peak" for f in report.findings)


def test_a_peak_below_the_base_is_caught():
    """Seeding the running peak from an empty collection gave ZERO, so the
    first snapshot's peak became its own unit NAV and its drawdown was 0 by
    construction -- hiding a real 1.63% loss."""
    rows = [_row(D(2026, 8, 20), "98.3742", "98.3742", drawdown=0.0)]
    report = check_snapshots(PID, rows, base_unit_nav=BASE)
    assert any(f.check == "peak" for f in report.findings)


def test_a_null_day_return_with_a_prior_session_is_caught():
    """`latest()` rather than the prior session meant every backfilled row
    stored None, and the dashboard's Day column was empty on all twelve."""
    rows = _series()
    rows[-1]["day_return"] = None
    report = check_snapshots(PID, rows, base_unit_nav=BASE)
    assert any(f.check == "day_return" for f in report.findings)


def test_the_first_row_may_have_no_day_return():
    """There is no session before inception to compare against."""
    rows = [_row(D(2026, 8, 20), "98.3742", "100.00")]
    assert check_snapshots(PID, rows, base_unit_nav=BASE).ok


def test_a_drawdown_that_disagrees_with_its_peak_is_caught():
    rows = _series()
    rows[0]["drawdown"] = -0.05
    report = check_snapshots(PID, rows, base_unit_nav=BASE)
    assert any(f.check == "drawdown" for f in report.findings)


def test_an_empty_series_is_not_a_failure():
    assert check_snapshots(PID, [], base_unit_nav=BASE).ok


# ------------------------------------------------------ monthly returns ------


def test_monthly_returns_that_compound_to_the_total_pass():
    monthly = [{"month": "2026-08", "ret": 0.004099},
               {"month": "2026-09", "ret": -0.008742}]
    total = float(Decimal("99.5321") / BASE - 1)
    assert check_monthly_returns(monthly, total).ok


def test_monthly_returns_measured_within_each_month_are_caught():
    """+2.1% and +0.4% compounding to +2.50%, shown beside -0.47%."""
    monthly = [{"month": "2026-08", "ret": 0.0207},
               {"month": "2026-09", "ret": 0.0043}]
    report = check_monthly_returns(monthly, -0.0047)
    assert not report.ok
    assert "compound to" in report.findings[0].detail


def test_no_months_is_not_a_failure():
    assert check_monthly_returns([], -0.0047).ok
