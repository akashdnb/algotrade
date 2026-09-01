"""The audit trail — the page you open when a number looks wrong."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from app.db import get_db
from app.portfolio import resolve
from app.repositories.audit import AuditRepository
from app.settings import get_settings

router = APIRouter()

PAGE_SIZE = 50


@router.get("/audit", response_class=HTMLResponse)
def audit(
    request: Request,
    action: str = Query(""),
    entity: str = Query(""),
    severity: str = Query(""),
    before: str = Query(""),
) -> HTMLResponse:
    settings = get_settings()
    db = get_db(settings)
    selection = resolve(request, settings, db)

    cursor = None
    if before:
        try:
            cursor = datetime.fromisoformat(before)
            if cursor.tzinfo is None:
                cursor = cursor.replace(tzinfo=timezone.utc)
        except ValueError:
            cursor = None

    repo = AuditRepository(db)
    rows, next_cursor = repo.page(
        portfolio_id=selection.active, action=action.strip(),
        entity_id=entity.strip(), severity=severity.strip(),
        before=cursor, limit=PAGE_SIZE,
    )

    return request.app.state.templates.TemplateResponse(
        request, "audit.html",
        {
            "title": "Audit",
            "settings": settings,
            "selection": selection,
            "rows": rows,
            "actions": repo.actions(selection.active),
            "action": action.strip(),
            "entity": entity.strip(),
            "severity": severity.strip(),
            "next_cursor": next_cursor.isoformat() if next_cursor else "",
            "is_first_page": cursor is None,
        },
    )
