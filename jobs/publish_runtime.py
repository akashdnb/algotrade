#!/usr/bin/env python
"""Write static JSON summaries to `data/runtime/`.

The future dashboard's data source. **No REST API**: the dashboard is
read-mostly, a static file has no server to keep alive, no cold start, and no
attack surface, and it is committed so every state the dashboard ever showed
is in git history.

    python -m jobs.publish_runtime
    python -m jobs.publish_runtime --out data/runtime

Files:
    portfolio_summary.json     NAV, cash, holdings, performance
    latest_signals.json        the current ranking
    latest_recommendation.json the outstanding instruction set, if any
    acceptance_status.json     10-session gate progress
    system_health.json         score, status, deductions

Money is emitted as a **string**, never a float -- JavaScript's float64 cannot
hold rupees exactly, and a dashboard that displays a rounded NAV is a dashboard
that disagrees with the database.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from algotrade.acceptance import status as acceptance_status  # noqa: E402
from algotrade.config import settings as settings_mod  # noqa: E402
from algotrade.config import validation  # noqa: E402
from algotrade.ops import health as health_ops  # noqa: E402
from algotrade.platform import clock  # noqa: E402
from algotrade.repositories import client  # noqa: E402
from algotrade.repositories.cash import CashRepository  # noqa: E402
from algotrade.repositories.executions import ExecutionRepository  # noqa: E402
from algotrade.repositories.holdings import HoldingRepository  # noqa: E402
from algotrade.repositories.portfolios import PortfolioRepository  # noqa: E402
from algotrade.repositories.recommendations import RecommendationRepository  # noqa: E402
from algotrade.repositories.signals import SignalRepository  # noqa: E402
from algotrade.repositories.snapshots import SnapshotRepository  # noqa: E402
from algotrade.services.performance import PerformanceService  # noqa: E402
from algotrade.services.signals import StrategySpec  # noqa: E402

log = logging.getLogger("jobs.publish_runtime")


def _json(value):
    """Money as a string; dates as ISO. Never a float for a rupee amount."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"cannot serialise {type(value).__name__}")


