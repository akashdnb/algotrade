"""Prove that reconciliation-driven scheduling actually self-heals.

    python -m algotrade.acceptance.verify_recovery
    python -m algotrade.acceptance.verify_recovery --json

Every other check in this system confirms the happy path. This one confirms
the property the whole scheduling design is built on: **that nothing needs to
remember a failure, because the next run asks what is missing.**

A recovery is only counted when all four hold for the same business date:

1. a run **failed** for that date,
2. a **later** run succeeded for it,
3. that later run was triggered by **`schedule`**, and
4. the date has a snapshot and the portfolio **reconciles** afterwards.

Three out of four is not enough. A job that succeeds on retry but leaves an
inconsistent book has not recovered -- it has papered over the failure, which
is worse than staying broken visibly. Neither is a repair that happened
because someone pressed "Run workflow": that is a human healing the system,
which is the opposite of the claim.

**Condition 3 was added 2026-08-30, and the docstring used to assert it
without checking it.** Two other things were wrong at the same time, and all
three flattered the result:

* Only `ingest_session` ever recorded a *failed* run. `generate_signals`
  wrote to the ledger only when nothing had failed, and `value_portfolio`
  never wrote at all -- so the failures this is meant to find were mostly
  unrecordable.
* Each daily run claims a *new* business date, so a failure on Monday and the
  backfill that repaired it on Tuesday were never the same date. Genuine
  self-healing was invisible: 2026-08-21 has a snapshot (its data was
  backfilled unaided on the 24th) and no job_run at all.
* The only failed/succeeded pair in the ledger was a hand-injected rehearsal
  for `value_portfolio 2026-08-19` -- a job that writes no rows -- which
  reproduced this function's exact shape and made it report success. Rows
  marked `synthetic` are now excluded.

Read-only. Exit 0 when recovery is demonstrated, 1 when it is not.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from algotrade.config import settings as settings_mod  # noqa: E402
from algotrade.config import validation  # noqa: E402
from algotrade.repositories import client  # noqa: E402
from algotrade.repositories.cash import CashRepository  # noqa: E402
from algotrade.repositories.executions import ExecutionRepository  # noqa: E402
from algotrade.repositories.corporate_actions import CorporateActionRepository
from algotrade.repositories.holdings import HoldingRepository  # noqa: E402
from algotrade.repositories.jobs import JobRunRepository  # noqa: E402
from algotrade.repositories.portfolios import PortfolioRepository  # noqa: E402
from algotrade.repositories.recommendations import RecommendationRepository  # noqa: E402
from algotrade.repositories.snapshots import SnapshotRepository  # noqa: E402
from algotrade.services.holdings import HoldingsService  # noqa: E402
from algotrade.services.reconciliation import ReconciliationService  # noqa: E402


#: What the backup trigger stamps on a dispatch it originated. Anything else
#: -- including a human calling the same endpoint -- is not self-healing.
CRON_SOURCE = "vercel-cron"


@dataclass
class Recovery:
    job_name: str
    business_date: str
    failed_attempt: int
    failed_at: str
    recovered_attempt: int
    recovered_at: str
    error: str = ""
    #: Whether a snapshot exists for the date, i.e. the gap was actually filled
    #: rather than merely re-attempted.
    gap_filled: bool = False
    #: What triggered the run that repaired it. `schedule` is the only value
    #: that demonstrates the property; `workflow_dispatch` means a human
    #: pressed the button, which is a repair, not self-healing. Empty for runs
    #: recorded before this was captured -- treated as unknown, not as proof.
    recovered_by: str = ""
    #: For `repository_dispatch`, what the dispatcher declared itself to be.
    recovered_source: str = ""

    @property
    def unaided(self) -> bool:
        """No human involved.

        `schedule` is GitHub's own timer. `repository_dispatch` is ambiguous on
        its own -- the backup trigger and a human curling the same endpoint
        produce the identical event -- so it counts only when the dispatcher
        stamped it as coming from the cron, which it does only for requests
        carrying Vercel's own cron headers.
        """
        if self.recovered_by == "schedule":
            return True
        return (self.recovered_by == "repository_dispatch"
                and self.recovered_source == CRON_SOURCE)

    @property
    def attempts(self) -> int:
        return self.recovered_attempt - self.failed_attempt


@dataclass
class RecoveryReport:
    portfolio_id: str
    recoveries: list[Recovery] = field(default_factory=list)
    failures_never_recovered: list[dict] = field(default_factory=list)
    reconciliation_ok: bool = False
    reconciliation_errors: list[str] = field(default_factory=list)

    @property
    def demonstrated(self) -> bool:
        """All conditions, for at least one date, on ONE recovery.

        `any(...)` over the whole list would let a gap-filled recovery and a
        separately unaided one add up to a proof neither of them is.
        """
        return bool(
            self.reconciliation_ok
            and any(r.gap_filled and r.unaided for r in self.recoveries)
        )

    @property
    def verdict(self) -> str:
        if self.demonstrated:
            return "SELF-HEALING DEMONSTRATED"
        if self.recoveries and not self.reconciliation_ok:
            return "RECOVERED BUT INCONSISTENT"
        if any(r.gap_filled and not r.unaided for r in self.recoveries):
            return "REPAIRED, BUT NOT UNAIDED"
        if self.failures_never_recovered:
            return "FAILURES NOT RECOVERED"
        return "NOT YET DEMONSTRATED"

    def to_dict(self) -> dict:
        return {
            "portfolio_id": self.portfolio_id,
            "verdict": self.verdict,
            "demonstrated": self.demonstrated,
            "recoveries": [asdict(r) for r in self.recoveries],
            "failures_never_recovered": self.failures_never_recovered,
            "reconciliation_ok": self.reconciliation_ok,
            "reconciliation_errors": self.reconciliation_errors,
        }


def analyse(db, portfolio_id: str) -> RecoveryReport:
    jobs = JobRunRepository(db)
    snapshots = SnapshotRepository(db)
    report = RecoveryReport(portfolio_id=portfolio_id)

    snapshot_dates = set(snapshots.dates(portfolio_id))
    # `synthetic` excludes rows written by a rehearsal rather than by a job.
    # A fabricated failed/succeeded pair reproduces the exact shape this
    # function looks for, so leaving them in makes the check unfalsifiable.
    failed_runs = jobs.find({"status": "failed", "synthetic": {"$ne": True}},
                            sort=[("business_date", 1)], limit=500)

    for run in failed_runs:
        business_date = run["business_date"]
        job_name = run["job_name"]

        # Was there a LATER successful attempt for the same business date?
        attempts = jobs.find({
            "job_name": job_name, "business_date": business_date,
            "synthetic": {"$ne": True},
        }, sort=[("attempt", 1)])
        success = next(
            (a for a in attempts
             if a.get("status") == "success"
             and a.get("attempt", 0) > run.get("attempt", 0)),
            None,
        )
        if success is None:
            report.failures_never_recovered.append({
                "job_name": job_name,
                "business_date": business_date,
                "attempt": run.get("attempt"),
                "error": (run.get("error") or {}).get("message", ""),
            })
            continue

        report.recoveries.append(Recovery(
            job_name=job_name,
            business_date=business_date,
            failed_attempt=run.get("attempt", 0),
            failed_at=str(run.get("started_at", "")),
            recovered_attempt=success.get("attempt", 0),
            recovered_at=str(success.get("finished_at", "")),
            error=(run.get("error") or {}).get("message", ""),
            # The gap is only truly filled if the date has a snapshot.
            gap_filled=date.fromisoformat(business_date) in snapshot_dates,
            recovered_by=success.get("trigger") or "",
            recovered_source=success.get("trigger_source") or "",
        ))

    executions = ExecutionRepository(db)
    holdings = HoldingRepository(db)
    consistency = ReconciliationService(
        portfolios=PortfolioRepository(db),
        holdings_service=HoldingsService(
            executions=executions, holdings=holdings,
            corporate_actions=CorporateActionRepository(db)),
        cash=CashRepository(db), executions=executions,
        recommendations=RecommendationRepository(db), snapshots=snapshots,
    ).check(portfolio_id)
    report.reconciliation_ok = consistency.ok
    report.reconciliation_errors = [f.message for f in consistency.errors]
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--portfolio-id", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    s = settings_mod.load()
    problems = validation.validate(s)
    if problems.errors:
        for finding in problems.errors:
            print(finding, file=sys.stderr)
        return 2

    portfolio_id = args.portfolio_id or s.default_portfolio_id
    db = client.connect(s.mongodb_uri, database=s.mongodb_database)
    report = analyse(db, portfolio_id)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, default=str))
        return 0 if report.demonstrated else 1

    print(f"self-healing verification -- {portfolio_id}\n")

    if report.recoveries:
        print(f"{len(report.recoveries)} recovery(ies) found:\n")
        for r in report.recoveries:
            mark = "OK  " if (r.gap_filled and r.unaided) else "PART"
            print(f"  [{mark}] {r.job_name} {r.business_date}")
            print(f"         failed attempt {r.failed_attempt}"
                  + (f": {r.error}" if r.error else ""))
            by = r.recovered_by or "an unrecorded trigger"
            if r.recovered_source:
                by += f" ({r.recovered_source})"
            print(f"         recovered on attempt {r.recovered_attempt} by {by}")
            if not r.gap_filled:
                print("         but no snapshot for that date -- the gap was "
                      "re-attempted, not filled")
            if not r.unaided:
                print("         and this does NOT count: only a `schedule` run "
                      "demonstrates self-healing")
    else:
        print("no failed-then-succeeded pairs in the job ledger")
        print("  inject a failure and let the next scheduled run repair it;")
        print("  see docs/operations/runbook.md")

    if report.failures_never_recovered:
        print(f"\n{len(report.failures_never_recovered)} failure(s) never recovered:")
        for f in report.failures_never_recovered[:5]:
            print(f"  {f['job_name']} {f['business_date']}: {f['error']}")

    print(f"\nreconciliation after recovery: "
          f"{'PASS' if report.reconciliation_ok else 'FAIL'}")
    for err in report.reconciliation_errors[:3]:
        print(f"  {err}")

    print(f"\n{report.verdict}")
    if not report.demonstrated:
        print("\nAll four must hold, for the SAME business date:")
        print("  1. a run failed")
        print("  2. a later run succeeded for that date")
        print("  3. that later run was triggered by `schedule`, or by the")
        print("     backup cron -- not by hand")
        print("  4. the date has a snapshot, and the portfolio reconciles")
        print("A retry that leaves an inconsistent book has not recovered -- "
              "it has papered over the failure. A repair someone pressed a "
              "button for is not self-healing either.")
    return 0 if report.demonstrated else 1


if __name__ == "__main__":
    raise SystemExit(main())
