"""Fills, filterable, with a financial-year CSV export for tax."""

from __future__ import annotations

import csv
import io
from datetime import date

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, StreamingResponse

from app import clock
from app.db import get_db
from app.portfolio import resolve
from app.repositories.executions import (
    ExecutionRepository,
    financial_year,
    fy_label,
)
from app.settings import get_settings

router = APIRouter()

PAGE_SIZE = 50


def _filters(fy: int | None, symbol: str, side: str) -> dict:
    return {"fy": fy, "symbol": symbol.upper().strip(), "side": side.strip().lower()}


@router.get("/executions", response_class=HTMLResponse)
def executions(
    request: Request,
    page: int = Query(1, ge=1),
    fy: int | None = Query(None),
    symbol: str = Query(""),
    side: str = Query(""),
) -> HTMLResponse:
    settings = get_settings()
    db = get_db(settings)
    selection = resolve(request, settings, db)

    repo = ExecutionRepository(db)
    years = repo.financial_years(selection.active)

    # Default to the current financial year when there is one, rather than
    # everything: the tax question is always asked about a single year.
    if fy is None and years:
        current = financial_year(clock.today())
        fy = current if current in years else years[0]

    f = _filters(fy, symbol, side)
    result = repo.page(selection.active, limit=PAGE_SIZE,
                       offset=(page - 1) * PAGE_SIZE, **f)
    totals = repo.totals(result.items)

    return request.app.state.templates.TemplateResponse(
        request, "executions.html",
        {
            "title": "Executions",
            "settings": settings,
            "selection": selection,
            "page": result,
            "totals": totals,
            # Labelled here rather than in the template: the label is
            # "2026-27", which template string-slicing got wrong.
            "years": [{"year": y, "label": fy_label(y)} for y in years],
            "fy": fy,
            "fy_label": fy_label(fy) if fy is not None else "all",
            "symbol": f["symbol"],
            "side": f["side"],
        },
    )


@router.get("/executions.csv")
def executions_csv(
    request: Request,
    fy: int | None = Query(None),
    symbol: str = Query(""),
    side: str = Query(""),
) -> StreamingResponse:
    """The whole financial year, never a page of it.

    A tax export that silently stopped at the first fifty rows would be worse
    than no export at all -- it would look complete.
    """
    settings = get_settings()
    db = get_db(settings)
    selection = resolve(request, settings, db)

    repo = ExecutionRepository(db)
    if fy is None:
        years = repo.financial_years(selection.active)
        if not years:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No executions to export")
        current = financial_year(clock.today())
        fy = current if current in years else years[0]

    f = _filters(fy, symbol, side)
    rows = repo.all_for_fy(selection.active, fy=fy,
                           symbol=f["symbol"], side=f["side"])

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "executed_on", "symbol", "isin", "side", "quantity", "price",
        "value", "charges", "reference_price", "slippage_bps",
        "broker_order_id", "recommendation_set", "execution_id",
    ])
    for row in rows:
        price = row.get("price")
        quantity = row.get("quantity") or 0
        writer.writerow([
            row.get("executed_on"), row.get("symbol"), row.get("isin", ""),
            row.get("action"), quantity,
            # Plain decimal strings: a spreadsheet must not have to parse a
            # rupee sign, and a float here would lose paise.
            f"{price:.2f}" if price is not None else "",
            f"{price * quantity:.2f}" if price is not None else "",
            f"{row.get('charges'):.2f}" if row.get("charges") is not None else "",
            f"{row.get('reference_price'):.2f}"
            if row.get("reference_price") is not None else "",
            row.get("slippage_bps", ""),
            row.get("broker_order_id", ""), row.get("set_id", ""), row.get("_id"),
        ])

    name = f"executions-{selection.active}-FY{fy_label(fy)}.csv"
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
