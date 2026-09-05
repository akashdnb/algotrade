"""The demat: what the broker says, beside what the book says.

This route decides nothing. It reads, hands the results to `app.broker`, and
renders. Every judgement -- what the session state is, how positions line up,
whether the two agree -- is a pure function tested in `tests/test_broker.py`.

**Read-only, in two independent ways.** No code here places an order, and this
deployment could not if it tried: Zerodha requires a registered static IP for
order placement, and serverless egress has none. Submission lives on a
different host (design §4).
"""

from __future__ import annotations

import base64
import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from algotrade.brokers import tokens
from algotrade.brokers.base import BrokerError
from algotrade.brokers.kite import KiteBroker
from algotrade.repositories.broker_sessions import BrokerSessionRepository
from algotrade.repositories.holdings import HoldingRepository as TradingHoldings
from algotrade.services import demat as demat_service

from app import broker as view_model
from app import clock
from app.db import get_db
from app.portfolio import resolve
from app.settings import get_settings

router = APIRouter()

log = logging.getLogger("dashboard.broker")


@router.get("/broker", response_class=HTMLResponse)
def broker_section(request: Request) -> HTMLResponse:
    settings = get_settings()
    db = get_db(settings)
    selection = resolve(request, settings, db)

    sessions = BrokerSessionRepository(db)
    name = settings.broker_name or "zerodha"
    now = clock.now()

    ciphertext = (
        sessions.live(name, now=now) if settings.broker_readable else None
    )
    state = view_model.session_state(
        token_key_configured=bool(settings.broker_token_key),
        ciphertext=ciphertext,
        expires_at=sessions.expires_at(name) if ciphertext else None,
    )

    view = view_model.View(session=state)
    if state.usable:
        view = _load(settings, db, selection.active, state, ciphertext, name)

    return request.app.state.templates.TemplateResponse(
        request, "broker.html",
        {
            "title": "Broker",
            "settings": settings,
            "selection": selection,
            "view": view,
            "broker_name": name,
        },
    )


def _load(settings, db, portfolio_id, state, ciphertext, name):
    """Fetch and compare. Any broker failure becomes a visible error.

    Caught broadly on purpose: a demat page that renders an empty table when
    the broker is unreachable says "you hold nothing" when it means "we do not
    know", and those are opposite claims.
    """
    try:
        key = base64.b64decode(settings.broker_token_key, validate=True)
        access_token = tokens.decrypt(ciphertext, key=key)
    except (ValueError, tokens.TokenError) as exc:
        log.warning("broker token could not be decrypted: %s", exc)
        return view_model.unavailable(
            state, BrokerError("the stored session could not be decrypted; "
                               "authorise again"))

    client = KiteBroker(api_key=settings.broker_api_key,
                        access_token=access_token)
    try:
        held = client.holdings()
        funds = client.margins()
    except BrokerError as exc:
        log.warning("broker read failed: %s", exc)
        return view_model.unavailable(state, exc)

    book = [
        {"symbol": h.symbol, "isin": h.isin, "quantity": h.quantity}
        for h in TradingHoldings(db).book(portfolio_id)
    ]
    report = demat_service.check_holdings(portfolio_id, book=book, demat=held)

    return view_model.View(
        session=state,
        rows=view_model.align(book, held),
        findings=report.findings,
        funds=funds,
    )
