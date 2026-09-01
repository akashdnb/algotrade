"""Application factory.

Three layers, one direction of dependency:

    routes/  ->  repositories/  ->  MongoDB
       |
       +------>  templates/            (rendering only, never a query)

A Jinja template that can reach the database is how one page silently becomes
twenty round trips, so templates receive finished data and nothing else.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import charts, formatting
from app.auth import require_auth
from app.routes import health, holdings, summary
from app.settings import get_settings

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

#: `script-src 'none'` is possible only because every chart is server-rendered
#: SVG. It is the strongest guarantee available that a page showing your
#: holdings cannot execute anything.
CSP = (
    "default-src 'self'; script-src 'none'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; form-action 'self'; frame-ancestors 'none'; "
    "base-uri 'none'"
)


def create_app() -> FastAPI:
    app = FastAPI(
        title="algotrade",
        # No public API surface: the dashboard is the only consumer, and an
        # unauthenticated /openapi.json would describe every route.
        docs_url=None, redoc_url=None, openapi_url=None,
        dependencies=[Depends(require_auth)],
    )

    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

    templates = Jinja2Templates(directory=str(HERE / "templates"))
    templates.env.filters.update({
        "rupees": formatting.rupees,
        "signed_rupees": formatting.signed_rupees,
        "pct": formatting.pct,
        "signed_pct": formatting.signed_pct,
        "day": formatting.day,
        "ago": formatting.ago,
        "qty": formatting.qty,
    })
    templates.env.globals.update({
        "line_chart": charts.line_chart,
        "drawdown_chart": charts.drawdown_chart,
        "weight_bar": charts.weight_bar,
    })
    app.state.templates = templates

    for module in (summary, holdings, health):
        app.include_router(module.router)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        # The page shows a live portfolio; it should never sit in a shared cache.
        response.headers.setdefault("Cache-Control", "private, no-store")
        return response

    @app.exception_handler(Exception)
    async def on_error(request: Request, exc: Exception) -> HTMLResponse:
        # Never render a traceback: it can contain the connection string.
        return templates.TemplateResponse(
            request, "error.html",
            {"detail": "Something failed while loading this page.",
             "settings": get_settings()},
            status_code=500,
        )

    return app
