"""Recommendations, and the form that records what was actually filled."""

from __future__ import annotations

import secrets
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from algotrade.repositories.recommendations import RecommendationRepository

from app.auth import issue_csrf_token, require_csrf, require_writes_enabled
from app.db import get_db
from app.portfolio import resolve
from app.recording import (
    RecordingError,
    build_fills,
    open_items,
    outstanding_quantity,
    record_batch,
)
from app.settings import get_settings

router = APIRouter()


@router.get("/recommendations", response_class=HTMLResponse)
def recommendation_list(request: Request) -> HTMLResponse:
    settings = get_settings()
    db = get_db(settings)
    selection = resolve(request, settings, db)
    history = RecommendationRepository(db).history(selection.active, limit=24)

    return request.app.state.templates.TemplateResponse(
        request, "recommendations.html",
        {"title": "Recommendations", "settings": settings,
         "selection": selection, "sets": history},
    )


# Plain `{set_id}`, not `{set_id:path}`: the path converter matches slashes,
# so it swallowed the `/record` suffix and made the write routes unreachable
# (they 404'd instead of running). Set ids are `portfolio:period:rN` -- colons,
# never slashes -- so the default converter is both correct and stricter.
@router.get("/recommendations/{set_id}", response_class=HTMLResponse)
def recommendation_detail(request: Request, set_id: str) -> HTMLResponse:
    settings = get_settings()
    db = get_db(settings)
    rec = RecommendationRepository(db).get_set(set_id)
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such recommendation")

    selection = resolve(request, settings, db)
    items = sorted(rec.items, key=lambda i: (i.action != "sell", i.seq))
    pending = open_items(rec)

    return request.app.state.templates.TemplateResponse(
        request, "recommendation_detail.html",
        {
            "title": "Recommendation",
            "settings": settings,
            "selection": selection,
            "rec": rec,
            "items": items,
            "open_count": len(pending),
            # The form is only offered when there is something left to record
            # AND writes are switched on. A submit button that always fails is
            # worse than no button.
            "can_record": settings.allow_writes and bool(pending),
            "outstanding": outstanding_quantity,
        },
    )


@router.get("/recommendations/{set_id}/record", response_class=HTMLResponse)
def record_form(
    request: Request, set_id: str, _: None = Depends(require_writes_enabled),
) -> HTMLResponse:
    settings = get_settings()
    db = get_db(settings)
    rec = RecommendationRepository(db).get_set(set_id)
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such recommendation")

    items = open_items(rec)
    if not items:
        return RedirectResponse(f"/recommendations/{set_id}",
                                status_code=status.HTTP_303_SEE_OTHER)

    return request.app.state.templates.TemplateResponse(
        request, "record.html",
        {
            "title": "Record fills",
            "settings": settings,
            "rec": rec,
            "items": items,
            "outstanding": outstanding_quantity,
            "csrf_token": issue_csrf_token(),
            # Minted when the form is rendered, so every re-render is a new
            # submission and a re-POST of the same page is not.
            "batch_id": secrets.token_urlsafe(16),
            "today": date.today().isoformat(),
        },
    )


@router.post("/recommendations/{set_id}/record", response_class=HTMLResponse)
async def record_submit(
    request: Request,
    set_id: str,
    _writes: None = Depends(require_writes_enabled),
    _csrf: None = Depends(require_csrf),
) -> HTMLResponse:
    settings = get_settings()
    db = get_db(settings)
    templates = request.app.state.templates

    form = dict(request.state.form)      # already read by the CSRF dependency
    rec = RecommendationRepository(db).get_set(set_id)
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such recommendation")

    batch_id = str(form.get("batch_id", "")).strip()
    if not batch_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing batch id")

    business_date = None
    if raw_date := str(form.get("executed_on", "")).strip():
        try:
            business_date = date.fromisoformat(raw_date)
        except ValueError:
            business_date = None

    def _reject(message: str) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "record.html",
            {
                "title": "Record fills",
                "settings": settings,
                "rec": rec,
                "items": open_items(rec),
                "outstanding": outstanding_quantity,
                "csrf_token": issue_csrf_token(),
                "batch_id": batch_id,
                "today": business_date.isoformat() if business_date
                         else date.today().isoformat(),
                "error": message,
                "submitted": form,
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        fills = build_fills(rec, form, batch_id=batch_id)
        outcome = record_batch(
            db, set_id=set_id, fills=fills, batch_id=batch_id,
            now=datetime.now(timezone.utc), business_date=business_date,
        )
    except RecordingError as exc:
        # Nothing was written: validation happens before the first insert, and
        # an execution and its ledger entry are written together or not at all.
        return _reject(str(exc))

    return templates.TemplateResponse(
        request, "record_result.html",
        {
            "title": "Recorded",
            "settings": settings,
            "rec": rec,
            "outcome": outcome,
        },
        status_code=status.HTTP_200_OK,
    )
