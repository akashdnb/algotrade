#!/usr/bin/env python
"""Mark the book to market and write the daily snapshot.

Reconciliation-driven: `--backfill N` writes every missing snapshot in the
window, so a skipped day repairs itself. Snapshots are keyed by
(portfolio, date) and upserted, making re-runs no-ops.

    python -m jobs.value_portfolio
    python -m jobs.value_portfolio --all-active --backfill 5
    python -m jobs.value_portfolio --date 2026-08-20
    python -m jobs.value_portfolio --backfill 10
    python -m jobs.value_portfolio --reconcile-only

`--all-active` values every active portfolio from a single panel load, which is
how a shadow book gets a NAV without paying the slow step twice.

Weekends and holidays are valued using the most recent session's closes --
correct rather than approximate, since no trading happened.

Exit codes: 0 valued and consistent, 1 inconsistent or failed, 2 config.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from algotrade.config import settings as settings_mod  # noqa: E402
from algotrade.config import validation  # noqa: E402
from algotrade.domain import money  # noqa: E402
from algotrade.platform import clock  # noqa: E402
from algotrade.repositories import client  # noqa: E402
from algotrade.repositories.audit import AuditRepository  # noqa: E402
from algotrade.repositories.cash import CashRepository  # noqa: E402
from algotrade.repositories.executions import ExecutionRepository  # noqa: E402
from algotrade.repositories.holdings import HoldingRepository  # noqa: E402
from algotrade.repositories.jobs import (  # noqa: E402
    JobAlreadySucceeded,
    JobRunRepository,
)
from algotrade.repositories.portfolios import PortfolioRepository  # noqa: E402
from algotrade.repositories.recommendations import RecommendationRepository  # noqa: E402
from algotrade.repositories.snapshots import SnapshotRepository  # noqa: E402
from algotrade.services import panel as panel_service  # noqa: E402
from algotrade.services import valuation as valuation_service  # noqa: E402
from algotrade.services.holdings import HoldingsService  # noqa: E402
from algotrade.services.performance import PerformanceService  # noqa: E402
from algotrade.services.reconciliation import ReconciliationService  # noqa: E402

JOB_NAME = "value_portfolio"
log = logging.getLogger("jobs.value_portfolio")


def _day(text: str) -> date:
    return datetime.strptime(text, "%Y-%m-%d").date()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--portfolio-id", default=None)
    ap.add_argument("--all-active", action="store_true",
                    help="value every active portfolio, sharing one panel load")
    ap.add_argument("--date", type=_day, default=None)
    ap.add_argument("--backfill", type=int, default=1,
                    help="ensure the last N sessions are valued")
    ap.add_argument("--reconcile-only", action="store_true")
    ap.add_argument("--rebuild-holdings", action="store_true",
                    help="rebuild the derived book before valuing")
    args = ap.parse_args()

    s = settings_mod.load()
    logging.basicConfig(level=getattr(logging, s.log_level, logging.INFO),
                        format="%(levelname)-7s %(message)s")

    report = validation.validate(s)
    blocking = [f for f in report.errors
                if not f.key.startswith(("TELEGRAM", "HEALTHCHECKS"))]
    if blocking:
        for finding in blocking:
            print(finding, file=sys.stderr)
        return 2

    db = client.connect(s.mongodb_uri, database=s.mongodb_database)
    portfolios = PortfolioRepository(db)

    if args.all_active:
        portfolio_ids = portfolios.active_ids()
        if not portfolio_ids:
            print("no active portfolios", file=sys.stderr)
            return 1
    else:
        portfolio_ids = [args.portfolio_id or s.default_portfolio_id]

    for portfolio_id in portfolio_ids:
        try:
            validation.guard_portfolio(s, portfolio_id)
        except settings_mod.ConfigError as exc:
            print(exc, file=sys.stderr)
            return 2
        if portfolios.get(portfolio_id) is None:
            print(f"no portfolio {portfolio_id!r}", file=sys.stderr)
            return 1

    executions = ExecutionRepository(db)
    holdings_repo = HoldingRepository(db)
    cash = CashRepository(db)
    snapshots = SnapshotRepository(db)
    audit = AuditRepository(db)
    now = clock.SystemClock().now()

    holdings_service = HoldingsService(
        executions=executions, holdings=holdings_repo, audit=audit)
    consistency = ReconciliationService(
        portfolios=portfolios, holdings_service=holdings_service, cash=cash,
        executions=executions, recommendations=RecommendationRepository(db),
        snapshots=snapshots)

    for portfolio_id in portfolio_ids:
        if args.rebuild_holdings:
            rebuild = holdings_service.rebuild(portfolio_id, now=now)
            print(f"{portfolio_id}: rebuilt {len(rebuild.holdings)} position(s) "
                  f"from {rebuild.executions_replayed} execution(s)")

    if args.reconcile_only:
        ok = True
        for portfolio_id in portfolio_ids:
            result = consistency.check(portfolio_id)
            print(f"\n{portfolio_id}")
            for finding in result.findings:
                print(f"  {finding}")
            ok = ok and result.ok
        return 0 if ok else 1

    # ONE panel load for every portfolio. It is the slow step here, it does not
    # depend on the book, and loading it per portfolio would make a second
    # portfolio cost as much as the first for no reason.
    print("loading panel ...", flush=True)
    loaded = panel_service.load(
        curated_dir=s.curated_dir, actions_dir=s.data_dir / "raw" / "actions",
        through=args.date,
    )
    sessions = sorted(loaded.panel["date"].unique().to_list())
    targets = [args.date] if args.date else sessions[-args.backfill:]

    written, consistent = 0, True
    for portfolio_id in portfolio_ids:
        if len(portfolio_ids) > 1:
            print(f"\n=== {portfolio_id} " + "=" * max(0, 52 - len(portfolio_id)))
        try:
            count, ok = _value_one(
                portfolio_id, s=s, db=db, portfolios=portfolios,
                holdings_repo=holdings_repo, snapshots=snapshots, cash=cash,
                consistency=consistency, loaded=loaded, targets=targets, now=now,
            )
        except Exception as exc:
            # One book failing must not stop the others: the shadow is the
            # least important portfolio here and it is valued last, so letting
            # it abort the run would cost the real book its snapshot. The
            # failure is already recorded in job_runs and the exit code
            # carries it to the workflow.
            if len(portfolio_ids) == 1:
                raise
            log.exception("%s failed to value", portfolio_id)
            print(f"{portfolio_id}: FAILED -- {exc}", file=sys.stderr)
            consistent = False
            continue
        written += count
        consistent = consistent and ok

    if (out := os.environ.get("GITHUB_OUTPUT")):
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"snapshots_written={written}\n")
            fh.write(f"consistent={str(consistent).lower()}\n")
    return 0 if consistent else 1


def _value_one(
    portfolio_id: str, *, s, db, portfolios, holdings_repo, snapshots,
    cash, consistency, loaded, targets, now,
) -> tuple[int, bool]:
    """Value and snapshot one portfolio. Returns (snapshots written, consistent)."""
    portfolio = portfolios.get(portfolio_id)
    holdings = holdings_repo.book(portfolio_id)
    realised = money.sum_rupees(h.realised_pnl for h in holdings)
    service = valuation_service.ValuationService(snapshots=snapshots, cash=cash)

    # This job declared JOB_NAME from the start but never wrote to the ledger,
    # so a valuation failure left no trace and `verify_recovery` -- which reads
    # exactly this collection -- could never observe one. Claimed per date,
    # because `--backfill` revisits earlier dates and that retry is the
    # self-healing event worth recording.
    jobs_repo = JobRunRepository(db)

    # The ledger's unique key is (job_name, business_date), so two portfolios
    # valued on the same date would collide: the second would read as "already
    # succeeded" and record nothing, and a failure valuing it would leave no
    # trace at all. Non-default portfolios therefore get their own job name and
    # their own gap history. The default keeps the bare name so that its
    # existing rows -- and `missing_dates`, which queries by name -- stay
    # continuous rather than every past date suddenly reading as missing.
    job_name = JOB_NAME if portfolio_id == s.default_portfolio_id \
        else f"{JOB_NAME}:{portfolio_id}"

    written = 0
    for as_of in targets:
        run_id = None
        try:
            run_id = jobs_repo.claim(
                job_name, as_of, now=now,
                github_run_id=s.github_run_id or None,
                code_commit=s.code_commit, trigger=s.github_event_name,
                trigger_source=s.github_trigger_source,
            )
        except JobAlreadySucceeded:
            pass      # already valued; re-valuing is idempotent, ledger unchanged

        try:
            # The benchmark rides along in the same lookup rather than a
            # second pass: it needs the identical carry-forward behaviour, and
            # a separately-resolved date could disagree with the portfolio's on
            # a holiday. `value_positions` iterates holdings, so the extra key
            # is ignored there.
            wanted = {h.symbol for h in holdings}
            prices, price_date, stale = valuation_service.prices_as_of(
                loaded.panel, as_of=as_of,
                symbols=(wanted | {valuation_service.BENCHMARK_SYMBOL})
                        if wanted else None,
            )
            benchmark_close = prices.get(valuation_service.BENCHMARK_SYMBOL)
            if stale:
                # A carried price is a real estimate, not a quote. Printed
                # every time rather than only past a threshold: the cause is
                # usually a series change (EQ -> BE) that looks exactly like a
                # delisting from inside the panel, and the difference is not
                # something the code can tell.
                for symbol, when in sorted(stale.items()):
                    print(f"  STALE {symbol}: no close since {when}, "
                          f"carried forward to {price_date}")
            v = service.value(
                portfolio_id=portfolio_id, as_of=as_of, holdings=holdings,
                prices=prices, price_date=price_date,
                units=portfolio["units"],
                total_contributed=portfolio["total_contributed"],
                realised_pnl=realised,
                benchmark_close=benchmark_close,
            )
            service.snapshot(v, now=now,
                             total_contributed=portfolio["total_contributed"],
                             panel_manifest_hash=loaded.manifest_hash)
        except Exception as exc:
            if run_id:
                jobs_repo.fail(run_id, now=now, error={
                    "type": type(exc).__name__, "message": str(exc),
                })
            raise

        if run_id:
            jobs_repo.complete(run_id, now=now, result={
                "session": str(as_of), "nav": str(v.total_value),
                "positions": len(holdings),
            })
        written += 1
        carried = "" if price_date == as_of else f"  (prices from {price_date})"
        print(f"\n{as_of}{carried}")
        print(f"  portfolio value  Rs {v.total_value:>12,}")
        print(f"  market value     Rs {v.market_value:>12,}")
        print(f"  cash             Rs {v.cash:>12,}")
        print(f"  invested capital Rs {v.invested_capital:>12,}")
        print(f"  unrealised P&L   Rs {v.unrealised_pnl:>12,}")
        print(f"  realised P&L     Rs {v.realised_pnl:>12,}")
        print(f"  unit NAV         {v.unit_nav}   units {v.units}")
        if v.day_return is not None:
            print(f"  day return       {v.day_return:+.2%}")
        print(f"  drawdown         {v.drawdown:+.2%}")

    perf = PerformanceService(snapshots=snapshots, cash=cash).compute(portfolio_id)
    if perf and perf.sessions > 1:
        print(f"\nperformance over {perf.sessions} session(s)")
        for label, value, fmt in (
            ("total return", perf.total_return, "{:+.2%}"),
            ("CAGR", perf.cagr, "{:+.2%}"),
            ("XIRR", perf.xirr, "{:+.2%}"),
            ("max drawdown", perf.max_drawdown, "{:+.2%}"),
            ("Sharpe", perf.sharpe, "{:.2f}"),
            ("Martin", perf.martin, "{:.2f}"),
        ):
            print(f"  {label:<16} {fmt.format(value) if value is not None else '-'}")

    result = consistency.check(portfolio_id)
    print("\nreconciliation:")
    for finding in result.findings:
        print(f"  {finding}")

    return written, result.ok


if __name__ == "__main__":
    raise SystemExit(main())
