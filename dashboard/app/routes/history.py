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
from algotrade.repositories.executions import (
    ExecutionRepository as TradingExecutions,
)
from algotrade.repositories.snapshots import SnapshotRepository as TradingSnapshots
from algotrade.services.performance import (
    MIN_SESSIONS_TO_ANNUALISE,
    PerformanceService,
)

from datetime import timedelta
from decimal import Decimal

from app import charts, clock, shadow
from app.db import get_db
from app.portfolio import resolve
from app.repositories.snapshots import SnapshotRepository
from app.settings import get_settings

router = APIRouter()

PAGE_SIZE = 60

#: The charts read every snapshot in this window. Unbounded, the page grew
#: without limit -- a decade of daily sessions is ~2,500 documents fetched to
#: draw two lines. Five years is longer than any drawdown the archive contains,
#: so it cannot hide the thing the page exists to show, and the table below
#: still pages through everything.
CHART_WINDOW_DAYS = 365 * 5


@router.get("/history", response_class=HTMLResponse)
def history(request: Request, page: int = Query(1, ge=1)) -> HTMLResponse:
    settings = get_settings()
    db = get_db(settings)
    selection = resolve(request, settings, db)

    snapshots = SnapshotRepository(db)
    series = snapshots.series(
        selection.active, since=clock.today() - timedelta(days=CHART_WINDOW_DAYS))
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

    # The shadow book, if one has been forked: the same strategy with every
    # instruction followed exactly. Absent until then, and the panel is simply
    # not rendered -- an empty comparison would imply one had been made.
    other_id = shadow.counterpart(selection.active, selection.options)
    divergence = comparison = None
    if other_id:
        other = snapshots.series(
            other_id, since=clock.today() - timedelta(days=CHART_WINDOW_DAYS))
        left, right = shadow.align(series, other)

        # `divergence` is directional -- its first argument is the TRADED book.
        # Viewing the shadow's own page must not relabel the shadow as traded
        # and flip the sign of the gap, so the pair is ordered here rather than
        # taken from whichever portfolio happens to be selected.
        is_shadow = selection.active.startswith("shadow_")
        traded_id = other_id if is_shadow else selection.active
        traded_series, obedient = (other, series) if is_shadow else (series, other)
        divergence = shadow.divergence(traded_series, obedient)

        if divergence and divergence["measured"]:
            # Only the traded book's fills. The shadow's are at the reference
            # price by construction, so its slippage is exactly zero and the
            # whole component is attributable to the book you trade.
            fills = TradingExecutions(db).between(
                traded_id, start=divergence["from"], end=divergence["to"])
            cost = sum(
                (f.value * Decimal(str(f.slippage_bps)) / Decimal(10_000)
                 for f in fills if f.slippage_bps is not None),
                Decimal(0),
            )
            traded_aligned, _ = shadow.align(traded_series, obedient)
            divergence["split"] = shadow.decompose(
                divergence["gap"], slippage_cost=cost,
                base_nav=(traded_aligned[0].get("nav") or Decimal(0))
                          if traded_aligned else Decimal(0))
        if len(left) >= 2:
            comparison = charts.comparison_chart(
                charts.to_points(left, x="date", y="unit_nav"),
                charts.to_points(right, x="date", y="unit_nav"),
                portfolio_label="This book",
                benchmark_label="Shadow" if not is_shadow else "Traded",
            )

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
            "shadow_id": other_id,
            "shadow_chart": comparison,
            "divergence": divergence,
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
