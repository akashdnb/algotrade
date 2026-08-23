#!/usr/bin/env python
"""Assemble and send the daily Telegram report.

Deduped by (kind, portfolio, date), so a re-run of the daily workflow cannot
post a second summary. Never raises on a send failure -- a broken alert channel
must not fail a run that otherwise succeeded -- but always records the failure
so the watchdog can surface it.

    python -m jobs.daily_report
    python -m jobs.daily_report --date 2026-08-20
    python -m jobs.daily_report --dry-run      # print, send nothing

Exit codes: 0 sent or skipped, 1 reconciliation failed, 2 configuration error.
The reconciliation exit code is deliberate: the report still goes out (you need
to see the failure), but the workflow step fails so the heartbeat records it.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from algotrade.config import settings as settings_mod  # noqa: E402
from algotrade.config import validation  # noqa: E402
from algotrade.domain import money  # noqa: E402
from algotrade.domain.models import SetStatus  # noqa: E402
from algotrade.platform import clock  # noqa: E402
from algotrade.platform.telegram import TelegramClient  # noqa: E402
from algotrade.repositories import client  # noqa: E402
from algotrade.repositories.cash import CashRepository  # noqa: E402
from algotrade.repositories.executions import ExecutionRepository  # noqa: E402
from algotrade.repositories.holdings import HoldingRepository  # noqa: E402
from algotrade.repositories.notifications import NotificationRepository  # noqa: E402
from algotrade.repositories.portfolios import PortfolioRepository  # noqa: E402
from algotrade.repositories.recommendations import RecommendationRepository  # noqa: E402
from algotrade.repositories.signals import SignalRepository  # noqa: E402
from algotrade.repositories.snapshots import SnapshotRepository  # noqa: E402
from algotrade.services.holdings import HoldingsService  # noqa: E402
from algotrade.services.notification import (  # noqa: E402
    NotificationService,
    ReportFacts,
    render_report,
)
from algotrade.services.reconciliation import ReconciliationService  # noqa: E402
from algotrade.services.signals import StrategySpec  # noqa: E402

log = logging.getLogger("jobs.daily_report")

#: Beyond this the data is stale enough to block valuation (§10.1).
STALE_SESSIONS = 2


def _day(text: str) -> date:
    return datetime.strptime(text, "%Y-%m-%d").date()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--portfolio-id", default=None)
    ap.add_argument("--date", type=_day, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    s = settings_mod.load()
    logging.basicConfig(level=getattr(logging, s.log_level, logging.INFO),
                        format="%(levelname)-7s %(message)s")

    report_v = validation.validate(s)
    blocking = [f for f in report_v.errors if not f.key.startswith("HEALTHCHECKS")]
    if blocking:
        for finding in blocking:
            print(finding, file=sys.stderr)
        return 2

    portfolio_id = args.portfolio_id or s.default_portfolio_id
    db = client.connect(s.mongodb_uri, database=s.mongodb_database)

    portfolios = PortfolioRepository(db)
    portfolio = portfolios.get(portfolio_id)
    if portfolio is None:
        print(f"no portfolio {portfolio_id!r}", file=sys.stderr)
        return 2

    snapshots = SnapshotRepository(db)
    latest = (
        snapshots.as_of(portfolio_id, args.date) if args.date
        else snapshots.latest(portfolio_id)
    )
    if latest is None:
        print("no snapshot to report -- run: python -m jobs.value_portfolio",
              file=sys.stderr)
        return 2

    spec = StrategySpec.from_yaml(s.strategy_config)
    signals_repo = SignalRepository(db)
    executions = ExecutionRepository(db)
    holdings_repo = HoldingRepository(db)
    cash = CashRepository(db)
    recs = RecommendationRepository(db)

    # --- reconciliation first: it decides whether the NAV means anything ----
    consistency = ReconciliationService(
        portfolios=portfolios,
        holdings_service=HoldingsService(
            executions=executions, holdings=holdings_repo),
        cash=cash, executions=executions, recommendations=recs,
        snapshots=snapshots,
    ).check(portfolio_id)

    # --- data freshness -----------------------------------------------------
    signal = signals_repo.latest(spec.version)
    signal_dates = signals_repo.dates(spec.version)
    behind = 0
    if signal:
        behind = len([
            d for d in clock.weekdays_between(signal.date, latest.date)
            if d > signal.date
        ])
    fresh = behind < STALE_SESSIONS

    # --- day P&L from the previous snapshot ---------------------------------
    series = snapshots.series(
        portfolio_id, end=latest.date, fields=["nav", "unit_nav"])
    previous = series[-2] if len(series) >= 2 else None
    day_pnl = (
        money.rupees(latest.nav - previous["nav"]) if previous else None
    )
    day_return = (
        money.percent_change(latest.unit_nav, previous["unit_nav"])
        if previous and previous.get("unit_nav") else None
    )
    since_inception = (
        float((latest.unit_nav - money.rupees("100.00")) / money.rupees("100.00"))
        if latest.unit_nav else None
    )

    pending = sum(
        len([i for i in (recs.get_set(d["_id"]).items if recs.get_set(d["_id"]) else [])
             if i.is_open])
        for d in recs.outstanding(portfolio_id)
    )

    facts = ReportFacts(
        portfolio_id=portfolio_id,
        label=portfolio.get("label") or "Momentum Portfolio",
        as_of=latest.date,
        nav=latest.nav, cash=latest.cash, positions=len(latest.positions),
        day_pnl=day_pnl, day_return=day_return,
        since_inception=since_inception, drawdown=latest.drawdown,
        top_holdings=[(p.symbol, p.weight) for p in latest.positions[:5]],
        signals_generated=len(signal.rows) if signal else 0,
        signal_date=signal.date if signal else None,
        data_fresh=fresh,
        data_detail="" if fresh else f"{behind} session(s) behind",
        reconciliation_ok=consistency.ok,
        reconciliation_failures=[f.message for f in consistency.errors],
        pending_items=pending,
    )

    text = render_report(facts)
    if args.dry_run:
        print(text)
        return 0 if consistency.ok else 1

    NotificationService(
        repository=NotificationRepository(db),
        client=(TelegramClient(s.telegram_bot_token, s.telegram_chat_id)
                if s.telegram_configured else None),
        chat_id=s.telegram_chat_id,
    ).send(kind="daily_summary", text=text, now=clock.SystemClock().now(),
           portfolio_id=portfolio_id, business_date=latest.date)

    print(f"daily report sent for {latest.date}")
    if not consistency.ok:
        for finding in consistency.errors:
            print(f"  {finding}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
