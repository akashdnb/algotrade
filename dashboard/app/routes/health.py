"""Liveness, and whether the numbers on the other pages can be trusted."""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app import clock
from app.db import get_db
from app.portfolio import resolve
from app.settings import get_settings

router = APIRouter()

#: Separate, so it can be mounted WITHOUT the auth dependency. `require_auth`
#: reads the settings, so an authenticated liveness probe cannot report on a
#: deployment whose settings are the problem.
public_router = APIRouter()

#: Beyond this, the book is being valued against prices old enough that the NAV
#: on the summary page is a historical figure rather than a current one.
STALE_SESSIONS = 2


@router.get("/health", response_class=HTMLResponse)
def health(request: Request) -> HTMLResponse:
    settings = get_settings()
    db = get_db(settings)

    selection = resolve(request, settings, db)
    latest = db["portfolio_snapshots"].find_one(
        {"portfolio_id": selection.active},
        {"date": 1, "price_date": 1}, sort=[("date", -1)],
    )
    last_session = latest.get("date") if latest else None

    # Recent job outcomes, most recent first. `synthetic` rows are rehearsal
    # artefacts, not events -- excluded for the same reason verify_recovery
    # excludes them.
    runs = list(db["job_runs"].find(
        {"synthetic": {"$ne": True}},
        {"job_name": 1, "business_date": 1, "status": 1, "trigger": 1,
         "error": 1, "finished_at": 1},
    ).sort("business_date", -1).limit(25))

    failed = [r for r in runs if r.get("status") == "failed"]

    behind = None
    if last_session:
        try:
            behind = _weekdays_between(date.fromisoformat(last_session), clock.today())
        except ValueError:
            behind = None

    return request.app.state.templates.TemplateResponse(
        request, "health.html",
        {
            "title": "Health",
            "settings": settings,
            "selection": selection,
            "last_session": last_session,
            "behind": behind,
            "stale": behind is not None and behind >= STALE_SESSIONS,
            "runs": runs,
            "failed": failed,
        },
    )


@public_router.get("/healthz", response_class=JSONResponse, include_in_schema=False)
def healthz() -> JSONResponse:
    """Liveness only, and unauthenticated.

    Touches no database and reads no settings, so it separates "the function
    is dead" from "the function is running but misconfigured or cut off from
    Atlas" -- a distinction that is worth most at exactly the moment nothing
    else responds.
    """
    return JSONResponse({"status": "ok"})


def _weekdays_between(start: date, end: date) -> int:
    """Trading days elapsed, weekends excluded. Holidays are not, because no
    trustworthy NSE calendar exists in this stack -- so this over-counts across
    a holiday rather than silently under-reporting staleness."""
    if end <= start:
        return 0
    days = 0
    cursor = start + timedelta(days=1)
    while cursor <= end:
        if cursor.weekday() < 5:
            days += 1
        cursor += timedelta(days=1)
    return days
