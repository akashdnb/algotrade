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


# ------------------------------------------------- the matrix, per strategy ---
#
# `due_portfolios` decides which portfolios enter the monthly Actions matrix.
# The property under test is that each is judged against ITS OWN strategy's
# signal calendar: unlike the frozen-hash gate, which refuses loudly, judging a
# mean-reversion book against momentum's month would pass silently.


class FakeSignals:
    """Signal calendars keyed by strategy version, like the real repository."""

    def __init__(self, calendars: dict[str, list]) -> None:
        self._calendars = calendars
        self.asked = []

    def latest(self, strategy_version: str):
        self.asked.append(strategy_version)
        dates = self._calendars.get(strategy_version) or []
        if not dates:
            return None
        return _signals(["A", "B", "C"], as_of=max(dates))

    def dates(self, strategy_version: str) -> list:
        return sorted(self._calendars.get(strategy_version) or [])


#: Spans into October on purpose. `is_due` calls a month closed only once a
#: session in the NEXT one exists -- the last session of a month cannot be
#: known to be the last while it is still the latest session there is.
MOMENTUM = [D(2026, 9, 28), D(2026, 9, 29), D(2026, 9, 30), D(2026, 10, 1)]
SEPTEMBER = "2026-09"


def _portfolio(portfolio_id: str, version: str = "momentum_v1") -> dict:
    return {"_id": portfolio_id, "strategy_version": version}


def test_every_active_portfolio_is_checked():
    service, _, _, _ = _service()
    checks = service.due_portfolios(
        [_portfolio(PID), _portfolio("shadow_momentum_v1_001")],
        signals=FakeSignals({"momentum_v1": MOMENTUM}), period=SEPTEMBER,
    )
    assert [c.portfolio_id for c in checks] == [PID, "shadow_momentum_v1_001"]
    assert all(c.due for c in checks)


def test_each_portfolio_is_judged_against_its_own_strategy():
    """The bug this method exists to prevent.

    Momentum's September has closed; mean-reversion's has not. Judging both
    against momentum's calendar would put a meanrev book into the matrix a
    month early, with nothing in the logs to say the wrong calendar was used.
    """
    service, _, _, _ = _service()
    checks = service.due_portfolios(
        [_portfolio(PID), _portfolio("paper_meanrev_v1_001", "meanrev_v1")],
        signals=FakeSignals({
            "momentum_v1": MOMENTUM,
            "meanrev_v1": [D(2026, 9, 15)],      # mid-month: not closed
        }),
        period=SEPTEMBER,
    )
    by_id = {c.portfolio_id: c for c in checks}
    assert by_id[PID].due is True
    assert by_id["paper_meanrev_v1_001"].due is False
    assert by_id["paper_meanrev_v1_001"].strategy_version == "meanrev_v1"


def test_each_strategy_gets_its_own_period():
    """Two panels ending in different months must not share one period."""
    service, _, _, _ = _service()
    checks = service.due_portfolios(
        [_portfolio(PID), _portfolio("paper_meanrev_v1_001", "meanrev_v1")],
        signals=FakeSignals({"momentum_v1": MOMENTUM,
                             "meanrev_v1": [D(2026, 8, 29)]}),
    )
    assert {c.period for c in checks} == {"2026-10", "2026-08"}


def test_a_calendar_is_read_once_per_strategy_not_once_per_portfolio():
    """Several portfolios share a strategy; the signals collection is keyed
    that way precisely so the lookup is not repeated."""
    signals = FakeSignals({"momentum_v1": MOMENTUM})
    service, _, _, _ = _service()
    service.due_portfolios(
        [_portfolio(PID), _portfolio("shadow_momentum_v1_001"),
         _portfolio("paper_momentum_v1_002")],
        signals=signals,
    )
    assert signals.asked == ["momentum_v1"]


def test_a_strategy_with_no_signals_does_not_remove_the_others():
    service, _, _, _ = _service()
    checks = service.due_portfolios(
        [_portfolio(PID), _portfolio("paper_meanrev_v1_001", "meanrev_v1")],
        signals=FakeSignals({"momentum_v1": MOMENTUM}), period=SEPTEMBER,
    )
    by_id = {c.portfolio_id: c for c in checks}
    assert by_id[PID].due is True
    assert by_id["paper_meanrev_v1_001"].due is False
    assert by_id["paper_meanrev_v1_001"].reason.startswith("no signals stored")


def test_a_completed_period_is_not_due_again():
    service, _, runs, _ = _service()
    runs.insert({"_id": f"{PID}:2026-09", "status": "completed"})
    checks = service.due_portfolios(
        [_portfolio(PID)], signals=FakeSignals({"momentum_v1": MOMENTUM}),
        period=SEPTEMBER)
    assert checks[0].due is False
    assert "already completed" in checks[0].reason


def test_force_overrides_a_completed_period():
    service, _, runs, _ = _service()
    runs.insert({"_id": f"{PID}:2026-09", "status": "completed"})
    checks = service.due_portfolios(
        [_portfolio(PID)], signals=FakeSignals({"momentum_v1": MOMENTUM}),
        force=True)
    assert checks[0].due is True
    assert checks[0].reason == "forced"


def test_an_explicit_period_overrides_every_strategys_own():
    service, _, _, _ = _service()
    checks = service.due_portfolios(
        [_portfolio(PID), _portfolio("paper_meanrev_v1_001", "meanrev_v1")],
        signals=FakeSignals({"momentum_v1": MOMENTUM,
                             "meanrev_v1": [D(2026, 8, 29)]}),
        period="2026-07",
    )
    assert {c.period for c in checks} == {"2026-07"}


def test_a_portfolio_with_no_strategy_version_is_reported_not_raised():
    """A malformed document must not take the whole month's run down."""
    service, _, _, _ = _service()
    checks = service.due_portfolios(
        [{"_id": "paper_momentum_v1_009"}],
        signals=FakeSignals({"momentum_v1": MOMENTUM}),
    )
    assert checks[0].due is False
    assert "unset strategy" in checks[0].reason
