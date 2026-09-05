"""The basket you approve before touching a broker.

Assembles, in this order and no other:

    pending recommendation -> frozen membership -> execution-day opens
                           -> resized quantities -> sell-first instructions

Every step is a function tested without a network; this route wires them and
renders. **It writes nothing and places nothing** -- the page is a statement
about what would be traded, not an action.

Prices come from the delayed public feed, never from the broker: the free Kite
tier carries no market data, and the engine fills at the open, which is a
static number fifteen minutes after the bell (design §6).
"""

from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from algotrade.config.settings import REPO_ROOT
from algotrade.core.costs import load_rate_cards
from algotrade.domain import money
from algotrade.io import feed
from algotrade.repositories.holdings import HoldingRepository
from algotrade.repositories.recommendations import RecommendationRepository
from algotrade.repositories.symbol_map import SymbolMapRepository
from algotrade.services import pricing
from algotrade.services import review as review_service
from algotrade.services.rebalance import RebalanceService
from algotrade.config.strategy import StrategySpec

from app import clock
from app.db import get_db
from app.portfolio import resolve
from app.settings import get_settings

router = APIRouter()

log = logging.getLogger("dashboard.review")

#: Absolute, not the library's cwd-relative default. On Vercel the working
#: directory is not the repository root, and a relative path would resolve to
#: nothing -- silently, as a missing cost card rather than as an error.
COST_CONFIG = REPO_ROOT / "config" / "costs.yaml"
STRATEGY_CONFIG = REPO_ROOT / "config" / "strategy" / "momentum_v1.yaml"


@router.get("/review", response_class=HTMLResponse)
def review_page(request: Request, on: str = Query("")) -> HTMLResponse:
    settings = get_settings()
    db = get_db(settings)
    selection = resolve(request, settings, db)

    session = _session_date(on)
    pending = _latest_pending(db, selection.active)

    context: dict = {
        "title": "Review",
        "settings": settings,
        "selection": selection,
        "on": session,
        "today": clock.today(),
        "recommendation": pending,
        "review": None,
        "prices": None,
        "error": "",
    }
    if pending is None:
        return _render(request, context)

    holdings = HoldingRepository(db).book(selection.active)
    cash = _cash(db, selection.active)
    target = review_service.target_membership(pending)

    # Held names are priced too: a rebalance redeploys the whole book, so their
    # value is part of what is investable.
    wanted = sorted(set(target) | {h.symbol for h in holdings if h.quantity})
    prices = pricing.resolve_opens(
        wanted, on=session,
        resolve=SymbolMapRepository(db).resolve,
        fetch=lambda tickers, day: feed.opens(tickers, on=day),
    )

    context["prices"] = prices
    unpriced = [s for s in target if s in set(prices.failures)]

    # **No plan is computed when a target cannot be priced.** Sizing against a
    # partial price vector collapses the target book, so every holding reads as
    # "leaving" and the page renders a full liquidation at zero -- both wrong
    # and the most alarming thing this screen could possibly show. A basket
    # that cannot be priced has no instructions, not zero-valued ones.
    if unpriced:
        context["review"] = review_service.blocked(pending, unpriced=unpriced)
        return _render(request, context)

    spec = StrategySpec.from_yaml(STRATEGY_CONFIG)
    service = RebalanceService(
        entry=spec.entry, exit_=spec.exit,
        charge_reserve_bps=30, card=_card(),
    )
    decision = service.resize(
        target=target, holdings=holdings, cash=cash, prices=prices.prices,
        ranks={}, as_of=session,
    )
    plan = service.plan(holdings=holdings, decision=decision)

    context["review"] = review_service.build(
        recommendation=pending,
        items=plan.items,
        unpriced=[],
        dropped=decision.dropped,
        skipped=plan.skipped,
        investable=decision.investable,
        charge_reserve=decision.charge_reserve,
        deployed=decision.deployed,
        residual_cash=decision.residual_cash,
    )
    return _render(request, context)


def _latest_pending(db, portfolio_id: str):
    """The newest set with work left to do.

    `history` is ordered newest-first, so the first set carrying an open item
    is the one being executed. A set whose items are all filled is finished
    even though its status may lag.
    """
    repo = RecommendationRepository(db)
    for doc in repo.history(portfolio_id, limit=6):
        rec = repo.get_set(doc["_id"])
        if rec is not None and rec.open_items:
            return rec
    return None


def _cash(db, portfolio_id: str):
    doc = db["portfolios"].find_one({"_id": portfolio_id})
    if not doc:
        return money.ZERO
    from algotrade.repositories import codec
    return codec.from_mongo_money(doc.get("cash")) or money.ZERO


def _render(request: Request, context: dict) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse(
        request, "review.html", context)


def _session_date(given: str) -> date:
    """The execution session. Defaults to today's business date.

    Overridable so the page can be read against a past session without waiting
    for a rebalance. The template says when the date is not today, because a
    basket priced on a stale session is a rehearsal and must not read as live.
    """
    if given:
        try:
            return date.fromisoformat(given)
        except ValueError:
            pass
    # The trading SESSION we are in, not the calendar date. On a Saturday
    # `today()` names a day the market never opened, so every symbol comes
    # back unpriced and the page reports a feed failure for a session that
    # does not exist. `session_date` is the rule the ingestion job uses.
    return clock.session_date()


def _card():
    try:
        return load_rate_cards(COST_CONFIG)["delivery"]
    except Exception as exc:              # pragma: no cover - config only
        # Charges then read as zero, which understates every trade. Louder
        # than silence, quieter than refusing to show the basket at all.
        log.warning("cost card unavailable; charges will read as zero: %s", exc)
        return None
