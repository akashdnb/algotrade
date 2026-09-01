"""Rebalance history, and the acceptance rate.

The table is the least interesting thing here. The number that matters is
**acceptance rate** — executed ÷ (executed + skipped). §9's behavioural audit
turns on it: if the trades you declined would have outperformed the ones you
took, your discretion is destroying alpha, and this is the only place that
becomes visible.

It needs a denominator, so it stays hidden until something has actually been
declined. A rate of "100%" computed from zero refusals is not a measurement.
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse

from algotrade.repositories.recommendations import RecommendationRepository

from app.db import get_db
from app.portfolio import resolve
from app.repositories.base import Page, decode
from app.settings import get_settings

router = APIRouter()

PAGE_SIZE = 24


@router.get("/rebalances", response_class=HTMLResponse)
def rebalances(request: Request, page: int = Query(1, ge=1)) -> HTMLResponse:
    settings = get_settings()
    db = get_db(settings)
    selection = resolve(request, settings, db)

    query = {"portfolio_id": selection.active}
    total = db["rebalance_runs"].count_documents(query)
    runs = [
        decode(d, dates=("as_of_session",), instants=("started_at", "completed_at"))
        for d in db["rebalance_runs"].find(query).sort("period", -1)
        .skip((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)
    ]

    recs = RecommendationRepository(db)
    rows, totals = [], {"executed": 0, "skipped": 0, "pending": 0,
                        "instructions": 0}
    for run in runs:
        rec = None
        if run.get("recommendation_set_id"):
            rec = recs.get_set(run["recommendation_set_id"])
        stats = _item_stats(rec)
        for key in totals:
            totals[key] += stats[key]
        rows.append({"run": run, "rec": rec, **stats})

    decided = totals["executed"] + totals["skipped"]
    return request.app.state.templates.TemplateResponse(
        request, "rebalances.html",
        {
            "title": "Rebalances",
            "settings": settings,
            "selection": selection,
            "rows": rows,
            "page": Page(rows, total, PAGE_SIZE, (page - 1) * PAGE_SIZE),
            "totals": totals,
            # Hidden until something has been declined: a rate computed from
            # zero refusals says nothing about discretion.
            "acceptance_rate": (totals["executed"] / decided) if decided else None,
            "has_declines": totals["skipped"] > 0,
        },
    )


@router.get("/rebalances/{run_id:path}", response_class=HTMLResponse)
def rebalance_detail(request: Request, run_id: str) -> HTMLResponse:
    settings = get_settings()
    db = get_db(settings)
    selection = resolve(request, settings, db)

    run = db["rebalance_runs"].find_one({"_id": run_id})
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such rebalance run")
    run = decode(run, dates=("as_of_session",),
                 instants=("started_at", "completed_at"))

    rec = None
    if run.get("recommendation_set_id"):
        rec = RecommendationRepository(db).get_set(run["recommendation_set_id"])

    # Both are lists of holding/target DICTS, not bare symbols -- the target
    # carries rank, quantity and the momentum feature. Pull the symbol out
    # rather than assuming the shape.
    current = _symbols(run.get("current"))
    target = _symbols(run.get("target"))

    return request.app.state.templates.TemplateResponse(
        request, "rebalance_detail.html",
        {
            "title": "Rebalance",
            "settings": settings,
            "selection": selection,
            "run": run,
            "rec": rec,
            "entering": sorted(target - current),
            "leaving": sorted(current - target),
            "held": sorted(current & target),
            # panel hash + params hash + commit. These three make a
            # recommendation reproducible (§9.2) and are the first thing you
            # would want if one ever looked wrong.
            "fingerprint": run.get("input_fingerprint") or {},
            **_item_stats(rec),
        },
    )


def _symbols(entries) -> set[str]:
    """Symbols from a list of dicts, tolerating a bare-string list too."""
    out: set[str] = set()
    for entry in entries or []:
        if isinstance(entry, dict):
            symbol = entry.get("symbol")
            if symbol:
                out.add(symbol)
        elif entry:
            out.add(str(entry))
    return out


def _item_stats(rec) -> dict:
    if rec is None:
        return {"executed": 0, "skipped": 0, "pending": 0, "instructions": 0,
                "turnover_value": Decimal(0), "charges": Decimal(0)}

    executed = sum(1 for i in rec.items if i.status == "executed")
    skipped = sum(1 for i in rec.items if i.status == "skipped")
    pending = sum(1 for i in rec.items if i.is_open)
    return {
        "executed": executed,
        "skipped": skipped,
        "pending": pending,
        # NOT "items": Jinja resolves `totals.items` to dict.items, the bound
        # method, and renders "<built-in method items of dict object>".
        "instructions": len(rec.items),
        "turnover_value": sum((i.executed_value for i in rec.items), Decimal(0)),
        "charges": sum((i.executed_charges for i in rec.items), Decimal(0)),
    }
