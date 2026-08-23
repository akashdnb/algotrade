"""Monitor the 10-session unattended acceptance test.

    python -m algotrade.acceptance.status
    python -m algotrade.acceptance.status --json
    python -m algotrade.acceptance.status --sessions 10

**Read-only.** Nothing here writes, so checking on the test cannot affect the
result it measures.

The gate is not "ten green days". It is ten sessions with **zero manual
intervention**, plus at least one deliberately-injected failure that the
system recovered from unaided (criterion 6 in the Phase 2 design). Ten quiet
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

    # --- 1. sessions completed ---------------------------------------------
    snapshot_dates = snapshots.dates(portfolio_id)
    completed = len(snapshot_dates)
    facts["sessions_completed"] = completed
    facts["sessions_target"] = target
    checks.append(Check(
        "sessions", _mark(completed >= target, warn=completed > 0),
        f"{completed} / {target}"
        + (f"   {snapshot_dates[0]} .. {snapshot_dates[-1]}" if snapshot_dates else ""),
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


def _recovered_from_failure(jobs: JobRunRepository) -> bool:
    """Whether any job failed and a later run for the same date succeeded.

    That pairing is the self-healing property: nothing remembered the failure,
    the next run simply asked what was missing and filled it.
    """
    failed = jobs.find({"status": "failed"}, limit=200)
    if not failed:
        return False
    for run in failed:
        if jobs.already_succeeded(
            run["job_name"], date.fromisoformat(run["business_date"])
        ):
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
        print(json.dumps(
            {"facts": facts, "checks": [asdict(c) for c in checks]},
            indent=2, default=str))
    else:
        print(f"acceptance test -- {facts['portfolio_id']} -- {facts['as_of']}\n")
        for check in checks:
            print(f"  {check}")
        print()

    failures = [c for c in checks if c.status == "FAIL"]
    warnings = [c for c in checks if c.status == "WARN"]
    if not args.json:
        if failures:
            print(f"{len(failures)} check(s) need attention")
        elif warnings:
            print(f"on track -- {len(warnings)} warning(s)")
        else:
            print("all checks pass")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
