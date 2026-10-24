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
from algotrade.services import ingestion  # noqa: E402
from algotrade.services import panel as panel_service  # noqa: E402
from algotrade.zones.config import load_configs  # noqa: E402
from algotrade.zones.universes import UNIVERSES, members_for  # noqa: E402
from algotrade.zones.pipeline import ZONES_MIN_SESSIONS, run_for_symbol, run_for_universe  # noqa: E402

JOB_NAME = "zones_daily"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", type=date.fromisoformat, help="single as-of date; persistent writes are allowed only for the latest session")
    parser.add_argument("--through", type=date.fromisoformat)
    parser.add_argument("--backfill", type=int, default=5)
    parser.add_argument("--symbol", help="restrict detection to one symbol")
    parser.add_argument("--universe", choices=UNIVERSES, default="liquid", help="symbol universe; nifty500 uses the committed constituent snapshot (still subject to the liquidity floor)")
    parser.add_argument("--offline", action="store_true", help="detect and print zones without MongoDB writes")
    parser.add_argument("--skip-existing", action="store_true", help="skip business dates already recorded as successful")
    parser.add_argument("--force", action="store_true", help="deprecated no-op; recomputation is now the default")
    parser.add_argument("--dry-run", action="store_true", help="deprecated alias for --offline")
    return parser


def _print_offline_result(result) -> None:
    print(f"{result.symbol} | current_price={result.current_price}")
    for timeframe, zones in result.zones.items():
        print(f"  {timeframe.value}: {len(zones)} zones")
        for zone in zones:
            print(f"    {zone.zone_type.value} {zone.zone_low}-{zone.zone_high} status={zone.status.value} score={zone.strength_score} created={zone.created_at} id={zone.zone_id}")
    if result.confluence:
        print(f"  confluence: {len(result.confluence)} groups")


def _refresh_market_data(settings, *, through: date) -> dict:
    service = ingestion.nse_ingestion_service(sessions_dir=settings.sessions_dir, archive_dirs=settings.archive_dirs)
    last_stored = service.last_stored()
    missing = service.missing_sessions(through=through)
    print(f"market data: last_stored={last_stored or 'none'} missing_through={through} count={len(missing)}")
    result = service.ingest(through=through)
    summary = result.summary()
    print(f"market data: fetched={summary['fetched']} holidays={summary['holidays']} failed={summary['failed']}")
    if not result.ok:
        failed = ", ".join(str(item.day) for item in result.failed)
        raise RuntimeError(f"market-data refresh failed for session(s): {failed}")
    rebuild = panel_service.rebuild_curated(session_dirs=[*settings.archive_dirs, settings.sessions_dir], curated_dir=settings.curated_dir, through=through)
    print(f"curated panel: sessions={rebuild['sessions']} rows={rebuild['rows']} through={rebuild['last_session']} rejected={rebuild['rejected']}")
    return {"ingest": summary, "panel": rebuild}


def select_targets(sessions: list[date], *, as_of: date | None, through: date | None, backfill: int, offline: bool) -> list[date]:
    """Sessions to compute. Raises ValueError for an unsafe persistent run.

    The zones collection holds the *current* state, replaced wholesale per
    symbol/timeframe. Persisting any earlier as-of date would overwrite it with
    stale zones, and recomputing several days only ever leaves the last one
    behind, so persistent runs write the latest session alone.
    """
    available = [d for d in sessions if not through or d <= through]
    if as_of:
        if as_of not in available:
            raise ValueError(f"--date {as_of} is not present in the curated panel")
        targets = [as_of]
    else:
        targets = available[-backfill:]
    if targets and not offline:
        if targets[-1] != sessions[-1]:
            raise ValueError(
                f"historical detection (as of {targets[-1]}, latest session {sessions[-1]}) is read-only; use --offline"
            )
        targets = targets[-1:]
    return targets


