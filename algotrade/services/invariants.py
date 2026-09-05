"""Cross-checks on stored figures: the same number, computed two ways.

Every defect found on 2026-09-04/05 had one shape -- **a number derived from
where the data happened to start rather than from where the thing being
measured began** -- and every one of them was caught by a human reading a
rendered page, not by the system:

* `total_return` measured from the first stored snapshot, disagreeing with the
  snapshot's own field about the SIGN.
* Monthly returns measured within each month, compounding to +2.50% beside a
  total return of -0.47%.
* `day_return` compared against the newest row rather than the prior session,
  so every value was null.
* A drawdown peak anchored to a high recorded on a date the portfolio did not
  exist.

Each is arithmetic between two stored numbers. Nothing here needs a market, a
network or a judgement -- which is the argument for checking it every day
rather than waiting for someone to notice a page looks wrong.

Pure functions over already-decoded rows: no repository, no clock, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Sequence

#: Snapshot money and ratios are stored to fewer places than float arithmetic
#: produces. Comparisons are made at the precision the data actually carries,
#: so the check does not fire on the last decimal of a Decimal->float cast.
TOLERANCE: float = 1e-6


@dataclass
class Finding:
    check: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.check}] {self.detail}"


@dataclass
class Report:
    portfolio_id: str
    findings: list[Finding] = field(default_factory=list)
    checked: int = 0

    @property
    def ok(self) -> bool:
        return not self.findings

    def add(self, check: str, detail: str) -> None:
        self.findings.append(Finding(check, detail))


def _f(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def check_snapshots(
    portfolio_id: str,
    rows: Sequence[dict],
    *,
    base_unit_nav: Decimal,
) -> Report:
    """Every invariant that holds between fields of the snapshot series.

    `rows` must be in date order and already decoded to Decimal.
    """
    report = Report(portfolio_id=portfolio_id)
    if not rows:
        return report
    report.checked = len(rows)

    peak_so_far: Decimal | None = None
    previous: dict | None = None

    for row in rows:
        day = row.get("date")
        unit_nav, peak = row.get("unit_nav"), row.get("peak_unit_nav")
        if unit_nav is None or peak is None:
            report.add("nulls", f"{day}: unit_nav or peak_unit_nav is null")
            previous = row
            continue

        # 1. total_return is measured from inception, not from anywhere else.
        want = float(unit_nav / base_unit_nav - 1)
        got = _f(row.get("total_return"))
        if got is None:
            report.add("total_return", f"{day}: null")
        elif abs(want - got) > TOLERANCE:
            report.add("total_return",
                       f"{day}: stored {got:+.6%}, unit_nav implies {want:+.6%}")

        # 2. drawdown is the decline from the peak, and zero at or above it.
        want_dd = float((unit_nav - peak) / peak) if unit_nav < peak else 0.0
        got_dd = _f(row.get("drawdown"))
        if got_dd is None:
            report.add("drawdown", f"{day}: null")
        elif abs(want_dd - got_dd) > TOLERANCE:
            report.add("drawdown",
                       f"{day}: stored {got_dd:+.6%}, peak implies {want_dd:+.6%}")

        # 3. The peak ratchets and never starts below the base. A peak the
        #    series never reached means it was set on a row since corrected --
        #    or, as in 2026-09-05, on a date the portfolio did not exist.
        if peak < base_unit_nav:
            report.add("peak", f"{day}: peak {peak} is below the base "
                               f"{base_unit_nav}")
        if peak_so_far is not None and peak < peak_so_far:
            report.add("peak", f"{day}: peak {peak} fell from {peak_so_far}")
        peak_so_far = peak if peak_so_far is None else max(peak_so_far, peak)

        # 4. day_return compares against the SESSION BEFORE, not the newest row.
        if previous is not None:
            prior = previous.get("unit_nav")
            got_day = _f(row.get("day_return"))
            if prior and got_day is None:
                report.add("day_return",
                           f"{day}: null, but {previous.get('date')} has a "
                           f"unit_nav to compare against")
            elif prior and got_day is not None:
                want_day = float(unit_nav / prior - 1)
                if abs(want_day - got_day) > TOLERANCE:
                    report.add("day_return",
                               f"{day}: stored {got_day:+.6%}, prior session "
                               f"implies {want_day:+.6%}")
        previous = row

    # 5. The first snapshot's peak cannot exceed the base unless the book had
    #    already risen -- and it cannot have, because the first row IS the
    #    first valuation.
    first = rows[0]
    if first.get("peak_unit_nav") is not None and first.get("unit_nav") is not None:
        expected = max(base_unit_nav, first["unit_nav"])
        if first["peak_unit_nav"] != expected:
            report.add("peak", f"{first.get('date')}: first peak "
                               f"{first['peak_unit_nav']}, expected {expected}")
    return report


def check_monthly_returns(
    monthly: Sequence[dict], total_return: float | None
) -> Report:
    """Monthly returns must compound to the total.

    They did not: +2.1% and +0.4% shown beside -0.47%, because each month was
    measured from its own first close rather than the previous month's. The
    check is the reason the display can be trusted at all -- two numbers on one
    page that disagree are worse than one number with no context.
    """
    report = Report(portfolio_id="")
    if not monthly or total_return is None:
        return report
    report.checked = len(monthly)
    compounded = 1.0
    for month in monthly:
        compounded *= 1.0 + month["ret"]
    if abs((compounded - 1.0) - total_return) > TOLERANCE:
        report.add("monthly",
                   f"{len(monthly)} month(s) compound to {compounded - 1:+.6%}, "
                   f"total return is {total_return:+.6%}")
    return report
