"""The self-healing verifier must refuse the shapes it used to accept.

Every test here is a regression against a specific way this check reported
`SELF-HEALING DEMONSTRATED` on 2026-08-30 without the property holding.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from algotrade.acceptance.verify_recovery import RecoveryReport, analyse

PORTFOLIO = "paper_momentum_v1_001"


class _FakeJobRuns:
    """Just enough of JobRunRepository.find for `analyse`."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def find(self, filter=None, *, sort=None, limit=0, projection=None):
        out = []
        for row in self.rows:
            if not self._matches(row, filter or {}):
                continue
            out.append(row)
        for key, direction in reversed(sort or []):
            out.sort(key=lambda r: r.get(key) or 0, reverse=direction < 0)
        return out[:limit] if limit else out

    @staticmethod
    def _matches(row, filter) -> bool:
        for key, want in filter.items():
            got = row.get(key)
            if isinstance(want, dict):
                if "$ne" in want and got == want["$ne"]:
                    return False
            elif got != want:
                return False
        return True


class _FakeSnapshots:
    def __init__(self, dates: list[date]) -> None:
        self._dates = dates

    def dates(self, _portfolio_id):
        return self._dates


def _run(job, day, attempt, status, *, trigger="schedule", synthetic=False):
    row = {
        "_id": f"{job}:{day}:{attempt}",
        "job_name": job,
        "business_date": day,
        "attempt": attempt,
        "status": status,
        "trigger": trigger,
    }
    if synthetic:
        row["synthetic"] = True
    return row


def _analyse(rows, snapshot_dates, *, reconciles=True, monkeypatch=None):
    """Run `analyse` with the reconciliation half stubbed out.

    Reconciliation needs six repositories and a live book; it is exercised by
    its own tests. What matters here is the ledger reasoning.
    """
    import algotrade.acceptance.verify_recovery as vr

    class _Result:
        ok = reconciles
        errors: list = []

    def _fake_check(self, portfolio_id):
        return _Result()

    monkeypatch.setattr(vr.ReconciliationService, "check", _fake_check)
    monkeypatch.setattr(vr, "JobRunRepository", lambda db: _FakeJobRuns(rows))
    monkeypatch.setattr(vr, "SnapshotRepository",
                        lambda db: _FakeSnapshots(snapshot_dates))
    return analyse(db=object(), portfolio_id=PORTFOLIO)


def test_a_scheduled_recovery_with_a_snapshot_is_demonstrated(monkeypatch):
    """The property, genuinely held: failed, then a SCHEDULED run fixed it,
    the date has a snapshot, and the book reconciles."""
    rows = [
        _run("generate_signals", "2026-09-01", 1, "failed"),
        _run("generate_signals", "2026-09-01", 2, "success", trigger="schedule"),
    ]
    report = _analyse(rows, [date(2026, 9, 1)], monkeypatch=monkeypatch)
    assert report.demonstrated
    assert report.verdict == "SELF-HEALING DEMONSTRATED"


def test_a_manual_repair_is_not_self_healing(monkeypatch):
    """The gap this check had: a human pressing Run workflow satisfied it.

    That is a person healing the system, which is the opposite of the claim.
    """
    rows = [
        _run("generate_signals", "2026-09-01", 1, "failed"),
        _run("generate_signals", "2026-09-01", 2, "success",
             trigger="workflow_dispatch"),
    ]
    report = _analyse(rows, [date(2026, 9, 1)], monkeypatch=monkeypatch)
    assert not report.demonstrated
    assert report.verdict == "REPAIRED, BUT NOT UNAIDED"


def test_a_synthetic_pair_proves_nothing(monkeypatch):
    """The 2026-08-19 rehearsal: rows hand-injected for `value_portfolio`, a
    job that writes no ledger rows at all, in exactly the shape this function
    looks for. It reported success for months on that basis."""
    rows = [
        _run("value_portfolio", "2026-08-19", 1, "failed", synthetic=True),
        _run("value_portfolio", "2026-08-19", 2, "success", synthetic=True),
    ]
    report = _analyse(rows, [date(2026, 8, 19)], monkeypatch=monkeypatch)
    assert report.recoveries == []
    assert not report.demonstrated
    assert report.verdict == "NOT YET DEMONSTRATED"


def test_an_unrecorded_trigger_does_not_count_as_scheduled(monkeypatch):
    """Rows written before the trigger was captured are unknown, not proof."""
    rows = [
        _run("generate_signals", "2026-09-01", 1, "failed", trigger=""),
        _run("generate_signals", "2026-09-01", 2, "success", trigger=""),
    ]
    report = _analyse(rows, [date(2026, 9, 1)], monkeypatch=monkeypatch)
    assert not report.demonstrated


def test_a_retry_without_a_snapshot_is_not_a_filled_gap(monkeypatch):
    """Re-attempted is not repaired."""
    rows = [
        _run("generate_signals", "2026-09-01", 1, "failed"),
        _run("generate_signals", "2026-09-01", 2, "success"),
    ]
    report = _analyse(rows, [], monkeypatch=monkeypatch)          # no snapshot
    assert not report.demonstrated


def test_a_recovered_but_unreconciled_book_is_not_a_recovery(monkeypatch):
    rows = [
        _run("generate_signals", "2026-09-01", 1, "failed"),
        _run("generate_signals", "2026-09-01", 2, "success"),
    ]
    report = _analyse(rows, [date(2026, 9, 1)], reconciles=False,
                      monkeypatch=monkeypatch)
    assert not report.demonstrated
    assert report.verdict == "RECOVERED BUT INCONSISTENT"


def test_a_failure_that_was_never_retried_is_reported_as_such(monkeypatch):
    rows = [_run("generate_signals", "2026-09-01", 1, "failed")]
    report = _analyse(rows, [date(2026, 9, 1)], monkeypatch=monkeypatch)
    assert not report.demonstrated
    assert report.verdict == "FAILURES NOT RECOVERED"
    assert len(report.failures_never_recovered) == 1


def test_two_partial_recoveries_do_not_add_up_to_one_real_one(monkeypatch):
    """One date filled its gap but was repaired by hand; another was scheduled
    but left no snapshot. Neither demonstrates the property, and an `any()`
    over separate conditions would have let them combine into a false pass."""
    rows = [
        _run("generate_signals", "2026-09-01", 1, "failed"),
        _run("generate_signals", "2026-09-01", 2, "success",
             trigger="workflow_dispatch"),                 # filled, but aided
        _run("generate_signals", "2026-09-02", 1, "failed"),
        _run("generate_signals", "2026-09-02", 2, "success"),  # unaided, no snap
    ]
    report = _analyse(rows, [date(2026, 9, 1)], monkeypatch=monkeypatch)
    assert len(report.recoveries) == 2
    assert not report.demonstrated


def test_an_empty_ledger_is_not_demonstrated(monkeypatch):
    report = _analyse([], [], monkeypatch=monkeypatch)
    assert not report.demonstrated
    assert report.verdict == "NOT YET DEMONSTRATED"
