"""The one page you actually open: what is it worth, and how far down is it."""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app import charts
from app.db import get_db
from app.portfolio import resolve
from app.policy import EXPECTED_PAIN
from app.repositories.portfolios import PortfolioRepository
from app.repositories.snapshots import SnapshotRepository
from app.settings import get_settings

router = APIRouter()

CHART_WINDOW_DAYS = 365


@router.get("/", response_class=HTMLResponse)
def summary(request: Request) -> HTMLResponse:
    settings = get_settings()
    db = get_db(settings)

    selection = resolve(request, settings, db)

    snapshots = SnapshotRepository(db)
    latest = snapshots.latest(selection.active)
    portfolio = PortfolioRepository(db).get(selection.active)

    series = snapshots.series(
        selection.active,
        since=date.today() - timedelta(days=CHART_WINDOW_DAYS),
    )

    nav_points = charts.to_points(series, x="date", y="nav")
    drawdown_points = charts.to_points(series, x="date", y="drawdown")
    benchmark_points = charts.to_points(series, x="date", y="benchmark_close")

    # A carried-forward price on a weekend or holiday looks exactly like a flat
    # market, which is a materially different thing to be told.
    carried = bool(
        latest and latest.get("price_date") and latest.get("date")
        and latest["price_date"] != latest["date"]
    )

    # `benchmark_close` is declared in the snapshot schema but nothing writes
    # it yet (PROJECT.md next step 6). Detect rather than assume, so the
    # comparison appears on its own the day it starts being populated.
    has_benchmark = any(r.get("benchmark_close") is not None for r in series)

    return request.app.state.templates.TemplateResponse(
        request, "summary.html",
        {
            "title": "Summary",
            "settings": settings,
            "selection": selection,
            "snapshot": latest,
            "portfolio": portfolio,
            "sessions": len(series),
            "nav_points": nav_points,
            "drawdown_points": drawdown_points,
            "carried_forward": carried,
            # Reference line on the NAV chart: what was paid in, so the gap
            # between the line and the level IS the profit, readable at a
            # glance without arithmetic.
            "contributed": (
                float(portfolio["total_contributed"])
                if portfolio and portfolio.get("total_contributed") else None
            ),
            "has_benchmark": has_benchmark,
            "benchmark_points": benchmark_points,
            "pain": EXPECTED_PAIN,
        },
    )
