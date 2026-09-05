"""Application factory.

Three layers, one direction of dependency:

    routes/  ->  repositories/  ->  MongoDB
       |
       +------>  templates/            (rendering only, never a query)

A Jinja template that can reach the database is how one page silently becomes
twenty round trips, so templates receive finished data and nothing else.
"""

from __future__ import annotations

import html
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import charts, formatting
from app.auth import require_auth
from app.routes import (
    audit, broker, executions, health, history, holdings, rebalances,
    recommendations, review, summary,
)
from app.settings import ConfigError

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
        # Auth is applied PER ROUTER below, not here. An app-level dependency
        # also guards /healthz, and `require_auth` reads the settings -- so a
        # missing environment variable made the liveness probe fail too, which
        # is precisely the case it exists to diagnose.
    )

    # Served BY THE APP, not from a CDN. Vercel promotes mounted static
    # directories to its CDN at build time, and CDN-served files bypass the
    # function entirely -- so the middleware below and the auth dependency
    # above would not run for them. Vercel detects top-level middleware and
    # keeps the mount in the function for exactly this reason; the security
    # headers are therefore load-bearing beyond the headers themselves.
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
        "comparison_chart": charts.comparison_chart,
        "weight_bar": charts.weight_bar,
    })
    app.state.templates = templates

    for module in (summary, holdings, recommendations, executions,
                   history, rebalances, review, broker, audit, health):
        app.include_router(module.router, dependencies=[Depends(require_auth)])

    # Unauthenticated on purpose, and the only such route: it reads no
    # settings, opens no connection and returns a constant, so it answers
    # "is the function even alive?" when everything else cannot.
    app.include_router(health.public_router)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        # The page shows a live portfolio; it should never sit in a shared cache.
        response.headers.setdefault("Cache-Control", "private, no-store")
        return response

    @app.exception_handler(ConfigError)
    async def on_config_error(request: Request, exc: ConfigError) -> HTMLResponse:
        """A misconfigured deployment must say SO.

        This used to fall through to the handler below, which rendered a
        template that reads the settings -- the very thing that had failed --
        so the handler raised too and the platform returned a bare "Internal
        Server Error". A missing variable was indistinguishable from a crash.

        The message names a variable, never a value, so it is safe to show.
        """
        return HTMLResponse(
            "<!doctype html><meta charset=utf-8>"
            "<title>Not configured</title>"
            "<style>body{font:16px/1.6 system-ui;margin:3rem auto;max-width:34rem;"
            "padding:0 1rem}code{background:#eee;padding:.1rem .3rem}</style>"
            f"<h1>Not configured</h1><p><code>{html.escape(str(exc))}</code></p>"
            "<p>Set the environment variables on the deployment and redeploy. "
            "See <code>docs/deployment/dashboard.md</code>.</p>",
            status_code=500,
        )

    @app.exception_handler(Exception)
    async def on_error(request: Request, exc: Exception) -> HTMLResponse:
        # Never render a traceback: it can contain the connection string. And
        # never touch the settings or a template that inherits base.html --
        # an error handler that can itself fail leaves you with no message at
        # all, which is how this one behaved on its first real deployment.
        return HTMLResponse(
            "<!doctype html><meta charset=utf-8><title>Error</title>"
            "<style>body{font:16px/1.6 system-ui;margin:3rem auto;max-width:34rem;"
            "padding:0 1rem}</style>"
            "<h1>Something failed</h1>"
            "<p>The detail is deliberately omitted: a stack trace here could "
            "contain the database connection string. Check the deployment "
            "logs.</p>",
            status_code=500,
        )

    return app
