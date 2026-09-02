"""Turn a rebalance decision into a stored, notifiable recommendation set.

Owns the lifecycle. Two rules that matter more than they look:

* **Generation is idempotent per (portfolio, period).** `rebalance_runs._id` is
  `portfolio:period`, so a duplicated workflow run cannot claim the same period
  twice -- enforced by the database, not by a check that could race.
* **A set is never edited.** Regenerating creates a NEW revision and marks the
  previous one superseded. Both stay readable forever, because a recommendation
  that may already have been acted on is a financial record.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from algotrade.domain import money
from algotrade.domain.models import (
    Holding,
    RecommendationSet,
    SetStatus,
    SignalSet,
)
from algotrade.platform import clock
from algotrade.repositories.base import AlreadyExists
from algotrade.services.rebalance import RebalanceDecision, RebalanceService

log = logging.getLogger(__name__)


class RebalanceNotDue(RuntimeError):
    """This period already has a completed run, and --force was not given."""


def closed_periods(sessions: list[date]) -> list[str]:
    """The periods whose last trading session is known to have passed.

    A period is closed once a session in a **later** period exists. The most
    recent period is therefore never closed: its last session cannot be known
    to be the last while it is still the latest session there is.

    That is precisely what the scheduled due-ness check used to get wrong. It
    derived the period from the latest session, so it always asked about the
    month in progress, and `is_due` always -- correctly -- answered "may not be
    the final session yet". Twelve simulated months produced twelve refusals,
    and the only real rebalance this book has had was triggered by hand.
    """
    periods = sorted({clock.period_of(day) for day in sessions})
    return periods[:-1]


class FrozenStrategyMismatch(RuntimeError):
    """The strategy config no longer hashes to what the portfolio was created
    with. A frozen strategy that can be edited unnoticed is not frozen."""


@dataclass(frozen=True, slots=True)
class GenerateResult:
    recommendation: RecommendationSet
    decision: RebalanceDecision
    created: bool
    run_id: str


@dataclass(frozen=True, slots=True)
class DueCheck:
    """One portfolio's answer, carrying the strategy it was judged against.

    `strategy_version` is on the result rather than assumed by the caller
    because the whole point of the check is that two portfolios may be judged
    against different signal calendars.
    """

    portfolio_id: str
    strategy_version: str
    period: str
    due: bool
    reason: str
    #: Closed periods between the last completed rebalance and `period`, which
    #: this run does NOT go back and perform. Reported so a gap is legible
    #: rather than merely absent -- see `due_period`.
    missed: tuple[str, ...] = ()


class RecommendationService:
    def __init__(
        self,
        *,
        recommendations,
        rebalance_runs,
        rebalance: RebalanceService,
        audit=None,
        ttl_days: int = 3,
    ) -> None:
        self._recs = recommendations
        self._runs = rebalance_runs
        self._rebalance = rebalance
        self._audit = audit
        self._ttl_days = ttl_days

    # ------------------------------------------------------------- due? -----

    def is_due(
        self, *, portfolio_id: str, period: str, sessions: list[date], as_of: date
    ) -> tuple[bool, str]:
        """Whether a rebalance should run now. Reads STATE, never "cron fired".

        Due when the period's last trading session has passed and no completed
        run exists for it. That is what makes a dropped monthly schedule
        self-healing: the daily check notices and runs it.
        """
        if self._runs.is_complete(portfolio_id, period):
            return False, f"{period} already completed"

        in_period = [d for d in sessions if clock.period_of(d) == period]
        if not in_period:
            return False, f"no trading sessions recorded in {period}"

        last_session = max(in_period)
        if as_of < last_session:
            return False, (
                f"{period}'s last session so far is {last_session}; "
                f"waiting for the month to close"
            )
        # The month has more sessions coming only if today is still inside it.
        if clock.period_of(as_of) == period and as_of == last_session:
            following = [d for d in sessions if d > as_of]
            if not following:
                return False, (
                    f"{as_of} may not be {period}'s final session yet"
                )
        return True, f"{period} is complete and has no rebalance run"

    def due_period(
        self, portfolio_id: str, sessions: list[date]
    ) -> tuple[str | None, str, tuple[str, ...]]:
        """The period to rebalance now, why not, and the months skipped.

        **The latest closed period, and only that one.** If September and
        October are both missed and it is November, this returns October;
        September is never rebalanced and the ledger goes on saying so.

        The alternative -- working forwards from the oldest unrebalanced month
        -- would not trade stale data, because `generate` always uses the
        current signals and `period` is only the bookkeeping key. It would do
        something worse: file a November run under `portfolio:2026-09` and mark
        it completed, so the gap disappears and the record asserts a September
        rebalance that never happened. Same principle as append-only
        executions -- a correction is recorded, never written over the top of
        what was believed at the time.

        This only PROPOSES the month to ask about; `is_due` still decides, so
        there remains exactly one place that answers the question -- including
        "already completed", which it reports better than a second check here
        could.

        Returns:
            `(period, reason, missed)`. `period` is None only when no month has
            closed at all, and `reason` says so. `missed` runs oldest-first.
        """
        closed = closed_periods(sessions)
        if not closed:
            return None, "no month has closed yet", ()

        latest = closed[-1]
        if self._runs.is_complete(portfolio_id, latest):
            return latest, "", ()

        # Back to the most recent month that DID rebalance. Bounded by the
        # signal calendar, so a book that has never rebalanced reports every
        # closed month -- which is the honest answer for one.
        missed: list[str] = []
        for period in reversed(closed[:-1]):
            if self._runs.is_complete(portfolio_id, period):
                break
            missed.append(period)
        return latest, "", tuple(reversed(missed))

    def due_portfolios(
        self, portfolios: list[dict], *, signals, period: str | None = None,
        force: bool = False,
    ) -> list[DueCheck]:
        """Which of these portfolios need a rebalance -- the Actions matrix.

        **Each portfolio is judged against its OWN strategy's signal
        calendar**, read from the portfolio document rather than from whichever
        strategy config the process happens to have loaded. A `meanrev_v1` book
        asked whether momentum's month has closed is not a slightly wrong
        answer, it is an answer to a different question -- and unlike the
        frozen-hash gate, which refuses loudly, that one would pass silently.

        Signals are looked up once per strategy version, not once per
        portfolio: several portfolios on one strategy share a calendar, which
        is exactly why `signals` is keyed by `(strategy_version, session)`.

        A portfolio whose strategy has no stored signals is reported as not
        due, with the reason, rather than raising -- one unbuilt strategy must
        not remove every other portfolio from the month's run.
        """
        calendars: dict[str, tuple[date | None, list[date]]] = {}
        out: list[DueCheck] = []

        for portfolio in portfolios:
            portfolio_id = portfolio["_id"]
            version = portfolio.get("strategy_version") or ""

            if version not in calendars:
                latest = signals.latest(version) if version else None
                calendars[version] = (
                    (latest.date if latest else None),
                    (signals.dates(version) if latest else []),
                )
            as_of, sessions = calendars[version]

            if as_of is None:
                out.append(DueCheck(
                    portfolio_id=portfolio_id, strategy_version=version,
                    period=period or "", due=False,
                    reason=f"no signals stored for {version or 'an unset strategy'}",
                ))
                continue

            # Resolved against THIS strategy's own calendar, so two strategies
            # whose panels end on different days each get their own period
            # rather than one borrowing the other's.
            missed: tuple[str, ...] = ()
            if period:
                resolved, why = period, ""
            else:
                resolved, why, missed = self.due_period(portfolio_id, sessions)

            if resolved is None:
                out.append(DueCheck(
                    portfolio_id=portfolio_id, strategy_version=version,
                    period="", due=bool(force),
                    reason="forced" if force else why,
                ))
                continue

            # `is_due` stays the single place that decides. `due_period` only
            # proposes which month to ask about; asking is still its job.
            due, reason = self.is_due(
                portfolio_id=portfolio_id, period=resolved,
                sessions=sessions, as_of=as_of,
            )
            if force:
                due, reason = True, "forced"
            out.append(DueCheck(
                portfolio_id=portfolio_id, strategy_version=version,
                period=resolved, due=due, reason=reason,
                missed=missed if due else (),
            ))
        return out

    # --------------------------------------------------------- generate -----

    def generate(
        self,
        *,
        portfolio_id: str,
        period: str,
        holdings: list[Holding],
        signals: SignalSet,
        cash,
        prices,
        as_of: date,
        now: datetime,
        panel_manifest_hash: str,
        strategy_params_hash: str,
        portfolio_params_hash: str,
        code_commit: str = "",
        cost_model_hash: str = "",
        force: bool = False,
    ) -> GenerateResult:
        """Produce and store a recommendation set for one period.

        Raises:
            FrozenStrategyMismatch: the config no longer matches the portfolio.
            RebalanceNotDue: already completed and `force` is False.
        """
        if strategy_params_hash != portfolio_params_hash:
            raise FrozenStrategyMismatch(
                f"{portfolio_id} was created with strategy params "
                f"{portfolio_params_hash!r}; the config now hashes to "
                f"{strategy_params_hash!r}. A parameter change forks a NEW "
                f"portfolio -- it must never silently alter this one."
            )

        completed = self._runs.is_complete(portfolio_id, period)
        if completed and not force:
            raise RebalanceNotDue(f"{portfolio_id}:{period} is already completed")

        decision = self._rebalance.decide(
            holdings=holdings, signals=signals, cash=cash,
            prices=prices, as_of=as_of,
        )
        plan = self._rebalance.plan(holdings=holdings, decision=decision)

        fingerprint = {
            "panel_manifest_hash": panel_manifest_hash,
            "strategy_params_hash": strategy_params_hash,
            "cost_model_hash": cost_model_hash,
            "code_commit": code_commit,
            "last_session": as_of.isoformat(),
            "universe_size": signals.universe_size,
        }

        run_id = self._runs.key(portfolio_id, period)
        try:
            self._runs.start(
                portfolio_id, period, now=now, as_of_session=as_of,
                fingerprint=fingerprint, triggered_by="manual" if force else "schedule",
            )
        except AlreadyExists:
            if not force:
                # A run document exists but is not completed -- a previous
                # attempt failed part-way. Reopen it rather than creating a
                # second document for the same period.
                self._runs.retry(run_id, now=now)
            else:
                self._runs.retry(run_id, now=now)

        book_value = money.sum_rupees(
            h.value_at(prices.get(h.symbol, money.ZERO)) for h in holdings
        )
        self._runs.record_target(
            run_id,
            target=[
                {"rank": decision.ranks.get(s, 0), "symbol": s,
                 "quantity": q,
                 "feature": next((r.feature for r in signals.rows if r.symbol == s), 0.0)}
                for s, q in sorted(decision.target_quantities.items(),
                                   key=lambda kv: decision.ranks.get(kv[0], 10**9))
            ],
            current=[{"symbol": h.symbol, "quantity": h.quantity} for h in holdings],
            turnover=plan.turnover(book_value=money.rupees(book_value + cash)),
        )

        revision = self._recs.next_revision(portfolio_id, period)
        previous = self._recs.latest_for_period(portfolio_id, period)

        recommendation = RecommendationSet(
            set_id=self._recs.key(portfolio_id, period, revision),
            portfolio_id=portfolio_id,
            rebalance_run_id=run_id,
            period=period,
            revision=revision,
            supersedes=previous.set_id if previous else None,
            status=SetStatus.PENDING,
            generated_at=now,
            as_of_session=as_of,
            expires_on=as_of + timedelta(days=self._ttl_days),
            reference_nav=money.rupees(book_value + cash),
            reference_cash=cash,
            items=plan.items,
            holds=sorted(decision.holding),
            code_commit=code_commit,
            strategy_params_hash=strategy_params_hash,
        )
        self._recs.save(recommendation)

        if previous is not None:
            # Never edited -- superseded, so both remain readable forever.
            self._recs.mark_superseded(previous.set_id)

        self._runs.complete(run_id, now=now, recommendation_set_id=recommendation.set_id)

        if self._audit:
            self._audit.record(
                now=now, actor="system", action="recommendation.generated",
                entity_type="recommendation", entity_id=recommendation.set_id,
                portfolio_id=portfolio_id,
                after={
                    "items": len(recommendation.items),
                    "holds": len(recommendation.holds),
                    "entering": decision.entering,
                    "leaving": decision.leaving,
                },
                context={"code_commit": code_commit, "run_id": run_id},
            )

        log.info("generated %s: %d trades, %d holds",
                 recommendation.set_id, len(recommendation.items),
                 len(recommendation.holds))
        return GenerateResult(recommendation, decision, created=True, run_id=run_id)

    # ---------------------------------------------------------- lifecycle ---

    def approve(self, set_id: str, *, now: datetime, note: str = "") -> bool:
        return self._transition(set_id, SetStatus.APPROVED, now=now, note=note)

    def reject(self, set_id: str, *, now: datetime, reason: str) -> bool:
        if not reason:
            raise ValueError(
                "a rejection needs a reason -- the reasons are the dataset "
                "that measures whether discretion helps or hurts (§9)"
            )
        return self._transition(set_id, SetStatus.REJECTED, now=now, note=reason)

    def _transition(self, set_id: str, status: SetStatus, *, now, note: str) -> bool:
        current = self._recs.get_set(set_id)
        if current is None:
            raise KeyError(f"no recommendation set {set_id!r}")
        if current.status in (SetStatus.SUPERSEDED, SetStatus.EXPIRED,
                              SetStatus.COMPLETED):
            raise ValueError(
                f"{set_id} is {current.status} and cannot be {status}"
            )
        ok = self._recs.update(set_id, {"status": status.value})
        if ok and self._audit:
            self._audit.record(
                now=now, actor="user", action=f"recommendation.{status.value}",
                entity_type="recommendation", entity_id=set_id,
                portfolio_id=current.portfolio_id,
                before={"status": current.status.value},
                after={"status": status.value, "note": note},
            )
        return ok

    def expire_stale(self, *, as_of: date, now: datetime) -> int:
        """Retire sets past their expiry. A four-day-old signal is not a
        decision the strategy made today."""
        stale = self._recs.expiring(as_of=as_of)
        for doc in stale:
            self._recs.update(doc["_id"], {"status": SetStatus.EXPIRED.value})
            if self._audit:
                self._audit.record(
                    now=now, actor="system", action="recommendation.expired",
                    entity_type="recommendation", entity_id=doc["_id"],
                    portfolio_id=doc.get("portfolio_id"),
                    before={"status": doc.get("status")},
                    after={"status": SetStatus.EXPIRED.value},
                    severity="warning",
                )
        return len(stale)