def _write(directory: Path, name: str, payload: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=_json) + "\n",
        encoding="utf-8",
    )
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=Path("data/runtime"))
    ap.add_argument("--portfolio-id", default=None)
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    s = settings_mod.load()
    report = validation.validate(s)
    blocking = [f for f in report.errors if not f.key.startswith("HEALTHCHECKS")]
    if blocking:
        for finding in blocking:
            print(finding, file=sys.stderr)
        return 2

    portfolio_id = args.portfolio_id or s.default_portfolio_id
    db = client.connect(s.mongodb_uri, database=s.mongodb_database)
    generated_at = clock.SystemClock().now()
    spec = StrategySpec.from_yaml(s.strategy_config)

    portfolios = PortfolioRepository(db)
    snapshots = SnapshotRepository(db)
    holdings = HoldingRepository(db)
    cash = CashRepository(db)
    recs = RecommendationRepository(db)
    signals_repo = SignalRepository(db)

    portfolio = portfolios.get(portfolio_id) or {}
    latest = snapshots.latest(portfolio_id)
    written: list[Path] = []

    # ---- portfolio_summary --------------------------------------------------
    perf = PerformanceService(snapshots=snapshots, cash=cash).compute(portfolio_id)
    summary = {
        "generated_at": generated_at,
        "portfolio_id": portfolio_id,
        "label": portfolio.get("label", ""),
        "mode": portfolio.get("mode", ""),
        "strategy_version": portfolio.get("strategy_version", ""),
        "as_of": latest.date if latest else None,
        "nav": latest.nav if latest else None,
        "cash": latest.cash if latest else None,
        "market_value": latest.invested_value if latest else None,
        "units": latest.units if latest else None,
        "unit_nav": latest.unit_nav if latest else None,
        "drawdown": latest.drawdown if latest else None,
        "positions": [
            {"symbol": p.symbol, "quantity": p.quantity, "close": p.close,
             "value": p.value, "weight": round(p.weight, 6),
             "unrealised_pnl": p.unrealised_pnl}
            for p in (latest.positions if latest else [])
        ],
        "performance": (perf.summary() if perf else None),
        # A NAV chart needs no positions; projecting them away keeps the file
        # small enough to commit daily without bloating the repository.
        "series": snapshots.series(
            portfolio_id, fields=["nav", "unit_nav", "drawdown"]),
    }
    written.append(_write(args.out, "portfolio_summary.json", summary))

    # ---- latest_signals -----------------------------------------------------
    signal = signals_repo.latest(spec.version)
    written.append(_write(args.out, "latest_signals.json", {
        "generated_at": generated_at,
        "strategy_version": spec.version,
        "date": signal.date if signal else None,
        "universe_size": signal.universe_size if signal else 0,
        "entry_threshold": spec.entry,
        "exit_threshold": spec.exit,
        "rows": [
            {"rank": r.rank, "symbol": r.symbol,
             "momentum": round(r.feature, 6), "close": r.close}
            for r in (signal.rows if signal else [])
        ],
    }))

    # ---- latest_recommendation ----------------------------------------------
    outstanding = recs.outstanding(portfolio_id)
    rec = recs.get_set(outstanding[0]["_id"]) if outstanding else None
    if rec is None:
        history = recs.history(portfolio_id, limit=1)
        rec = recs.get_set(history[0]["_id"]) if history else None
    written.append(_write(args.out, "latest_recommendation.json", {
        "generated_at": generated_at,
        "set_id": rec.set_id if rec else None,
        "period": rec.period if rec else None,
        "revision": rec.revision if rec else None,
        "status": rec.status.value if rec else None,
        "as_of_session": rec.as_of_session if rec else None,
        "expires_on": rec.expires_on if rec else None,
        "reference_nav": rec.reference_nav if rec else None,
        "holds": rec.holds if rec else [],
        "items": [
            {"seq": i.seq, "action": i.action.value, "symbol": i.symbol,
             "quantity": i.quantity, "reference_price": i.reference_price,
             "estimated_value": i.estimated_value,
             "estimated_charges": i.estimated_charges,
             "status": i.status.value,
             "executed_quantity": i.executed_quantity,
             "executed_price": i.executed_price,
             "slippage_bps": i.slippage_bps,
             "reason": i.reason, "skip_reason": i.skip_reason}
            for i in (rec.items if rec else [])
        ],
    }))

    # ---- acceptance_status ---------------------------------------------------
    checks, facts = acceptance_status.collect(s)
    written.append(_write(args.out, "acceptance_status.json", {
        "generated_at": generated_at,
        "facts": facts,
        "checks": [
            {"name": c.name, "status": c.status, "detail": c.detail}
            for c in checks
        ],
    }))

    # ---- system_health -------------------------------------------------------
    backups = sorted(Path("backups").glob("algotrade-backup-*.json.gz")) \
        if Path("backups").exists() else []
    backup_age = None
    if backups:
        stamp = backups[-1].name.split("-")[-1].removesuffix(".json.gz")
        try:
            backup_age = (generated_at.date() - datetime.strptime(
                stamp, "%Y%m%d").date()).days
        except ValueError:
            backup_age = None

    snapshot_dates = set(snapshots.dates(portfolio_id))
    expected = [
        d for d in clock.weekdays_between(
            (latest.date - timedelta(days=14)) if latest else generated_at.date(),
            latest.date if latest else generated_at.date())
    ]
    health = health_ops.score(
        sessions_behind=facts.get("sessions_behind", 0),
        consecutive_job_failures=0 if facts.get("last_ingest") else 1,
        missing_snapshots=len([d for d in expected if d not in snapshot_dates]),
        reconciliation_failures=len(facts.get("reconciliation_errors", [])),
        notification_failures=facts.get("notification_failures_7d", 0),
        heartbeat_configured=s.healthchecks_configured,
        backup_age_days=backup_age, backup_checked=True,
    )
    written.append(_write(args.out, "system_health.json", {
        "generated_at": generated_at, **health.to_dict(),
    }))

    for path in written:
        print(f"  {path}  ({path.stat().st_size:,} bytes)")
    print(f"\nhealth {health.score}/100 {health.status} -- {health.explanation}")

    if (out := __import__("os").environ.get("GITHUB_OUTPUT")):
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"health_score={health.score}\n")
            fh.write(f"health_status={health.status}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
