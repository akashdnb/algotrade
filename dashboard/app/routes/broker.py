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
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from algotrade.brokers import kite_auth, tokens
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
def broker_section(request: Request, message: str = "") -> HTMLResponse:
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
            "message": message,
            "can_login": settings.broker_login_ready,
        },
    )


@router.get("/broker/login")
def login(request: Request) -> RedirectResponse:
    """Hand the browser to Zerodha. Nothing is stored, nothing is sent.

    The credential exchange happens on their page, with their 2FA. This app
    never sees a password or a TOTP seed, which is both what Zerodha asks and
    the reason not to build a credentials form.
    """
    settings = get_settings()
    if not settings.broker_login_ready:
        return RedirectResponse(_back(request), status_code=303)
    return RedirectResponse(kite_auth.login_url(settings.broker_api_key),
                            status_code=303)


@router.get("/broker/callback")
def callback(request: Request, request_token: str = "",
             status: str = "") -> RedirectResponse:
    """Where Zerodha sends the browser back, carrying a `request_token`.

    A GET that changes state, which OAuth redirects require and which is
    survivable only because the request token is single-use and short-lived.
    The route is behind the same authentication as every other page, so it
    cannot be driven by someone who is not already signed in.
    """
    settings = get_settings()
    back = _back(request)

    if status and status != "success":
        return _with_message(back, "login was cancelled at the broker")
    if not request_token:
        return _with_message(back, "the broker returned no request token")
    if not settings.broker_login_ready:
        return _with_message(back, "broker credentials are not configured")

    try:
        access_token = kite_auth.exchange(
            api_key=settings.broker_api_key,
            api_secret=settings.broker_api_secret,
            request_token=request_token,
        )
        key = base64.b64decode(settings.broker_token_key, validate=True)
        ciphertext = tokens.encrypt(access_token, key=key)
    except (BrokerError, ValueError, tokens.TokenError) as exc:
        log.warning("broker login failed: %s", exc)
        return _with_message(back, str(exc))

    now = clock.now()
    BrokerSessionRepository(get_db(settings)).store(
        broker=settings.broker_name or "zerodha",
        ciphertext=ciphertext,
        expires_at=tokens.expires_after(now),
        now=now,
    )
    return RedirectResponse(back, status_code=303)


@router.post("/broker/logout")
def logout(request: Request) -> RedirectResponse:
    """Forget the session here, and end it at Kite too.

    POST because it changes state. Not gated by `allow_writes`: that flag
    guards writes to the *book*, and discarding a session touches nothing a
    valuation depends on. Refusing to let someone log out would be a strange
    reading of a safety switch.
    """
    settings = get_settings()
    db = get_db(settings)
    name = settings.broker_name or "zerodha"
    sessions = BrokerSessionRepository(db)

    ciphertext = sessions.live(name, now=clock.now())
    if ciphertext and settings.broker_token_key:
        try:
            key = base64.b64decode(settings.broker_token_key, validate=True)
            kite_auth.invalidate(api_key=settings.broker_api_key,
                                 access_token=tokens.decrypt(ciphertext, key=key))
        except (ValueError, tokens.TokenError) as exc:
            # Best effort. The local row goes either way -- a token we have
            # forgotten is unusable by us whatever Kite still thinks.
            log.warning("could not invalidate at the broker: %s", exc)

    sessions.drop(name)
    return RedirectResponse(_back(request), status_code=303)


def _back(request: Request) -> str:
    """Return to the section, preserving the portfolio selection."""
    query = request.url.query
    stripped = "&".join(
        part for part in query.split("&")
        if part and not part.startswith(("request_token=", "status=",
                                         "action=", "message="))
    )
    return f"/broker?{stripped}" if stripped else "/broker"


def _with_message(back: str, message: str) -> RedirectResponse:
    joiner = "&" if "?" in back else "?"
    return RedirectResponse(f"{back}{joiner}message={quote(message)}",
                            status_code=303)


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
