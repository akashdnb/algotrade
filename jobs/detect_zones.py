#!/usr/bin/env python
"""Detect and persist multi-timeframe demand/supply zones."""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from algotrade.config import settings as settings_mod  # noqa: E402
from algotrade.config import validation  # noqa: E402
from algotrade.platform import clock  # noqa: E402
from algotrade.repositories import client  # noqa: E402
from algotrade.repositories.jobs import JobAlreadySucceeded, JobRunRepository  # noqa: E402
from algotrade.repositories.zones import ZoneRepository  # noqa: E402
from algotrade.services import panel as panel_service  # noqa: E402
from algotrade.zones.config import load_configs  # noqa: E402
from algotrade.zones.pipeline import ZONES_MIN_SESSIONS, run_for_universe  # noqa: E402

JOB_NAME = "zones_daily"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", type=date.fromisoformat)
    parser.add_argument("--through", type=date.fromisoformat)
    parser.add_argument("--backfill", type=int, default=5)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    settings = settings_mod.load()
    report = validation.validate(settings)
    blocking = [f for f in report.errors if not f.key.startswith(("TELEGRAM", "HEALTHCHECKS"))]
    if blocking:
        for finding in blocking:
            print(finding, file=sys.stderr)
        return 2

    configs = load_configs(REPO / "config/zones/zones_v1.yaml")
    through = args.date or args.through
    loaded = panel_service.load(
        curated_dir=settings.curated_dir,
        actions_dir=settings.data_dir / "raw" / "actions",
        through=through,
        sessions=ZONES_MIN_SESSIONS,
        min_sessions=ZONES_MIN_SESSIONS,
    )
    available = sorted(d for d in loaded.panel["date"].unique().to_list() if not args.through or d <= args.through)
    targets = [args.date] if args.date else available[-args.backfill:]
    if not targets:
        return 0

    db = None
    jobs = None
    repo = None
    if not args.dry_run:
        db = client.connect(settings.mongodb_uri, database=settings.mongodb_database)
        jobs = JobRunRepository(db)
        repo = ZoneRepository(db)
        if not args.force:
            missing = set(jobs.missing_dates(JOB_NAME, start=min(targets), end=max(targets)))
            targets = [day for day in targets if day in missing]

    config_hash = hashlib.sha256((REPO / "config/zones/zones_v1.yaml").read_bytes()).hexdigest()
    total = 0
    symbols_scanned = 0
    for day in targets:
        run_id = None
        now = clock.SystemClock().now()
        if jobs:
            try:
                run_id = jobs.claim(
                    JOB_NAME,
                    day,
                    now=now,
                    github_run_id=os.environ.get("GITHUB_RUN_ID"),
                    code_commit=os.environ.get("GITHUB_SHA", ""),
                    trigger=os.environ.get("GITHUB_EVENT_NAME", ""),
                    trigger_source=os.environ.get("GITHUB_WORKFLOW", ""),
                )
            except JobAlreadySucceeded:
                continue
        try:
            results = run_for_universe(loaded.panel, configs=configs, as_of_date=day)
            symbols_scanned += len(results)
            written = 0
            if repo:
                for result in results:
                    for timeframe, zones in result.zones.items():
                        repo.replace_for_symbol_timeframe(
                            result.symbol,
                            timeframe,
                            zones,
                            config_hash=config_hash,
                            computed_at=clock.SystemClock().now(),
                        )
                        written += len(zones)
            else:
                written = sum(len(zones) for result in results for zones in result.zones.values())
            total += written
            if jobs and run_id:
                jobs.complete(
                    run_id,
                    now=clock.SystemClock().now(),
                    result={"symbols_scanned": len(results), "zones_written": written},
                )
        except Exception as exc:
            if jobs and run_id:
                jobs.fail(
                    run_id,
                    now=clock.SystemClock().now(),
                    error={"type": type(exc).__name__, "message": str(exc)[:1000]},
                )
            raise

    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
            handle.write(f"zones_written={total}\nsymbols_scanned={symbols_scanned}\n")
    print(f"{total} zones across {symbols_scanned} symbols")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
