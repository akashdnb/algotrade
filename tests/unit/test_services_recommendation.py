"""Tests for the recommendation lifecycle.

Three properties matter most, and all three are about surviving a system that
runs unattended for years:

* **is_due reads STATE**, never "the cron fired", so a dropped monthly schedule
  repairs itself.
* **Regeneration supersedes, never edits.** A set may already have been acted
  on; it is a financial record.
* **The frozen-strategy gate refuses** rather than trading a changed strategy.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from algotrade.domain import money
from algotrade.domain.models import (
    Holding,
    Lot,
    SetStatus,
    SignalRow,
    SignalSet,
)
from algotrade.services.rebalance import RebalanceService
from algotrade.services.recommendation import (
    FrozenStrategyMismatch,
    RebalanceNotDue,
    RecommendationService,
)
from tests.unit.fakes import FakeAudit, FakeRebalanceRuns, FakeRecommendations

D = date
R = money.rupees
NOW = datetime(2026, 9, 30, 14, 30, tzinfo=timezone.utc)
PID = "paper_momentum_v1_001"
HASH = "sha256:abc"


def _signals(symbols, as_of=D(2026, 9, 30)) -> SignalSet:
    return SignalSet(
        strategy_version="momentum_v1", date=as_of, universe_size=len(symbols),
        rows=[SignalRow(rank=i + 1, symbol=s, feature=1.0 - i * 0.01,
                        close=R("100.00"))
              for i, s in enumerate(symbols)],
    )


def _holding(symbol, qty=100) -> Holding:
    return Holding(portfolio_id=PID, symbol=symbol,
                   lots=[Lot(f"L-{symbol}", D(2026, 1, 1), qty, R("100.00"))])


def _service(**over):
    recs, runs, audit = FakeRecommendations(), FakeRebalanceRuns(), FakeAudit()
    service = RecommendationService(
        recommendations=recs, rebalance_runs=runs,
        rebalance=RebalanceService(entry=3, exit_=6),
        audit=audit, ttl_days=over.get("ttl_days", 3),
    )
    return service, recs, runs, audit


def _generate(service, **over):
    kwargs = dict(
        portfolio_id=PID, period="2026-09", holdings=[],
        signals=_signals(["A", "B", "C", "D"]), cash=R("30000.00"),
        prices={s: R("100.00") for s in ("A", "B", "C", "D")},
        as_of=D(2026, 9, 30), now=NOW, panel_manifest_hash="sha256:panel",
        strategy_params_hash=HASH, portfolio_params_hash=HASH,
    )
    kwargs.update(over)
    return service.generate(**kwargs)


# ------------------------------------------------------------- is_due -------


def test_not_due_when_the_period_already_completed():
    service, _, runs, _ = _service()
    runs.docs[runs.key(PID, "2026-09")] = {"_id": "x", "status": "completed"}
    due, why = service.is_due(portfolio_id=PID, period="2026-09",
                              sessions=[D(2026, 9, 30)], as_of=D(2026, 9, 30))
    assert not due and "already completed" in why


def test_not_due_when_the_month_has_no_recorded_sessions():
    service, _, _, _ = _service()
    due, why = service.is_due(portfolio_id=PID, period="2026-10",
                              sessions=[D(2026, 9, 30)], as_of=D(2026, 9, 30))
    assert not due and "no trading sessions" in why


def test_due_once_the_month_has_closed_and_no_run_exists():
    """A later session proves the month is over."""
    service, _, _, _ = _service()
    due, why = service.is_due(
        portfolio_id=PID, period="2026-09",
        sessions=[D(2026, 9, 29), D(2026, 9, 30), D(2026, 10, 1)],
        as_of=D(2026, 10, 1),
    )
    assert due


def test_not_due_on_what_might_still_be_the_months_last_session():
    """Running on the 30th when the 30th is the newest session risks
    rebalancing before the month has actually finished."""
    service, _, _, _ = _service()
    due, why = service.is_due(portfolio_id=PID, period="2026-09",
                              sessions=[D(2026, 9, 29), D(2026, 9, 30)],
                              as_of=D(2026, 9, 30))
    assert not due and "final session" in why


def test_is_due_reads_state_so_a_missed_schedule_self_heals():
    """Three days late, still due -- nothing remembers the cron was dropped."""
    service, _, _, _ = _service()
    sessions = [D(2026, 9, 30), D(2026, 10, 1), D(2026, 10, 2), D(2026, 10, 5)]
    due, _ = service.is_due(portfolio_id=PID, period="2026-09",
                            sessions=sessions, as_of=D(2026, 10, 5))
    assert due


# ---------------------------------------------------------- generation ------


def test_a_first_rebalance_produces_buys_and_no_holds():
    service, recs, runs, audit = _service()
    result = _generate(service)
    rec = result.recommendation

    assert rec.revision == 1
    assert rec.supersedes is None
    assert rec.status == SetStatus.PENDING
    assert len(rec.items) == 3          # entry = 3
    assert rec.holds == []
    assert runs.is_complete(PID, "2026-09")
    assert "recommendation.generated" in audit.actions()


def test_holds_are_recorded_but_are_not_trades():
    """A zero-delta HOLD as an Item would be counted in turnover, charged for,
    and shown as outstanding work."""
    service, _, _, _ = _service()
    holdings = [_holding("A", 99), _holding("B", 99), _holding("C", 99)]
    rec = _generate(
        service, holdings=holdings, cash=R("0.00"),
        signals=_signals(["A", "B", "C", "D"]),
    ).recommendation

    assert set(rec.holds) == {"A", "B", "C"}
    assert rec.items == []


def test_the_expiry_is_set_from_the_signal_date():
    service, _, _, _ = _service()
    rec = _generate(service).recommendation
    assert rec.expires_on == D(2026, 10, 3)


def test_the_input_fingerprint_makes_the_run_reproducible():
    service, _, runs, _ = _service()
    _generate(service)
    fingerprint = runs.docs[runs.key(PID, "2026-09")]["input_fingerprint"]
    assert fingerprint["panel_manifest_hash"] == "sha256:panel"
    assert fingerprint["strategy_params_hash"] == HASH
    assert fingerprint["last_session"] == "2026-09-30"


def test_the_target_records_the_rank_that_justified_each_name():
    """So "why was D bought?" is answerable a year later without re-deriving a
    ranking against data that may since have been backfilled."""
    service, _, runs, _ = _service()
    _generate(service)
    target = runs.docs[runs.key(PID, "2026-09")]["target"]
    assert all("rank" in row and "feature" in row for row in target)


# ------------------------------------------------ frozen strategy gate ------


def test_a_changed_strategy_hash_refuses_to_generate():
    service, recs, _, _ = _service()
    with pytest.raises(FrozenStrategyMismatch, match="forks a NEW portfolio"):
        _generate(service, strategy_params_hash="sha256:changed")
    assert recs.sets == {}, "nothing may be written when the gate fires"


# ------------------------------------------------------- regeneration -------


def test_regenerating_a_completed_period_needs_force():
    service, _, _, _ = _service()
    _generate(service)
    with pytest.raises(RebalanceNotDue):
        _generate(service)


def test_force_creates_a_new_revision_and_supersedes_the_previous():
    """Never edited: the previous set may already have been acted on."""
    service, recs, _, _ = _service()
    first = _generate(service).recommendation
    second = _generate(service, force=True).recommendation

    assert second.revision == 2
    assert second.supersedes == first.set_id
    assert recs.get_set(first.set_id).status == SetStatus.SUPERSEDED
    assert len(recs.sets) == 2, "both revisions remain readable"


def test_a_failed_run_is_reopened_rather_than_duplicated():
    """One document per period -- a second would defeat the uniqueness this
    collection exists to provide."""
    service, _, runs, _ = _service()
    runs.docs[runs.key(PID, "2026-09")] = {
        "_id": runs.key(PID, "2026-09"), "status": "failed", "attempt": 1,
    }
    _generate(service)
    assert len(runs.docs) == 1
    assert runs.docs[runs.key(PID, "2026-09")]["attempt"] == 2


# --------------------------------------------------------- lifecycle --------


def test_approval_and_rejection_are_recorded():
    service, recs, _, audit = _service()
    rec = _generate(service).recommendation

    service.approve(rec.set_id, now=NOW)
    assert recs.get_set(rec.set_id).status == SetStatus.APPROVED
    assert "recommendation.approved" in audit.actions()


def test_a_rejection_without_a_reason_is_refused():
    """The reasons are the dataset that measures whether discretion helps."""
    service, _, _, _ = _service()
    rec = _generate(service).recommendation
    with pytest.raises(ValueError, match="reason"):
        service.reject(rec.set_id, now=NOW, reason="")


def test_a_rejection_with_a_reason_is_stored():
    service, recs, _, audit = _service()
    rec = _generate(service).recommendation
    service.reject(rec.set_id, now=NOW, reason="insufficient funds this month")
    assert recs.get_set(rec.set_id).status == SetStatus.REJECTED
    assert audit.entries[-1]["after"]["note"] == "insufficient funds this month"


def test_a_superseded_set_cannot_be_approved():
    service, _, _, _ = _service()
    first = _generate(service).recommendation
    _generate(service, force=True)
    with pytest.raises(ValueError, match="cannot be"):
        service.approve(first.set_id, now=NOW)


def test_approving_an_unknown_set_raises():
    service, _, _, _ = _service()
    with pytest.raises(KeyError):
        service.approve("nope:2026-09:r1", now=NOW)


def test_stale_sets_expire():
    """A four-day-old signal is not a decision the strategy made today."""
    service, recs, _, audit = _service()
    rec = _generate(service).recommendation
    assert service.expire_stale(as_of=D(2026, 10, 10), now=NOW) == 1
    assert recs.get_set(rec.set_id).status == SetStatus.EXPIRED
    assert "recommendation.expired" in audit.actions()


def test_a_fresh_set_does_not_expire():
    service, _, _, _ = _service()
    _generate(service)
    assert service.expire_stale(as_of=D(2026, 10, 1), now=NOW) == 0
