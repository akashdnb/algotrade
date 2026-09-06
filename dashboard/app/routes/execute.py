"""One order, one click.

Registered only when `EXECUTE_ENABLED` is set, and never on Vercel: order
placement needs a static IP registered with Zerodha, which serverless egress
does not have (design §4). Importing this module is a deliberate act.

**One button per instruction, never a batch.** Not caution for its own sake:
every sizing defect this system has had would have become a money defect
behind an "execute all", and a per-trade click means the first rejection stops
you after one order rather than after thirty.

**Acts on the frozen basket, never on a fresh one.** `/review` recomputes on
every load; this reads what was accepted, so the order sent is the order
approved.

The route decides nothing about correctness. `services/submission.py` owns the
place-at-most-once sequence, `services/execution_plan.py` owns what the order
is and whether it may go yet, and `brokers/wiring.py` owns which client this
portfolio may use -- checked again here, immediately before the irreversible
call, because the pairing was decided by a different function a moment ago.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from algotrade.brokers import wiring
from algotrade.brokers.base import BrokerError
from algotrade.domain.models import SetStatus
from algotrade.repositories.recommendations import RecommendationRepository
from algotrade.repositories.submissions import SubmissionRepository
from algotrade.services import execution_plan, settlement, submission
from algotrade.services.execution import FillRequest

from app import clock
from app.auth import issue_csrf_token, require_csrf
from app.recording import RecordingError, record_batch
from app.db import get_db
from app.portfolio import resolve
from app.settings import get_settings

router = APIRouter()

log = logging.getLogger("dashboard.execute")


@router.get("/execute", response_class=HTMLResponse)
def execute_page(request: Request, on: str = Query(""),
                 message: str = Query("")) -> HTMLResponse:
    """The frozen basket, with a button beside each instruction.

    A separate page from `/review` on purpose: reading and acting are
    different intentions, and a button on the page you use to think is a
    button you eventually press while thinking.
    """
    settings = get_settings()
    db = get_db(settings)
    selection = resolve(request, settings, db)
    session = _session(on)

    frozen = _frozen(db, selection.active)
    basket, lines, states = None, [], {}
    if frozen is not None:
        basket = frozen
        rows = [_row(i) for i in frozen.items]
        states = _states(db, frozen.set_id)
        lines = [
            {**row,
             "state": states.get(int(row["seq"]), ""),
             "gate": execution_plan.gate(row, states=states, lines=rows)}
            for row in rows
        ]

    return request.app.state.templates.TemplateResponse(
        request, "execute.html",
        {
            "title": "Execute",
            "settings": settings,
            "selection": selection,
            "on": session,
            "basket": basket,
            "lines": lines,
            "remaining": execution_plan.remaining(
                [_row(i) for i in basket.items] if basket else [], states),
            "message": message,
            "csrf_token": issue_csrf_token(),
            "live": wiring.is_live(selection.active),
        },
    )


@router.post("/execute/order", dependencies=[Depends(require_csrf)])
def place_one(
    request: Request,
    set_id: str = Form(...),
    seq: int = Form(...),
    on: str = Form(...),
) -> RedirectResponse:
    """Place exactly one order.

    Idempotent by construction: `submission.submit` writes its intent first
    and refuses to send twice for the same `(set_id, seq)`. A double click, a
    refresh, or a retry after a crash all resolve to the same single order.
    """
    settings = get_settings()
    db = get_db(settings)
    selection = resolve(request, settings, db)
    session = _session(on)

    frozen = _frozen(db, selection.active)
    if frozen is None or frozen.set_id != set_id:
        return _back(session, "no frozen basket for this session")

    rows = [_row(i) for i in frozen.items]
    line = next((r for r in rows if int(r["seq"]) == seq), None)
    if line is None:
        return _back(session, f"instruction {seq} is not in this basket")

    # Re-checked here, not trusted from the form: the page was drawn before
    # any of the sells were placed, so its buttons reflect a state that has
    # since moved on.
    allowed = execution_plan.gate(line, states=_states(db, set_id),
                                  lines=rows)
    if not allowed.allowed:
        return _back(session, f"{line['symbol']}: {allowed.reason}")

    try:
        client = wiring.client_for(
            selection.active,
            api_key=settings.broker_api_key,
            access_token=_access_token(settings),
        )
        wiring.check(selection.active, client)
        order = execution_plan.order_for(
            line, set_id=set_id,
            market_protection=_protection(settings))
        outcome = submission.submit(
            order, set_id=set_id, seq=seq, client=client,
            store=SubmissionRepository(db), now=clock.now(),
        )
    except (submission.SubmissionError, execution_plan.NotReady,
            wiring.WiringError, BrokerError, ValueError) as exc:
        log.warning("refused %s:%s -- %s", set_id, seq, exc)
        return _back(session, str(exc))

    log.info("submitted %s seq=%s tag=%s order=%s placed=%s",
             set_id, seq, outcome.tag, outcome.broker_order_id, outcome.placed)
    note = "" if outcome.placed else (
        f"{line['symbol']} was already placed; nothing was sent again")
    return _back(session, note)


# ------------------------------------------------------------- helpers ---

def _frozen(db, portfolio_id: str):
    """The approved revision -- the basket that was read and accepted.

    `APPROVED` is precisely "priced at the execution open and signed off".
    A `pending` set has not been through /review, and executing it would send
    quantities sized at the previous close -- the wrong price vector, which
    is the whole reason /review re-sizes.
    """
    repo = RecommendationRepository(db)
    for doc in repo.history(portfolio_id, limit=6):
        rec = repo.get_set(doc["_id"])
        if rec is not None and rec.status == SetStatus.APPROVED and rec.open_items:
            return rec
    return None


def _row(item) -> dict:
    """One instruction, in the shape `execution_plan` reads.

    `order_type` comes from the item -- decided when the basket was frozen and
    approved with it. Recomputing it here would apply a rule nobody signed off
    on to an order about to be sent.
    """
    return {"seq": item.seq, "action": str(item.action),
            "symbol": item.symbol, "quantity": item.quantity,
            "price": item.reference_price,
            "order_type": item.order_type or "LIMIT"}


def _states(db, set_id: str) -> dict[int, str]:
    return {int(row["item_seq"]): row["state"]
            for row in SubmissionRepository(db).for_set(set_id)}


def _session(given: str) -> date:
    if given:
        try:
            return date.fromisoformat(given)
        except ValueError:
            pass
    return clock.session_date()


def _protection(settings) -> Decimal:
    """Percent for MARKET orders. Kite rejects an order protected at zero, so
    a malformed setting must not quietly become one."""
    try:
        value = Decimal(str(settings.market_protection_pct))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(
            f"MARKET_PROTECTION_PCT is not a number: "
            f"{settings.market_protection_pct!r}") from exc
    if value <= 0:
        raise ValueError("MARKET_PROTECTION_PCT must be greater than zero; "
                         "the broker rejects unprotected market orders")
    return value


def _access_token(settings) -> str:
    """Credentials for a live book. Empty until a live book exists.

    `wiring.client_for` refuses a live book without them rather than falling
    back to the stub -- which would record real trades against nothing.
    """
    return ""


def _back(session: date, message: str = "") -> RedirectResponse:
    target = f"/execute?on={session.isoformat()}"
    if message:
        target = f"{target}&message={quote(message)}"
    return RedirectResponse(target, status_code=303)


@router.post("/execute/settle", dependencies=[Depends(require_csrf)])
def settle_fills(
    request: Request,
    set_id: str = Form(...),
    on: str = Form(...),
) -> RedirectResponse:
    """Ask the broker what became of the orders, and record what did happen.

    Separate from placing, and deliberately so. Placing is irreversible and
    one-at-a-time; settling reads and is safe to repeat -- an order already
    recorded is a no-op, because `record_batch` treats a repeated
    `broker_order_id` as one it has already seen.

    Only terminal orders are recorded. Everything still open is reported and
    left alone, which is most of a session.
    """
    settings = get_settings()
    db = get_db(settings)
    selection = resolve(request, settings, db)
    session = _session(on)

    sent = SubmissionRepository(db).for_set(set_id)
    if not sent:
        return _back(session, "nothing has been placed for this basket yet")

    try:
        client = wiring.client_for(
            selection.active,
            api_key=settings.broker_api_key,
            access_token=_access_token(settings),
        )
        wiring.check(selection.active, client)
        result = settlement.settle(sent, client=client,
                                   charge_for=_charge_for())
    except (wiring.WiringError, BrokerError) as exc:
        return _back(session, str(exc))

    if result.unreachable:
        # Never read as "nothing filled": the orders may well have filled and
        # we simply cannot see them.
        return _back(session, f"the broker did not answer: {result.unreachable}")

    if not result.fills:
        waiting = len(result.pending)
        return _back(session, f"nothing terminal yet ({waiting} still open)")

    now = clock.now()
    try:
        outcome = record_batch(
            db, set_id=set_id,
            fills=[_as_fill_request(f, now) for f in result.fills],
            batch_id=f"settle:{set_id}:{session.isoformat()}", now=now,
            business_date=session,
        )
    except RecordingError as exc:
        return _back(session, f"the fills could not be recorded: {exc}")

    repo = SubmissionRepository(db)
    for tag, state in result.states.items():
        repo.settle(tag=tag, state=state, now=now)

    log.info("settled %s: %d fills recorded, %d still open",
             set_id, len(result.fills), len(result.pending))
    note = f"recorded {len(result.fills)}"
    if result.pending:
        note += f"; {len(result.pending)} still open at the broker"
    return _back(session, note)


def _as_fill_request(fill, now) -> FillRequest:
    """A broker outcome as the book's own vocabulary.

    A rejection becomes a SKIP carrying the broker's reason, never a
    zero-quantity execution -- one is what happened, the other is a trade that
    never did.
    """
    if fill.is_skip:
        return FillRequest(seq=fill.seq, skip=fill.skipped,
                           broker_order_id=fill.broker_order_id)
    return FillRequest(
        seq=fill.seq, quantity=fill.quantity, price=fill.price,
        charges=fill.charges, broker_order_id=fill.broker_order_id,
        executed_at=fill.filled_at or now,
        note="settled from the broker",
    )


def _charge_for():
    """Rate-card charges, or None if the card cannot be loaded.

    None means charges record as zero, which UNDERSTATES cost. That is
    corrected by the next-day pass against the contract note (design §9), and
    is better than blocking a settlement over a config file.
    """
    try:
        from algotrade.config.settings import REPO_ROOT
        from algotrade.core.costs import load_rate_cards, order_charges
        card = load_rate_cards(REPO_ROOT / "config" / "costs.yaml")["delivery"]
    except Exception as exc:              # pragma: no cover - config only
        log.warning("cost card unavailable; charges will record as zero: %s", exc)
        return None

    from algotrade.domain import money as _money

    def charge_for(value, side):
        # order_charges works in float; money.rupees REFUSES floats, so the
        # conversion is explicit here rather than laundered.
        return _money.rupees(
            f"{order_charges(float(value), side, card).total:.2f}")

    return charge_for
