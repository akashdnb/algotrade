#!/usr/bin/env python
"""Generate and store the momentum ranking for a business date.

Reconciliation-driven, like ingestion: it computes every session that has
market data but no stored signal, so a missed run repairs itself.

    python -m jobs.generate_signals                       # today (IST)
    python -m jobs.generate_signals --date 2026-08-20
    python -m jobs.generate_signals --backfill 5          # last 5 sessions
    python -m jobs.generate_signals --dry-run

Signals belong to the STRATEGY, not a portfolio: `_id` is
`{strategy_version}:{date}`, so every portfolio on `momentum_v1` reads one
document rather than each storing 30 identical rows.

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
from algotrade.repositories.jobs import JobRunRepository  # noqa: E402
from algotrade.repositories.signals import SignalRepository  # noqa: E402
from algotrade.services import panel as panel_service  # noqa: E402
from algotrade.services.signals import (  # noqa: E402
    SignalService,
    SignalUnavailable,
    StrategySpec,
    compare,
)

JOB_NAME = "generate_signals"
log = logging.getLogger("jobs.generate_signals")


def _day(text: str) -> date:
    return datetime.strptime(text, "%Y-%m-%d").date()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", type=_day, default=None,
                    help="session to rank (default: latest available)")
    ap.add_argument("--backfill", type=int, default=1,
                    help="how many recent sessions to ensure are ranked")
    ap.add_argument("--rebuild-panel", action="store_true",
                    help="rebuild the curated cache from session CSVs first")
    ap.add_argument("--force", action="store_true",
                    help="recompute even if a signal is already stored")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    s = settings_mod.load()
    logging.basicConfig(level=getattr(logging, s.log_level, logging.INFO),
                        format="%(levelname)-7s %(name)s  %(message)s")

    report = validation.validate(s)
    blocking = [f for f in report.errors if not f.key.startswith(
        ("TELEGRAM", "HEALTHCHECKS", "DEFAULT_PORTFOLIO_ID"))]
    if blocking:
        for finding in blocking:
            print(finding, file=sys.stderr)
        return 2

    spec = StrategySpec.from_yaml(s.strategy_config)
    service = SignalService(spec)

    if args.rebuild_panel:
        session_dirs = ([s.sessions_dir] if s.sessions_dir.exists() else []) + s.archive_dirs
        print("rebuilding curated panel ...", flush=True)
        info = panel_service.rebuild_curated(
            session_dirs=session_dirs, curated_dir=s.curated_dir, through=args.date
        )
        print(f"  {info['sessions']:,} sessions, {info['rows']:,} rows")

    print("loading panel ...", flush=True)
    try:
        loaded = panel_service.load(
            curated_dir=s.curated_dir,
            actions_dir=s.data_dir / "raw" / "actions",
            through=args.date,
        )
    except panel_service.InsufficientHistory as exc:
        print(f"cannot compute a signal: {exc}", file=sys.stderr)
        return 1

    print(f"  {loaded.sessions} sessions, {loaded.first_session} .. "
          f"{loaded.last_session}  ({loaded.manifest_hash})")

    sessions = sorted(loaded.panel["date"].unique().to_list())
    targets = [args.date] if args.date else sessions[-args.backfill:]
    targets = [d for d in targets if d in set(sessions)]
    if not targets:
        print("no matching session in the panel", file=sys.stderr)
        return 1

    db = client.connect(s.mongodb_uri, database=s.mongodb_database)
    signals_repo = SignalRepository(db)
    jobs_repo = JobRunRepository(db)
    now = clock.SystemClock().now()

    # Reconciliation: only sessions with no stored signal, unless forced.
    pending = [
        d for d in targets
        if args.force or signals_repo.as_of(spec.version, d) is None
    ]
    if not pending:
        print(f"signals already stored for {targets[0]}..{targets[-1]}; nothing to do")
        return 0

    print(f"\ncomputing {len(pending)} session(s): "
          f"{', '.join(str(d) for d in pending)}")

    failures = 0
    for day in pending:
        try:
            signals = service.generate(
                loaded.panel, as_of=day,
                panel_manifest_hash=loaded.manifest_hash, now=now,
            )
        except SignalUnavailable as exc:
            print(f"  {day}  FAILED: {exc}")
            failures += 1
            continue

        previous = signals_repo.as_of(spec.version, _previous(sessions, day))
        delta = compare(previous, signals)

        top = signals.top(10)
        print(f"\n  {day}  universe {signals.universe_size}  "
              f"stored {len(signals.rows)} ranks")
        for row in top:
            print(f"    {row.rank:>3}  {row.symbol:<14}"
                  f"{row.feature:>9.1%}"
                  f"{('  Rs ' + f'{row.close:,.2f}') if row.close else ''}")
        if not delta["first_signal"]:
            if delta["entered"]:
                print(f"    entered: {', '.join(delta['entered'][:8])}")
            if delta["exited"]:
                print(f"    exited : {', '.join(delta['exited'][:8])}")

        if not args.dry_run:
            created = signals_repo.save(signals)
            log.info("%s signal for %s", "stored" if created else "replaced", day)

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return 0

    last = pending[-1]
    if failures == 0:
        try:
            run_id = jobs_repo.claim(JOB_NAME, last, now=now,
                                     github_run_id=s.github_run_id or None,
                                     code_commit=s.code_commit)
            jobs_repo.complete(run_id, now=now, result={
                "sessions": len(pending), "failures": failures,
                "last_session": str(last),
            })
        except Exception as exc:  # noqa: BLE001 -- already-succeeded is fine
            log.info("job ledger: %s", exc)

    if (out := __import__("os").environ.get("GITHUB_OUTPUT")):
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"signals_written={len(pending) - failures}\n")
            fh.write(f"signal_date={last}\n")
            fh.write(f"failures={failures}\n")

    print(f"\n{len(pending) - failures} signal(s) stored, {failures} failed")
    return 0 if failures == 0 else 1


def _previous(sessions: list[date], day: date) -> date:
    """The session before `day`, for the change comparison."""
    index = sessions.index(day)
    return sessions[index - 1] if index > 0 else day


if __name__ == "__main__":
    raise SystemExit(main())
