"""The position book: what is held, at what cost, and how it is taxed."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.db import get_db
from app.portfolio import resolve
from app.repositories.holdings import (
    HoldingRepository,
    average_cost,
    days_held,
    oldest_lot,
    tax_status,
)
from app.repositories.snapshots import SnapshotRepository
from app.settings import get_settings

router = APIRouter()

EQUAL_WEIGHT_TARGET = Decimal(1) / Decimal(30)


@router.get("/holdings", response_class=HTMLResponse)
def holdings(request: Request) -> HTMLResponse:
    settings = get_settings()
    db = get_db(settings)

    selection = resolve(request, settings, db)
    book = HoldingRepository(db).book(selection.active)
    snapshot = SnapshotRepository(db).latest(selection.active)

    # Closes live on the snapshot, cost basis lives on the holding. Joined in
    # Python: two small reads beat a $lookup, and the snapshot is already
    # loaded for the as-of date.
    positions = {p["symbol"]: p for p in (snapshot or {}).get("positions", [])}
    as_of = (snapshot or {}).get("date") or date.today()

    rows = []
    for holding in book:
        if holding.quantity <= 0:
            continue
        position = positions.get(holding.symbol, {})
        close = position.get("close")
        cost = average_cost(holding)
        value = position.get("value")
        unrealised = position.get("unrealised_pnl")

        rows.append({
            "symbol": holding.symbol,
            "quantity": holding.quantity,
            "average_cost": cost,
            "close": close,
            "value": value,
            "weight": float(position.get("weight") or 0),
            "unrealised": unrealised,
            "unrealised_pct": (
                float(unrealised / (cost * holding.quantity))
                if unrealised is not None and cost and holding.quantity else None
            ),
            "days_held": days_held(holding, as_of),
            "tax_status": tax_status(holding, as_of),
            "acquired_on": (lot.acquired_on if (lot := oldest_lot(holding)) else None),
            "lots": sorted(holding.lots, key=lambda x: x.acquired_on),
            "priced": close is not None,
        })

    # Largest first: the top of a 30-row table should be what moves the NAV.
    rows.sort(key=lambda r: (r["value"] or Decimal(0)), reverse=True)

    invested = sum((r["value"] or Decimal(0)) for r in rows)
    unpriced = [r["symbol"] for r in rows if not r["priced"]]

    return request.app.state.templates.TemplateResponse(
        request, "holdings.html",
        {
            "title": "Holdings",
            "settings": settings,
            "selection": selection,
            "rows": rows,
            "as_of": as_of,
            "invested": invested,
            "cash": (snapshot or {}).get("cash"),
            "target_weight": float(EQUAL_WEIGHT_TARGET),
            "unpriced": unpriced,
        },
    )
