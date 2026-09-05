#!/usr/bin/env python
"""Generate monthly rebalance recommendations. Recommendation only -- no orders.

Reconciliation-driven: `--check-only` reads STATE to decide whether a rebalance
is due, so this can run every weekday and exit in seconds. A dropped monthly
schedule then repairs itself within 24 hours.

    python -m jobs.generate_recommendations --check-only
    python -m jobs.generate_recommendations
    python -m jobs.generate_recommendations --period 2026-08 --force
    python -m jobs.generate_recommendations --verify-frozen
    python -m jobs.generate_recommendations --dry-run

Exit codes: 0 done or not due, 1 failed, 2 configuration error.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from algotrade.config import settings as settings_mod  # noqa: E402
from algotrade.config import validation  # noqa: E402
from algotrade.core.costs import load_rate_cards  # noqa: E402
from algotrade.io import archive  # noqa: E402
from algotrade.domain import money  # noqa: E402
from algotrade.platform import clock  # noqa: E402
from algotrade.repositories import client  # noqa: E402
from algotrade.repositories.audit import AuditRepository  # noqa: E402
from algotrade.repositories.holdings import HoldingRepository  # noqa: E402
from algotrade.repositories.portfolios import PortfolioRepository  # noqa: E402
from algotrade.repositories.rebalance_runs import RebalanceRunRepository  # noqa: E402
from algotrade.repositories.recommendations import RecommendationRepository  # noqa: E402
from algotrade.repositories.signals import SignalRepository  # noqa: E402
from algotrade.services import panel as panel_service  # noqa: E402
from algotrade.services import valuation as valuation_service  # noqa: E402
from algotrade.services.rebalance import RebalanceService  # noqa: E402
from algotrade.services.recommendation import (  # noqa: E402
    FrozenStrategyMismatch,
    RebalanceNotDue,
    RecommendationService,
)
from algotrade.services import signals as signals_service  # noqa: E402
from algotrade.services.signals import StrategySpec  # noqa: E402

log = logging.getLogger("jobs.generate_recommendations")


def _emit(**outputs) -> None:
    if not (path := os.environ.get("GITHUB_OUTPUT")):
        return
    with open(path, "a", encoding="utf-8") as fh:
        for key, value in outputs.items():
            fh.write(f"{key}={value}\n")


def _service(db, s, spec) -> RecommendationService:
    return RecommendationService(
        recommendations=RecommendationRepository(db),
        rebalance_runs=RebalanceRunRepository(db),
        rebalance=RebalanceService(
            entry=spec.entry, exit_=spec.exit,
            charge_reserve_bps=s.charge_reserve_bps,
            card=load_rate_cards(s.cost_config)["delivery"],
        ),
        audit=AuditRepository(db),
        ttl_days=s.recommendation_ttl_days,
    )


def _check_only(args, s, spec, db, portfolios, signals) -> int:
    """Which portfolios are due -- the GitHub Actions matrix (§4.2).

    EVERY active portfolio, not just the default one. `rebalance.yml` has
    advertised "blank = every active one" and driven its matrix from this
    output since it was written, but this emitted a single id, so a second
    portfolio -- a shadow, or a second strategy -- would have been skipped
    every month with nothing in the logs to say it had been.

    Each portfolio is judged against **its own** strategy's signal calendar,
    read from the portfolio document. `spec` here is only the process's loaded
    config; a second strategy's portfolio must not be asked whether momentum's
    month has closed.

    The frozen-strategy gate is deliberately NOT applied here. It is a
    per-portfolio check and the rebalance job runs `--verify-frozen` on each
    matrix entry, so a portfolio whose hash has drifted fails visibly on its
    own row instead of silently removing every other portfolio from the run.
    """
    if args.portfolio_id:
        candidates = [p for p in [portfolios.get(args.portfolio_id)] if p]
        if not candidates:
            print(f"no portfolio {args.portfolio_id!r}", file=sys.stderr)
            _emit(due="false", portfolios="[]", reason="no-such-portfolio")
            return 1
    else:
        candidates = portfolios.active()

    allowed = []
    for portfolio in candidates:
        try:
            validation.guard_portfolio(s, portfolio["_id"])
        except settings_mod.ConfigError as exc:
            print(f"  {portfolio['_id']:<28} SKIP  {exc}")
            continue
        allowed.append(portfolio)

    checks = _service(db, s, spec).due_portfolios(
        allowed, signals=signals, period=args.period, force=args.force)

    print(f"portfolios {len(checks)}")
    for check in checks:
        print(f"  {check.portfolio_id:<28} {check.strategy_version:<14} "
              f"{'DUE' if check.due else 'no':<4}  {check.reason}")
        if check.missed:
            # Named rather than counted: a gap that leaves no trace is
            # indistinguishable afterwards from a month that went well.
            print(f"  {'':<28} {'':<14} MISSED  {', '.join(check.missed)} "
                  f"-- skipped, not rebalanced")

    due_ids = [c.portfolio_id for c in checks if c.due]

    # Two strategies whose panels end on different days have different current
    # periods, so there is no single one to report. The matrix does not consume
    # this output -- each job resolves its own -- but printing one strategy's
    # period as though it were everyone's would be a lie in the logs.
    periods = list(dict.fromkeys(c.period for c in checks if c.period))
    # `dict.fromkeys` rather than a set: the reasons stay in the order the
    # portfolios were checked, so the string reads the same way twice.
    reasons = list(dict.fromkeys(c.reason for c in checks))

    _emit(due=str(bool(due_ids)).lower(), portfolios=json.dumps(due_ids),
          period="; ".join(periods),
          reason="; ".join(reasons) or "no-active-portfolios")

    # A check that could not be made anywhere is a broken deployment, not a
    # quiet "nothing is due". Exiting 0 here would let a missing signal run
    # look identical to a month that has not closed yet.
    if not checks:
        print("no active portfolio to check", file=sys.stderr)
        return 1
    if all(c.reason.startswith("no signals stored") for c in checks):
        print("no portfolio has stored signals -- run: "
              "python -m jobs.generate_signals", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--portfolio-id", default=None)
    ap.add_argument("--period", default=None, help="YYYY-MM (default: current)")
    ap.add_argument("--check-only", action="store_true",
                    help="report whether a rebalance is due; change nothing")
    ap.add_argument("--verify-frozen", action="store_true",
                    help="check the strategy config still matches the portfolio")
    ap.add_argument("--force", action="store_true",
                    help="regenerate as a new revision even if completed")
    ap.add_argument("--dry-run", action="store_true")
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

    spec = StrategySpec.from_yaml(s.strategy_config)
    db = client.connect(s.mongodb_uri, database=s.mongodb_database)
    portfolios = PortfolioRepository(db)
    signals_repo = SignalRepository(db)

    # Fans out over every active portfolio, so it runs before the
    # single-portfolio resolution below.
    if args.check_only:
        return _check_only(args, s, spec, db, portfolios, signals_repo)

    portfolio_id = args.portfolio_id or s.default_portfolio_id
    try:
        validation.guard_portfolio(s, portfolio_id)
    except settings_mod.ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2

    portfolio = portfolios.get(portfolio_id)
    if portfolio is None:
        print(f"no portfolio {portfolio_id!r} -- run: python -m jobs.create_portfolio",
              file=sys.stderr)
        return 1

    # --- the frozen-strategy gate -------------------------------------------
    stored_hash = portfolio.get("strategy_params_hash")
    if stored_hash != spec.params_hash():
        print(f"FROZEN STRATEGY MISMATCH\n"
              f"  portfolio was created with {stored_hash}\n"
              f"  config now hashes to      {spec.params_hash()}\n"
              f"  A parameter change forks a NEW portfolio (bump to v2); it must "
              f"never silently alter this one.", file=sys.stderr)
        return 1

    # --- the same gate, for the data the parameters run over ------------------
    # params_hash freezes what the strategy DOES. This freezes what it can SEE:
    # the series filter, the ETF denylist, the equity allowlist, the fund-name
    # suffixes. All four live in code rather than the frozen YAML, and a change
    # to any of them alters every ranking while the gate above stays green --
    # DEFAULT_EXCLUSIONS was in fact edited live on 2026-08-20.
    current_pipeline = signals_service.pipeline_hash()
    stored_pipeline = portfolio.get("pipeline_hash")
    if stored_pipeline and stored_pipeline != current_pipeline:
        print(f"DATA PIPELINE MISMATCH\n"
              f"  portfolio was created with {stored_pipeline}\n"
              f"  the pipeline now hashes to {current_pipeline}\n"
              f"  The universe this book ranks over has changed. That is a new\n"
              f"  strategy however small the edit looked -- fork a v2 rather\n"
              f"  than letting it alter this one.", file=sys.stderr)
        return 1
    if not stored_pipeline:
        # Adoption, once, for books created before this gate existed. Stamping
        # the current value is safe only because it is the pipeline they have
        # been running all along -- and it is announced rather than silent, so
        # the first stamp is never mistaken for a verified match.
        portfolios.update(portfolio_id, {"pipeline_hash": current_pipeline})
        AuditRepository(db).record(
            now=clock.SystemClock().now(), actor="system",
            action="portfolio.pipeline_hash.adopted", entity_type="portfolio",
            entity_id=portfolio_id, portfolio_id=portfolio_id,
            after={"pipeline_hash": current_pipeline}, severity="warning",
            context={"job": "generate_recommendations",
                     "why": "first run after the data-pipeline gate was added"},
        )
        print(f"pipeline hash adopted: {current_pipeline} "
              f"(first run under this gate -- not a verified match)")

    if args.verify_frozen:
        print(f"frozen strategy OK: {spec.version} {spec.params_hash()}")
        print(f"data pipeline   OK: {current_pipeline}")
        return 0

    latest = signals_repo.latest(spec.version)
    if latest is None:
        print(f"no signals stored for {spec.version} -- run: "
              f"python -m jobs.generate_signals", file=sys.stderr)
        _emit(due="false", reason="no-signals")
        return 1

    as_of = latest.date
    sessions = signals_repo.dates(spec.version)
    service = _service(db, s, spec)

    # The LATEST CLOSED period, not the one the latest session sits in. The
    # month in progress can never be complete, so deriving the period from
    # `as_of` made this exit "not due" every day of the year.
    missed: tuple[str, ...] = ()
    if args.period:
        period = args.period
    else:
        period, why, missed = service.due_period(portfolio_id, sessions)
        if period is None:
            print(f"portfolio  {portfolio_id}")
            print(f"due        False  -- {why}")
            _emit(due="false", reason=why)
            return 0

    if missed:
        # Named, never rebalanced. A gap that leaves no trace is
        # indistinguishable afterwards from a month that went well.
        print(f"MISSED     {', '.join(missed)} -- not rebalanced, and will not "
              f"be. Only {period} is being brought up to date.")

    due, reason = service.is_due(
        portfolio_id=portfolio_id, period=period, sessions=sessions, as_of=as_of,
    )
    if args.force:
        due, reason = True, "forced"

    print(f"portfolio  {portfolio_id}")
    print(f"period     {period}")
    print(f"signal     {as_of}  ({len(latest.rows)} ranks)")
    print(f"due        {due}  -- {reason}")

    if not due:
        _emit(due="false", reason=reason)
        return 0

    holdings = HoldingRepository(db).book(portfolio_id)
    ranked = {r.symbol: r.close for r in latest.rows if r.close is not None}
    held = [h.symbol for h in holdings]

    # Held names must be priced too, or their value cannot count toward what is
    # investable. From the PANEL, not defaulted to zero: the signal set stores
    # only the top 60 of ~2,600 traded symbols, so a held name missing from it
    # has almost always just fallen out of the rank buffer -- which is why it
    # is being sold, and says nothing about what it is worth.
    fallback: dict[str, Decimal] = {}
    if any(symbol not in ranked for symbol in held):
        loaded = panel_service.load(
            curated_dir=s.curated_dir,
            actions_dir=s.data_dir / "raw" / "actions", through=as_of,
        )
        fallback, price_date, stale = valuation_service.prices_as_of(
            loaded.panel, as_of=as_of,
            symbols={symbol for symbol in held if symbol not in ranked},
        )
        print(f"panel      {len(fallback)} held name(s) priced as of {price_date}")

        # Same BE fallback the valuation uses, and it matters MORE here. The
        # reference price is what slippage is measured against and what the
        # shadow auto-fills at, so a carried close does two damaging things:
        # selling at the real price reads as favourable slippage that never
        # happened, and the shadow books a fill at a price nobody could get,
        # putting a data artifact into the discretion column.
        #
        # `as_of` is the panel's own latest session -- `load(through=as_of)` --
        # so the back-adjustment guard that limits this to the newest close is
        # satisfied by construction.
        if stale:
            session_dirs = (([s.sessions_dir] if s.sessions_dir.exists()
                             else []) + s.archive_dirs)
            path = archive.discover(session_dirs).get(price_date)
            if path is not None:
                for symbol, close in valuation_service.unfiltered_closes(
                        path, set(stale)).items():
                    print(f"  {symbol}: {close} from {price_date} "
                          f"(outside the panel's EQ series, not stale)")
                    fallback[symbol] = close
                    stale.pop(symbol, None)

        for symbol, when in sorted(stale.items()):
            print(f"  STALE {symbol}: last close {when}, used as the "
                  f"reference price")

    prices, unpriced = valuation_service.merge_prices(ranked, held, fallback)
    if unpriced:
        # Refuse rather than emit "sell 30 HFCL @ Rs 0.00". A sell at zero is
        # not a conservative estimate, and the shadow portfolio auto-fills at
        # the reference price, so nobody would see it before it was booked.
        print(f"\nCANNOT PRICE {len(unpriced)} HELD NAME(S): {', '.join(unpriced)}\n"
              f"  Absent from the panel on {as_of}, so any instruction for them "
              f"would carry a reference price of zero.\n"
              f"  A series change (EQ -> BE) removes a name from the panel while "
              f"it is still trading normally -- check the bhavcopy before "
              f"assuming a delisting.", file=sys.stderr)
        _emit(due="false", reason="unpriced-holdings")
        return 1

    if args.dry_run:
        decision = service._rebalance.decide(
            holdings=holdings, signals=latest, cash=portfolio["cash"],
            prices=prices, as_of=as_of,
        )
        _print_decision(decision, holdings)
        print("\n--dry-run: nothing written.")
        return 0

    try:
        result = service.generate(
            portfolio_id=portfolio_id, period=period,
            holdings=holdings, signals=latest,
            cash=portfolio["cash"], prices=prices, as_of=as_of,
            now=clock.SystemClock().now(),
            panel_manifest_hash=latest.panel_manifest_hash,
            strategy_params_hash=spec.params_hash(),
            portfolio_params_hash=stored_hash,
            code_commit=s.code_commit,
            cost_model_hash=str(s.cost_config.name),
            force=args.force,
        )
    except (FrozenStrategyMismatch, RebalanceNotDue) as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 0 if isinstance(exc, RebalanceNotDue) else 1

    rec, decision = result.recommendation, result.decision
    _print_decision(decision, holdings)
    print(f"\nstored {rec.set_id}")
    print(f"  {len(rec.items)} trade(s), {len(rec.holds)} hold(s), "
          f"expires {rec.expires_on}")
    _emit(due="true", set_id=rec.set_id, trades=len(rec.items),
          period=period, entering=len(decision.entering),
          leaving=len(decision.leaving))
    return 0


def _print_decision(decision, holdings) -> None:
    print(f"\ncurrent    {len(holdings)} position(s)")
    print(f"target     {len(decision.target_symbols)} name(s)")
    print(f"entering   {len(decision.entering)}  {', '.join(decision.entering[:8])}")
    print(f"leaving    {len(decision.leaving)}  {', '.join(decision.leaving[:8])}")
    print(f"holding    {len(decision.holding)}")
    print(f"investable Rs {decision.investable:,}  "
          f"(reserve Rs {decision.charge_reserve:,})")
    print(f"deployed   Rs {decision.deployed:,}   "
          f"residual Rs {decision.residual_cash:,}")
    print(f"cash req.  Rs {decision.cash_required:,}")
    if decision.dropped:
        print(f"dropped    {len(decision.dropped)}")
        for symbol, why in list(decision.dropped.items())[:5]:
            print(f"             {symbol}: {why}")


if __name__ == "__main__":
    raise SystemExit(main())
