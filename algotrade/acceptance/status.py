"""Monitor the 10-session unattended acceptance test.

    python -m algotrade.acceptance.status
    python -m algotrade.acceptance.status --json
    python -m algotrade.acceptance.status --sessions 10

**Read-only.** Nothing here writes, so checking on the test cannot affect the
result it measures.

The gate is not "ten green days". It is ten sessions with **zero manual
intervention**, plus at least one deliberately-injected failure that the
system recovered from unaided (criterion 5 in `docs/acceptance/`). Ten quiet
sessions only prove the happy path; the recovery is the only evidence for the
property actually being claimed.

Exit codes: 0 on track or passed, 1 something needs attention, 2 config error.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from algotrade.config import settings as settings_mod  # noqa: E402
from algotrade.config import validation  # noqa: E402
from algotrade.platform import clock  # noqa: E402
from algotrade.repositories import client  # noqa: E402
from algotrade.repositories.cash import CashRepository  # noqa: E402
from algotrade.repositories.executions import ExecutionRepository  # noqa: E402
from algotrade.repositories.holdings import HoldingRepository  # noqa: E402
from algotrade.repositories.jobs import JobRunRepository  # noqa: E402
from algotrade.repositories.notifications import NotificationRepository  # noqa: E402
from algotrade.repositories.portfolios import PortfolioRepository  # noqa: E402
from algotrade.repositories.rebalance_runs import RebalanceRunRepository  # noqa: E402
from algotrade.repositories.recommendations import RecommendationRepository  # noqa: E402
from algotrade.repositories.signals import SignalRepository  # noqa: E402
from algotrade.repositories.snapshots import SnapshotRepository  # noqa: E402
from algotrade.services.holdings import HoldingsService  # noqa: E402
from algotrade.services.reconciliation import ReconciliationService  # noqa: E402
from algotrade.services.signals import StrategySpec  # noqa: E402

TARGET_SESSIONS = 10
STALE_SESSIONS = 2

#: The only `trigger` value that demonstrates self-healing. A
#: `workflow_dispatch` repair is a person fixing it, which is the opposite.
SCHEDULED = "schedule"


@dataclass
class Check:
    name: str
    status: str          # PASS | WARN | FAIL | INFO
    detail: str

    def __str__(self) -> str:
        return f"{self.status:<5} {self.name:<22} {self.detail}"


def _mark(ok: bool, warn: bool = False) -> str:
    return "PASS" if ok else ("WARN" if warn else "FAIL")


def collect(s, *, target: int = TARGET_SESSIONS) -> tuple[list[Check], dict]:
    db = client.connect(s.mongodb_uri, database=s.mongodb_database)
    portfolio_id = s.default_portfolio_id
    today = clock.SystemClock().today()

    jobs = JobRunRepository(db)
    snapshots = SnapshotRepository(db)
    signals_repo = SignalRepository(db)
    executions = ExecutionRepository(db)
    holdings_repo = HoldingRepository(db)
    cash = CashRepository(db)
    recs = RecommendationRepository(db)
    portfolios = PortfolioRepository(db)

    spec = StrategySpec.from_yaml(s.strategy_config)
    checks: list[Check] = []
    facts: dict = {"portfolio_id": portfolio_id, "as_of": str(today)}

    # --- 1. session ledger ---------------------------------------------------
    snapshot_dates = snapshots.dates(portfolio_id)
    completed = len(snapshot_dates)
    facts["sessions_completed"] = completed
    facts["sessions_target"] = target
    checks.append(Check(
        "sessions", _mark(completed >= target, warn=completed > 0),
        f"{completed} / {target}"
        + (f"   {snapshot_dates[0]} .. {snapshot_dates[-1]}" if snapshot_dates else ""),
    ))

    # Failed / recovered / missed, from the job ledger. These are what
    # distinguish "ten quiet days" from "ten days that included a failure the
    # system handled" -- the second is the stronger result.
    failed_runs = jobs.find({"status": "failed", "synthetic": {"$ne": True}},
                            limit=500)
    failed_dates = {(r["job_name"], r["business_date"]) for r in failed_runs}
    recovered = {
        (job, day) for job, day in failed_dates
        if jobs.already_succeeded(job, date.fromisoformat(day))
    }
    unrecovered = failed_dates - recovered
    facts["sessions_failed"] = len(failed_dates)
    facts["sessions_recovered"] = len(recovered)
    facts["sessions_unrecovered"] = len(unrecovered)

    checks.append(Check(
        "failed sessions",
        _mark(not unrecovered, warn=bool(failed_dates)),
        f"{len(failed_dates)} failed, {len(recovered)} recovered, "
        f"{len(unrecovered)} outstanding",
    ))

    # Missed: expected weekday sessions inside the window with no snapshot.
    missed: list[date] = []
    if snapshot_dates:
        expected = clock.weekdays_between(snapshot_dates[0], snapshot_dates[-1])
        missed = [d for d in expected if d not in set(snapshot_dates)]
    facts["sessions_missed"] = len(missed)
    checks.append(Check(
        "missed sessions", _mark(not missed, warn=bool(missed)),
        "none" if not missed
        else f"{len(missed)}: {', '.join(str(d) for d in missed[:4])}"
             + ("  (may include NSE holidays)" if missed else ""),
    ))

    # --- 2. last successful run ---------------------------------------------
    last_ingest = jobs.last_success("ingest_session")
    last_signal = jobs.last_success("generate_signals")
    facts["last_ingest"] = last_ingest["business_date"] if last_ingest else None
    facts["last_signal"] = last_signal["business_date"] if last_signal else None
    checks.append(Check(
        "last ingest", _mark(last_ingest is not None),
        last_ingest["business_date"] if last_ingest else "never",
    ))
    checks.append(Check(
        "last signal", _mark(last_signal is not None),
        last_signal["business_date"] if last_signal else "never",
    ))

    # --- 3. data freshness ---------------------------------------------------
    signal = signals_repo.latest(spec.version)
    behind = 0
    if signal:
        behind = len([d for d in clock.weekdays_between(signal.date, today)
                      if d > signal.date])
    facts["sessions_behind"] = behind
    checks.append(Check(
        "data freshness",
        _mark(behind < STALE_SESSIONS, warn=behind == STALE_SESSIONS),
        f"signal {signal.date if signal else 'none'}, {behind} session(s) behind",
    ))

    # --- 4. last rebalance ---------------------------------------------------
    run = RebalanceRunRepository(db).latest(portfolio_id)
    latest_rec = None
    if run:
        latest_rec = recs.latest_for_period(portfolio_id, run["period"])
    facts["last_rebalance"] = run["period"] if run else None
    checks.append(Check(
        "last rebalance", "INFO",
        f"{run['period']} ({run['status']})" if run else "none yet",
    ))
    outstanding = recs.outstanding(portfolio_id)
    oldest_age = 0
    for doc in outstanding:
        if doc.get("as_of_session"):
            oldest_age = max(
                oldest_age, (today - date.fromisoformat(doc["as_of_session"])).days)
    facts["outstanding_sets"] = len(outstanding)
    checks.append(Check(
        "recommendations",
        _mark(oldest_age < 3, warn=oldest_age >= 3),
        f"{len(outstanding)} outstanding"
        + (f", oldest {oldest_age}d" if outstanding else ""),
    ))

    # --- 5. reconciliation ---------------------------------------------------
    consistency = ReconciliationService(
        portfolios=portfolios,
        holdings_service=HoldingsService(
            executions=executions, holdings=holdings_repo),
        cash=cash, executions=executions, recommendations=recs,
        snapshots=snapshots,
    ).check(portfolio_id)
    facts["reconciliation_ok"] = consistency.ok
    facts["reconciliation_errors"] = [f.message for f in consistency.errors]
    checks.append(Check(
        "reconciliation", _mark(consistency.ok),
        "all checks pass" if consistency.ok
        else f"{len(consistency.errors)} failure(s): {consistency.errors[0].message}",
    ))

    # --- 6. telegram ----------------------------------------------------------
    failures = NotificationRepository(db).failures(since=today - timedelta(days=7))
    sent = NotificationRepository(db).count({"status": "sent"})
    facts["notifications_sent"] = sent
    facts["notification_failures_7d"] = len(failures)
    checks.append(Check(
        "telegram", _mark(not failures, warn=bool(failures)),
        f"{sent} sent, {len(failures)} failed in 7d",
    ))

    # --- 7. healthchecks ------------------------------------------------------
    checks.append(Check(
        "healthchecks", _mark(s.healthchecks_configured),
        "configured" if s.healthchecks_configured
        else "NOT configured -- total failure would be silent",
    ))

    # --- 7b. stale-data events ------------------------------------------------
    # Counted from the audit log rather than inferred, so a transient staleness
    # that later cleared is still on the record.
    from algotrade.repositories.audit import AuditRepository

    stale_events = [
        e for e in AuditRepository(db).problems(
            since=clock.SystemClock().now() - timedelta(days=30), limit=200)
        if "stale" in e.get("action", "").lower()
        or "stale" in str(e.get("after", "")).lower()
    ]
    facts["stale_data_events"] = len(stale_events)
    checks.append(Check(
        "stale data events", _mark(not stale_events, warn=bool(stale_events)),
        f"{len(stale_events)} in 30 days",
    ))

    # --- 8. the criterion that actually matters -------------------------------
    # Ten quiet sessions prove the happy path. Recovering from an injected
    # failure unaided is the only evidence for the property being claimed.
    recovered = _recovered_from_failure(jobs)
    facts["recovered_from_failure"] = recovered
    checks.append(Check(
        "failure recovery", _mark(recovered, warn=not recovered),
        "a failed run was followed by an unaided success" if recovered
        else "NOT yet demonstrated -- inject a failure and watch it self-heal",
    ))

    return checks, facts


def verdict(checks: list[Check], facts: dict) -> tuple[str, str]:
    """PASS / WARN / FAIL, and why.

    PASS requires the gate's substance, not just the absence of red: the
    target session count, a clean reconciliation, and a demonstrated recovery.
    Ten quiet sessions without a recovery is WARN, because the property being
    claimed has not been shown.
    """
    if any(c.status == "FAIL" for c in checks):
        failing = ", ".join(c.name for c in checks if c.status == "FAIL")
        return "FAIL", f"failing checks: {failing}"

    if not facts.get("reconciliation_ok"):
        return "FAIL", "reconciliation is not passing"

    reached = facts.get("sessions_completed", 0) >= facts.get("sessions_target", 10)
    recovered = facts.get("recovered_from_failure", False)

    if reached and recovered:
        return "PASS", (
            f"{facts['sessions_completed']} sessions, reconciliation clean, "
            f"self-healing demonstrated"
        )
    if not reached:
        return "WARN", (
            f"{facts.get('sessions_completed', 0)} of "
            f"{facts.get('sessions_target', 10)} sessions completed"
        )
    return "WARN", (
        "session count reached but self-healing has not been demonstrated -- "
        "inject a failure and let the next run repair it"
    )


def _recovered_from_failure(jobs: JobRunRepository) -> bool:
    """Whether any job failed and a later SCHEDULED run for the same date won.

    That pairing is the self-healing property: nothing remembered the failure,
    the next run simply asked what was missing and filled it.

    Two exclusions, both added 2026-08-30 after this returned True on evidence
    that proved nothing (see `verify_recovery` for the full account):

    * `synthetic` rows, hand-injected by a rehearsal, reproduce this exact
      shape. Counting them makes the check unfalsifiable.
    * A success triggered by `workflow_dispatch` is someone pressing Run
      workflow. That is a person repairing the system, not the system
      repairing itself.

    Deliberately still cheaper than `verify_recovery`: this is a status line,
    and that command is the authority. It must not be *laxer*, though, or the
    two disagree and the friendlier answer is the one people read.
    """
    failed = jobs.find({"status": "failed", "synthetic": {"$ne": True}}, limit=200)
    for run in failed:
        attempts = jobs.find({
            "job_name": run["job_name"],
            "business_date": run["business_date"],
            "status": "success",
            "synthetic": {"$ne": True},
        })
        if any(a.get("trigger") == SCHEDULED for a in attempts):
            return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sessions", type=int, default=TARGET_SESSIONS)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    s = settings_mod.load()
    report = validation.validate(s)
    if report.errors:
        for finding in report.errors:
            print(finding, file=sys.stderr)
        return 2

    checks, facts = collect(s, target=args.sessions)

    if args.json:
        outcome, why = verdict(checks, facts)
        print(json.dumps({
            "verdict": outcome, "reason": why,
            "facts": facts, "checks": [asdict(c) for c in checks],
        }, indent=2, default=str))
    else:
        print(f"acceptance test -- {facts['portfolio_id']} -- {facts['as_of']}\n")
        for check in checks:
            print(f"  {check}")
        print()

    outcome, why = verdict(checks, facts)
    if args.json:
        return 0 if outcome == "PASS" else 1
    print(f"{outcome}  -- {why}")
    if outcome == "PASS":
        print("\nREADY FOR PAPER CAPITAL "
              "(see docs/acceptance/10-session-gate.md for what that does "
              "and does not mean)")
    return 0 if outcome != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
