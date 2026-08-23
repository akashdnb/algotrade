#!/usr/bin/env python
"""Fetch missing market-data sessions. The first step of the daily workflow.

Reconciliation-driven: fetches every session missing up to the target date, not
just "today". A dropped, delayed, or disabled schedule therefore repairs itself
on the next run (design §0.1).

    python -m jobs.ingest_session                       # through today (IST)
    python -m jobs.ingest_session --through 2026-10-02
    python -m jobs.ingest_session --bootstrap --days 730
    python -m jobs.ingest_session --check               # report gaps, fetch nothing

Exit codes: 0 success or nothing to do, 1 at least one session failed,
2 configuration error.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from algotrade.config import settings as settings_mod  # noqa: E402
from algotrade.config import validation  # noqa: E402
from algotrade.platform import clock  # noqa: E402
from algotrade.repositories import client  # noqa: E402
from algotrade.repositories.jobs import JobAlreadySucceeded, JobRunRepository  # noqa: E402
from algotrade.services import ingestion  # noqa: E402

JOB_NAME = "ingest_session"
log = logging.getLogger("jobs.ingest_session")


def _day(text: str) -> date:
    return datetime.strptime(text, "%Y-%m-%d").date()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--through", type=_day, default=None,
                    help="last session to consider (default: today, IST)")
    ap.add_argument("--days", type=int, default=ingestion.DEFAULT_LOOKBACK_DAYS,
                    help="how far back to look for gaps")
    ap.add_argument("--bootstrap", action="store_true",
                    help="seed an empty store; implies a long --days")
    ap.add_argument("--check", action="store_true",
                    help="report missing sessions, fetch nothing")
    ap.add_argument("--no-db", action="store_true",
                    help="skip the job_runs ledger (local use)")
    args = ap.parse_args()

    s = settings_mod.load()
    logging.basicConfig(
        level=getattr(logging, s.log_level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
    )

    report = validation.validate(s)
    blocking = [f for f in report.errors if not f.key.startswith(
        ("TELEGRAM", "HEALTHCHECKS", "DEFAULT_PORTFOLIO_ID"))]
    if blocking:
        for finding in blocking:
            print(finding, file=sys.stderr)
        return 2

    through = args.through or clock.SystemClock().today()
    lookback = max(args.days, 900) if args.bootstrap else args.days

    service = ingestion.nse_ingestion_service(
        sessions_dir=s.sessions_dir,
        archive_dirs=s.archive_dirs,
    )

    missing = service.missing_sessions(through=through, lookback_days=lookback)
    last = service.last_stored()
    print(f"last stored session: {last or 'none'}")
    print(f"missing through {through}: {len(missing)}")
    for day in missing[:20]:
        print(f"  {day}  ({day:%a})")
    if len(missing) > 20:
        print(f"  ... +{len(missing) - 20} more")

    if args.check:
        return 0

    if not missing:
        # Record the no-op as a SUCCESS rather than returning silently. The job
        # ran and achieved the desired state; that is what the ledger should
        # say. Returning early left "last successful ingest" empty forever on a
        # perfectly current system, which read as a failure.
        print("nothing to do -- already current")
        if not args.no_db:
            db = client.connect(s.mongodb_uri, database=s.mongodb_database)
            jobs_repo = JobRunRepository(db)
            now = datetime.now(tz=clock.IST).astimezone()
            try:
                run_id = jobs_repo.claim(
                    JOB_NAME, through, now=now,
                    github_run_id=s.github_run_id or None,
                    code_commit=s.code_commit)
                jobs_repo.complete(run_id, now=now, result={
                    "considered": 0, "fetched": 0, "holidays": 0, "failed": 0,
                    "note": "already current",
                })
            except JobAlreadySucceeded:
                pass          # already recorded for this date; nothing to add
        return 0

    # The ledger is what makes the gap query answerable tomorrow, so it is
    # written even when the fetch partially fails.
    jobs_repo = None
    run_id = None
    if not args.no_db:
        db = client.connect(s.mongodb_uri, database=s.mongodb_database)
        jobs_repo = JobRunRepository(db)
        now = datetime.now(tz=clock.IST).astimezone()
        try:
            run_id = jobs_repo.claim(
                JOB_NAME, through, now=now,
                github_run_id=s.github_run_id or None, code_commit=s.code_commit,
            )
        except JobAlreadySucceeded:
            # Not an error: the exactly-once guarantee firing. But a later date
            # may still be missing, so only exit if there is genuinely nothing
            # to do -- which `missing` has already told us there is.
            print(f"{JOB_NAME} already succeeded for {through}; "
                  f"continuing to repair {len(missing)} older gap(s)")

    result = service.ingest(through=through, lookback_days=lookback)
    summary = result.summary()
    print(f"\nfetched={summary['fetched']} holidays={summary['holidays']} "
          f"failed={summary['failed']}")
    for outcome in result.outcomes:
        if outcome.status != "fetched":
            print(f"  {outcome.day}  {outcome.status}: {outcome.detail}")

    if jobs_repo and run_id:
        now = datetime.now(tz=clock.IST).astimezone()
        if result.ok:
            jobs_repo.complete(run_id, now=now, result=summary)
        else:
            jobs_repo.fail(run_id, now=now, error={
                "type": "IngestFailure",
                "message": f"{summary['failed']} session(s) failed",
                "dates": [str(o.day) for o in result.failed],
            })

    # GitHub Actions consumes this to decide whether to commit.
    if (out := __import__("os").environ.get("GITHUB_OUTPUT")):
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"new_sessions={summary['fetched']}\n")
            fh.write(f"failed={summary['failed']}\n")
            fh.write(f"last_session={service.last_stored() or ''}\n")

    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
