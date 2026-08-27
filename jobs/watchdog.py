#!/usr/bin/env python
"""Detect the failures the other workflows cannot report (design §10).

A workflow that fails can alert. A workflow that never STARTS cannot -- and
over a multi-year horizon that is the likelier failure: a dropped schedule, a
repository disabled for inactivity, a deleted workflow. This job independently
queries state and reports what is missing.

It cannot detect its own failure. That is what healthchecks.io is for.

    python -m jobs.watchdog              # report only
    python -m jobs.watchdog --alert      # report and send Telegram on breach
    python -m jobs.watchdog --quiet      # report, never send

Exit codes: 0 healthy or warnings only, 1 at least one critical, 2 config error.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from algotrade.config import settings as settings_mod  # noqa: E402
from algotrade.config import validation  # noqa: E402
from algotrade.platform import clock  # noqa: E402
from algotrade.repositories import client  # noqa: E402
from algotrade.repositories.jobs import JobRunRepository  # noqa: E402
from algotrade.repositories.notifications import NotificationRepository  # noqa: E402
from algotrade.repositories.recommendations import RecommendationRepository  # noqa: E402
from algotrade.services import ingestion  # noqa: E402

# Thresholds from design §10.1.
STALE_WARN_SESSIONS = 1
STALE_CRITICAL_SESSIONS = 2
JOB_FAIL_CRITICAL_CONSECUTIVE = 2
UNEXECUTED_WARN_DAYS = 2
UNEXECUTED_CRITICAL_DAYS = 3
NOTIFY_FAIL_WARN = 1
NOTIFY_FAIL_CRITICAL = 3
STORAGE_WARN_MB = 400
STORAGE_CRITICAL_MB = 480
STUCK_RUN_HOURS = 6


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    level: str          # ok | warn | critical
    detail: str

    def __str__(self) -> str:
        mark = {"ok": "OK  ", "warn": "WARN", "critical": "CRIT"}[self.level]
        return f"[{mark}] {self.name}: {self.detail}"


def check_data_freshness(s, today: date) -> Check:
    service = ingestion.nse_ingestion_service(
        sessions_dir=s.sessions_dir, archive_dirs=s.archive_dirs
    )
    last = service.last_stored()
    if last is None:
        return Check("market_data", "critical", "no sessions on disk at all")

    # `through` is the last COMPLETED trading day, not today: the watchdog runs
    # at 09:30 IST, hours before daily.yml ingests today's session at 20:00 IST.
    # Checking `through=today` would count today itself as missing every single
    # morning -- a guaranteed false WARN, not a real staleness signal. Same
    # reasoning check_daily_job already applies by excluding the latest date.
    missing = service.missing_sessions(through=clock.previous_weekday(today))
    behind = len(missing)
    if behind >= STALE_CRITICAL_SESSIONS:
        return Check("market_data", "critical",
                     f"last session {last}, {behind} trading day(s) behind "
                     f"(missing {missing[0]}..{missing[-1]}) -- valuation BLOCKED")
    if behind >= STALE_WARN_SESSIONS:
        return Check("market_data", "warn",
                     f"last session {last}, {behind} trading day(s) behind")
    return Check("market_data", "ok", f"current through {last}")


def check_daily_job(jobs: JobRunRepository, today: date) -> Check:
    recent = clock.weekdays_between(today - timedelta(days=10), today)
    done = jobs.succeeded_dates("ingest_session", since=today - timedelta(days=10))
    # The most recent date is excluded: today's run may legitimately not have
    # happened yet when the watchdog runs at 09:30 IST.
    consecutive = 0
    for day in reversed(recent[:-1]):
        if day in done:
            break
        consecutive += 1

    last = jobs.last_success("ingest_session")
    last_str = last["business_date"] if last else "never"
    if consecutive >= JOB_FAIL_CRITICAL_CONSECUTIVE:
        return Check("daily_job", "critical",
                     f"{consecutive} consecutive sessions with no success "
                     f"(last: {last_str})")
    if consecutive >= 1:
        return Check("daily_job", "warn",
                     f"{consecutive} session without a success (last: {last_str})")
    return Check("daily_job", "ok", f"last success {last_str}")


def check_stuck_runs(jobs: JobRunRepository, now: datetime) -> Check:
    stuck = jobs.stuck(now=now, older_than_hours=STUCK_RUN_HOURS)
    if not stuck:
        return Check("stuck_runs", "ok", "none")
    names = ", ".join(f"{r['job_name']}:{r['business_date']}" for r in stuck[:3])
    return Check("stuck_runs", "warn",
                 f"{len(stuck)} run(s) still 'running' after {STUCK_RUN_HOURS}h: {names}")


def check_unexecuted(recs: RecommendationRepository, today: date) -> Check:
    outstanding = recs.outstanding()
    if not outstanding:
        return Check("recommendations", "ok", "nothing outstanding")

    worst_age, worst_id = 0, ""
    for doc in outstanding:
        as_of = doc.get("as_of_session")
        if not as_of:
            continue
        age = (today - date.fromisoformat(as_of)).days
        if age > worst_age:
            worst_age, worst_id = age, doc["_id"]

    level = (
        "critical" if worst_age >= UNEXECUTED_CRITICAL_DAYS
        else "warn" if worst_age >= UNEXECUTED_WARN_DAYS
        else "ok"
    )
    return Check("recommendations", level,
                 f"{len(outstanding)} set(s) outstanding; oldest {worst_age} day(s) "
                 f"({worst_id})")


def check_notifications(notifications: NotificationRepository, today: date) -> Check:
    """A failed send is invisible by definition -- you cannot be told that you
    cannot be told (§10.5)."""
    failures = notifications.failures(since=today - timedelta(days=1))
    if len(failures) >= NOTIFY_FAIL_CRITICAL:
        return Check("notifications", "critical",
                     f"{len(failures)} failed sends in 24h")
    if len(failures) >= NOTIFY_FAIL_WARN:
        return Check("notifications", "warn",
                     f"{len(failures)} failed send(s) in 24h")
    return Check("notifications", "ok", "no failures in 24h")


def check_storage(db) -> Check:
    usage = client.stats(db)
    mb = usage["storage_bytes"] / 1e6
    level = (
        "critical" if mb >= STORAGE_CRITICAL_MB
        else "warn" if mb >= STORAGE_WARN_MB
        else "ok"
    )
    return Check("mongodb_storage", level,
                 f"{mb:.1f} MB of 512 MB (M0), {usage['objects']:,} documents")


def check_schema(db) -> Check:
    problems = client.verify_schema(db)
    if problems:
        return Check("mongodb_schema", "critical",
                     f"{len(problems)} discrepancies; first: {problems[0]}")
    return Check("mongodb_schema", "ok", "matches schema.py")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--alert", action="store_true", help="send Telegram on breach")
    ap.add_argument("--quiet", action="store_true", help="never send")
    ap.add_argument("--date", type=lambda s: datetime.strptime(s, "%Y-%m-%d").date())
    args = ap.parse_args()

    s = settings_mod.load()
    report = validation.validate(s)
    if not report.ok:
        for finding in report.errors:
            print(finding, file=sys.stderr)
        return 2

    today = args.date or clock.SystemClock().today()
    now = clock.SystemClock().now()
    db = client.connect(s.mongodb_uri, database=s.mongodb_database)

    jobs = JobRunRepository(db)
    checks = [
        check_data_freshness(s, today),
        check_daily_job(jobs, today),
        check_stuck_runs(jobs, now),
        check_unexecuted(RecommendationRepository(db), today),
        check_notifications(NotificationRepository(db), today),
        check_schema(db),
        check_storage(db),
    ]

    print(f"watchdog · {today} · environment={s.environment}\n")
    for check in checks:
        print(check)

    criticals = [c for c in checks if c.level == "critical"]
    warnings = [c for c in checks if c.level == "warn"]
    print(f"\n{len(criticals)} critical, {len(warnings)} warning")

    should_alert = (criticals or warnings) and args.alert and not args.quiet
    if should_alert and s.telegram_configured:
        from algotrade.platform.telegram import TelegramClient, escape
        from algotrade.services.notification import NotificationService

        status = "\U0001F534" if criticals else "\U0001F7E0"
        body = "\n".join([
            f"{status} *Watchdog* · {escape(str(today))}",
            "",
            *[escape(str(c)) for c in criticals + warnings],
        ])
        NotificationService(
            repository=NotificationRepository(db),
            client=TelegramClient(s.telegram_bot_token, s.telegram_chat_id),
            chat_id=s.telegram_chat_id,
        ).send(kind="system_health", text=body, now=now, business_date=today)

    return 1 if criticals else 0


if __name__ == "__main__":
    raise SystemExit(main())