def main() -> int:
    args = build_parser().parse_args()
    offline = args.offline or args.dry_run
    if args.backfill < 1:
        raise SystemExit("--backfill must be >= 1")
    settings = settings_mod.load()
    report = validation.validate(settings)
    blocking = [f for f in report.errors if not f.key.startswith(("TELEGRAM", "HEALTHCHECKS"))]
    if blocking:
        for finding in blocking:
            print(finding, file=sys.stderr)
        return 2

    # The data refresh is independent of the requested as-of date: truncating the
    # curated cache at a historical --through would drop the later sessions.
    _refresh_market_data(settings, through=clock.session_date(clock.SystemClock().now()))
    configs = load_configs(REPO / "config/zones/zones_v1.yaml")
    # Always load the complete panel (detection slices by as-of date itself), so
    # "latest session" below means the real latest and not a --through cut-off.
    loaded = panel_service.load(
        curated_dir=settings.curated_dir,
        actions_dir=settings.data_dir / "raw" / "actions",
        through=None,
        sessions=None,
        min_sessions=ZONES_MIN_SESSIONS,
    )
    try:
        targets = select_targets(
            sorted(loaded.panel["date"].unique().to_list()),
            as_of=args.date, through=args.through, backfill=args.backfill, offline=offline,
        )
    except ValueError as exc:
        raise SystemExit(str(exc))
    if not targets:
        return 0

    db = None
    jobs = None
    repo = None
    if not offline:
        db = client.connect(settings.mongodb_uri, database=settings.mongodb_database)
        repo = ZoneRepository(db)
        if args.skip_existing:
            jobs = JobRunRepository(db)
            missing = set(jobs.missing_dates(JOB_NAME, start=min(targets), end=max(targets)))
            targets = [day for day in targets if day in missing]

    members = members_for(args.universe)
    if members is not None:
        present = members & set(loaded.panel["symbol"].unique().to_list())
        print(f"universe {args.universe}: {len(members)} constituents, {len(present)} present in the panel")
    config_hash = hashlib.sha256((REPO / "config/zones/zones_v1.yaml").read_bytes()).hexdigest()
    total = 0
    symbols_scanned = 0
    for day in targets:
        run_id = None
        if jobs:
            now = clock.SystemClock().now()
            try:
                run_id = jobs.claim(JOB_NAME, day, now=now, github_run_id=os.environ.get("GITHUB_RUN_ID"), code_commit=os.environ.get("GITHUB_SHA", ""), trigger=os.environ.get("GITHUB_EVENT_NAME", ""), trigger_source=os.environ.get("GITHUB_WORKFLOW", ""))
            except JobAlreadySucceeded:
                continue
        try:
            if args.symbol:
                result = run_for_symbol(loaded.panel, args.symbol, configs=configs, as_of_date=day)
                results = [result] if result is not None else []
            else:
                results = run_for_universe(loaded.panel, configs=configs, as_of_date=day, members=members)
            symbols_scanned += len(results)
            written = 0
            if repo:
                for result in results:
                    for timeframe, zones in result.zones.items():
                        repo.replace_for_symbol_timeframe(result.symbol, timeframe, zones, config_hash=config_hash, computed_at=clock.SystemClock().now())
                        written += len(zones)
            else:
                written = sum(len(zones) for result in results for zones in result.zones.values())
                if offline:
                    for result in results:
                        _print_offline_result(result)
            total += written
            if jobs and run_id:
                jobs.complete(run_id, now=clock.SystemClock().now(), result={"symbols_scanned": len(results), "zones_written": written})
        except Exception as exc:
            if jobs and run_id:
                jobs.fail(run_id, now=clock.SystemClock().now(), error={"type": type(exc).__name__, "message": str(exc)[:1000]})
            raise

    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
            handle.write(f"zones_written={total}\nsymbols_scanned={symbols_scanned}\n")
    print(f"{total} zones across {symbols_scanned} symbols")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
