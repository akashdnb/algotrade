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

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from algotrade.config.settings import REPO_ROOT
from algotrade.core.costs import load_rate_cards
from algotrade.domain import money
from algotrade.io import feed
from algotrade.repositories.baskets import BasketRepository
from algotrade.repositories.base import AlreadyExists
from algotrade.repositories.holdings import HoldingRepository
from algotrade.repositories.recommendations import RecommendationRepository
from algotrade.repositories.symbol_map import SymbolMapRepository
from algotrade.services import pricing
from algotrade.services import review as review_service
from algotrade.services.rebalance import RebalanceService
from algotrade.config.strategy import StrategySpec

from app.auth import issue_csrf_token, require_csrf
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
def review_page(request: Request, on: str = Query(""),
                message: str = Query("")) -> HTMLResponse:
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
        "message": message,
        "csrf_token": issue_csrf_token(),
        "accepted": None,
    }
    if pending is None:
        return _render(request, context)

    context["accepted"] = BasketRepository(db).for_session(pending.set_id,
                                                           session)
    review, prices = _assemble(db, selection.active, pending, session)
    context["prices"] = prices
    context["review"] = review
    return _render(request, context)


def _assemble(db, portfolio_id: str, pending, session: date):
    """Price, size and plan the basket. **The only place this happens.**

    The GET renders it and the POST re-derives it before freezing, so they
    must run the same code -- two implementations would let the page show one
    basket and accept another, which is the failure this whole collection
    exists to prevent.
    """
    holdings = HoldingRepository(db).book(portfolio_id)
    cash = _cash(db, portfolio_id)
    target = review_service.target_membership(pending)

    # Held names are priced too: a rebalance redeploys the whole book, so their
    # value is part of what is investable.
    wanted = sorted(set(target) | {h.symbol for h in holdings if h.quantity})
    prices = pricing.resolve_opens(
        wanted, on=session,
        resolve=SymbolMapRepository(db).resolve,
        fetch=lambda tickers, day: feed.opens(tickers, on=day),
    )

    unpriced = [s for s in target if s in set(prices.failures)]

    # **No plan is computed when a target cannot be priced.** Sizing against a
    # partial price vector collapses the target book, so every holding reads as
    # "leaving" and the page renders a full liquidation at zero -- both wrong
    # and the most alarming thing this screen could possibly show. A basket
    # that cannot be priced has no instructions, not zero-valued ones.
    if unpriced:
        return review_service.blocked(pending, unpriced=unpriced), prices

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

    return review_service.build(
        recommendation=pending,
        items=plan.items,
        unpriced=[],
        dropped=decision.dropped,
        skipped=plan.skipped,
        investable=decision.investable,
        charge_reserve=decision.charge_reserve,
        deployed=decision.deployed,
        residual_cash=decision.residual_cash,
    ), prices


def _latest_pending(db, portfolio_id: str):
    """The newest set that is still actionable.

    Status **and** open items, never open items alone. A superseded set keeps
    its items `pending` forever -- nothing ever marks them executed, because
    they never will be -- so selecting on open items alone walks past a
    completed revision and resurrects the dead one behind it. That is exactly
    what happened on 2026-09-05.
    """
    repo = RecommendationRepository(db)
    for doc in repo.history(portfolio_id, limit=6):
        rec = repo.get_set(doc["_id"])
        if rec is not None and review_service.is_actionable(rec):
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


@router.post("/review/accept", dependencies=[Depends(require_csrf)])
def accept_basket(
    request: Request,
    set_id: str = Form(...),
    on: str = Form(...),
    digest: str = Form(""),
) -> RedirectResponse:
    """Freeze this basket as the one that will be executed.

    Writes what is on the screen -- quantities, prices, order types -- so the
    per-trade buttons act on the basket that was read rather than on one
    re-priced at each click. `/review` recomputes on every load; without this
    row the number beside trade 28 would not be the number reviewed at the
    start, which is the whole reason a per-trade button is worth having.

    **Not gated by `allow_writes`.** That flag guards writes to the *book*;
    this records a decision and touches no holding, no lot and no cash. It is
    also a precondition for execution rather than execution itself -- nothing
    here can reach a broker.
    """
    settings = get_settings()
    db = get_db(settings)
    selection = resolve(request, settings, db)
    session = _session_date(on)

    pending = _latest_pending(db, selection.active)
    if pending is None or pending.set_id != set_id:
        # The set moved between rendering and clicking -- a new revision, or
        # someone else executed it. Accepting the basket we no longer show
        # would freeze numbers nobody has read.
        return _redirect(session, "that recommendation is no longer the "
                                  "live one; reload and read it again")

    view, _ = _assemble(db, selection.active, pending, session)
    if view is None or not view.ready:
        return _redirect(session, "the basket is not ready, so there is "
                                  "nothing to freeze")

    # Re-derived server-side, never taken from the form: a client that could
    # name its own quantities could name any quantity. But re-deriving reopens
    # a gap -- the feed moves -- so the digest the page carried must still
    # match. Different means it moved between reading and clicking, and it is
    # refused rather than frozen.
    if digest and digest != review_service.digest(view):
        return _redirect(session, "prices moved since this page was drawn, so "
                                  "the basket you read is not the basket that "
                                  "would be frozen; reload and read it again")

    try:
        BasketRepository(db).accept(
            portfolio_id=selection.active,
            set_id=set_id,
            session=session,
            lines=[
                {"seq": line.seq, "action": str(line.action),
                 "symbol": line.symbol, "quantity": line.quantity,
                 "price": line.price,
                 "estimated_charges": line.charges}
                for line in view.lines
            ],
            now=clock.now(),
        )
    except AlreadyExists:
        return _redirect(session, "a basket for this session was already "
                                  "accepted; it is the one that executes")

    log.info("basket accepted: %s on %s (%d lines)",
             set_id, session, len(view.lines))
    return _redirect(session)


def _redirect(session: date, message: str = "") -> RedirectResponse:
    from urllib.parse import quote
    target = f"/review?on={session.isoformat()}"
    if message:
        target = f"{target}&message={quote(message)}"
    return RedirectResponse(target, status_code=303)
