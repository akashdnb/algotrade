"""Performance over time: unit NAV, drawdown, and the measured metrics.

Every number here comes from the trading system's own `PerformanceService`
rather than being recomputed. That matters for one reason in particular: it
already refuses to annualise below 60 sessions, because a -0.67% move over
three days annualises to -70% CAGR -- arithmetically correct, meaningless, and
alarming. A dashboard that recomputed naively would undo that protection.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from algotrade.repositories.cash import CashRepository
from algotrade.repositories.snapshots import SnapshotRepository as TradingSnapshots
from algotrade.services.performance import (
    MIN_SESSIONS_TO_ANNUALISE,
    PerformanceService,
)

from app import charts
from app.db import get_db
from app.portfolio import resolve
from app.repositories.snapshots import SnapshotRepository
from app.settings import get_settings

router = APIRouter()

PAGE_SIZE = 60


@router.get("/history", response_class=HTMLResponse)
def history(request: Request, page: int = Query(1, ge=1)) -> HTMLResponse:
    settings = get_settings()
    db = get_db(settings)
    selection = resolve(request, settings, db)

    snapshots = SnapshotRepository(db)
    series = snapshots.series(selection.active)
    result = snapshots.page(selection.active, limit=PAGE_SIZE,
                            offset=(page - 1) * PAGE_SIZE)

    performance = PerformanceService(
        snapshots=TradingSnapshots(db), cash=CashRepository(db),
    ).compute(selection.active)

    # Unit NAV, never raw NAV: a contribution buys units at the prevailing
    # price and so does not move it. Charting raw NAV would draw every deposit
    # as a gain, which is the specific dishonesty unit accounting exists to
    # prevent.
    unit_nav_points = charts.to_points(series, x="date", y="unit_nav")
    drawdown_points = charts.to_points(series, x="date", y="drawdown")
    monthly = _monthly_returns(series)

    return request.app.state.templates.TemplateResponse(
        request, "history.html",
        {
            "title": "History",
            "settings": settings,
            "selection": selection,
            "performance": performance,
            "sessions": len(series),
            "annualisable": len(series) >= MIN_SESSIONS_TO_ANNUALISE,
            "min_sessions": MIN_SESSIONS_TO_ANNUALISE,
            "unit_nav_points": unit_nav_points,
            "drawdown_points": drawdown_points,
            "monthly": monthly,
            "page": result,
        },
    )


def _monthly_returns(series: list[dict]) -> list[dict]:
    """Compounded unit-NAV return per calendar month.

    From unit NAV for the same reason the chart is: a month containing a
    contribution would otherwise show a return the strategy did not earn.
    """
    by_month: dict[str, list] = {}
    for row in series:
        unit_nav, day = row.get("unit_nav"), row.get("date")
        if unit_nav is None or day is None:
            continue
        by_month.setdefault(f"{day.year:04d}-{day.month:02d}", []).append(unit_nav)

    out = []
    for month, values in sorted(by_month.items()):
        first, last = values[0], values[-1]
        if not first:
            continue
        out.append({"month": month, "ret": float(last / first) - 1.0})
    return out
