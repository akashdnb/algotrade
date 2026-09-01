# Dashboard — Implementation Plan

**Status: BUILT and DEPLOYED 2026-09-01.** All eight pages exist and are live.
Kept as the design record and as the reasoning behind decisions that are not
obvious from the code; `dashboard/README.md` is the current description.

Two things here were overtaken by events and are worth naming rather than
silently editing:

* **"A separate repository, `algotrade-dashboard`"** — it lives in *this*
  repository instead. Once the dashboard had to record executions, it needed
  the trading system's own `ExecutionService`, `HoldingsService` and
  `ReconciliationService`; a second implementation of the FIFO rebuild and the
  cash ledger would drift, and the drift is silent until the book is already
  wrong.
* **"Read-only"** — the write path exists (`/recommendations/{id}/record`) but
  is **off** unless `DASHBOARD_ALLOW_WRITES` is set. §13 below rules writes out
  entirely; that was reconsidered deliberately, because recording a rebalance
  by hand meant up to 30 `workflow_dispatch` runs of nine fields each.

View over the trading system's MongoDB Atlas database. FastAPI + Jinja2,
server-rendered, no React.

---

## 0. Four decisions to make before writing code

### 0.1 Serverless + Atlas is the whole risk

Everything else here is routine. This is not.

Vercel runs each request in a short-lived function. The naive FastAPI app opens
a `MongoClient` per request, and under any concurrency that exhausts Atlas M0's
**500-connection ceiling** while adding ~1–2 s of TLS handshake to every page
load — you measured 2.4 s cold latency to this cluster already.

Three things make it work:

```python
# Module-level, so warm invocations reuse the pool. Vercel keeps a container
# alive between nearby requests; this is the only reason the app is usable.
_client: MongoClient | None = None

def get_db() -> Database:
    global _client
    if _client is None:
        _client = MongoClient(
            settings.mongodb_uri,
            maxPoolSize=1,            # one function = one concurrent request
            minPoolSize=0,            # do not hold connections open when idle
            serverSelectionTimeoutMS=5_000,
            connectTimeoutMS=5_000,
        )
    return _client[settings.mongodb_database]
```

- **`maxPoolSize=1`** — a serverless invocation serves one request; a pool of
  100 is 100 idle sockets against a 500 ceiling.
- **Module-level client** — created on cold start, reused while the container
  lives.
- **Never call `client.close()`** in a request handler. That is correct in a
  script and wrong here; it throws away the pool the next request needs.

> **If page latency turns out to be unacceptable**, the fallback is not caching
> — it is `data/runtime/*.json`, which `publish_runtime.py` already writes
> daily. The data only changes once a day, so a static read is not stale in any
> way that matters. Keeping that option open is why §3 puts every query behind
> a repository interface.

### 0.2 Read-only must be enforced, not intended

"No writes initially" is a statement of intent. Intent does not survive a
refactor.

**Create a second Atlas database user** with the built-in `read` role on
`algotrade_paper` only. Then a write is impossible rather than merely absent,
and the dashboard cannot damage the trading system even if its code is wrong or
its credential leaks.

```
Atlas → Database Access → Add New Database User
  username: algotrade_dashboard
  role:     read @ algotrade_paper
```

Never reuse the trading system's `readWrite` credential.

### 0.3 The URL is public unless you make it otherwise

A Vercel deployment URL is guessable and indexed. Without auth, your portfolio
NAV, holdings and broker order ids are public. Vercel's built-in password
protection is a **Pro feature**; on Hobby you add it yourself.

HTTP Basic Auth in middleware, over Vercel's automatic TLS, is sufficient for a
single-user read-only dashboard. §8 has the implementation.

### 0.4 Charts without JavaScript

Chart.js means a CDN script, a canvas, and a client-side render for a chart
whose data changes once a day. **Server-rendered inline SVG** is ~40 lines,
loads instantly on mobile, works with JS disabled, and has no CDN dependency to
break. Use it for the NAV line and drawdown fill. Reach for a JS library only
if you later want zoom or hover — and by then you will know whether you use the
dashboard enough to care.

---

## 1. Repository structure

```
algotrade-dashboard/
├── api/
│   └── index.py               # Vercel entrypoint: `app = create_app()`
├── app/
│   ├── __init__.py
│   ├── main.py                # create_app(), middleware, router registration
│   ├── settings.py            # env → typed Settings, fail fast
│   ├── db.py                  # the cached client (§0.1)
│   ├── auth.py                # basic-auth dependency
│   ├── formatting.py          # rupees, percentages, dates — Jinja filters
│   ├── charts.py              # inline-SVG sparkline / line / drawdown
│   ├── repositories/
│   │   ├── base.py            # decimal decoding, pagination helper
│   │   ├── portfolios.py
│   │   ├── holdings.py
│   │   ├── snapshots.py
│   │   ├── recommendations.py
│   │   ├── executions.py
│   │   ├── rebalances.py
│   │   ├── cash.py
│   │   ├── audit.py
│   │   └── health.py          # job_runs + notifications + freshness
│   ├── routes/
│   │   ├── summary.py
│   │   ├── holdings.py
│   │   ├── recommendations.py
│   │   ├── executions.py
│   │   ├── history.py
│   │   ├── rebalances.py
│   │   ├── health.py
│   │   └── audit.py
│   └── templates/
│       ├── base.html
│       ├── _nav.html
│       ├── _pagination.html
│       ├── _money.html
│       ├── summary.html
│       ├── holdings.html
│       ├── recommendations.html
│       ├── recommendation_detail.html
│       ├── executions.html
│       ├── history.html
│       ├── rebalances.html
│       ├── health.html
│       ├── audit.html
│       └── error.html
├── static/
│   └── style.css              # one file, ~200 lines, no framework
├── tests/
│   ├── conftest.py            # in-memory fake repositories
│   ├── test_formatting.py
│   ├── test_charts.py
│   ├── test_routes.py         # TestClient against fakes — no database
│   └── test_repositories.py   # integration, skipped without a URI
├── vercel.json
├── requirements.txt
├── .env.example
└── README.md
```

**Why `api/index.py` is separate from `app/`:** Vercel's Python runtime treats
files under `api/` as function entrypoints. Keeping the application in `app/`
means it is importable and testable without Vercel in the picture.

---

## 2. FastAPI architecture

Three layers, one direction of dependency:

```
routes/  ──→  repositories/  ──→  MongoDB
   │
   └──→ templates/          (rendering only, no queries)
```

- **Routes** parse query parameters, call one or two repositories, and hand a
  plain dict to a template. No aggregation logic.
- **Repositories** own every query. Nothing else imports `pymongo`.
- **Templates** render. **No template performs a query** — a Jinja template
  that can reach the database is how a page silently becomes twenty round trips.

### `app/main.py`

```python
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import formatting
from app.auth import basic_auth
from app.routes import (audit, executions, health, history,
                        holdings, rebalances, recommendations, summary)
from app.settings import get_settings

def create_app() -> FastAPI:
    settings = get_settings()          # raises on missing config, at import
    app = FastAPI(
        title="algotrade",
        docs_url=None, redoc_url=None, openapi_url=None,   # no public API surface
        dependencies=[Depends(basic_auth)],                # auth on every route
    )
    app.mount("/static", StaticFiles(directory="static"), name="static")

    templates = Jinja2Templates(directory="app/templates")
    templates.env.filters.update({
        "rupees": formatting.rupees,
        "pct": formatting.pct,
        "signed_pct": formatting.signed_pct,
        "day": formatting.day,
        "ago": formatting.ago,
    })
    app.state.templates = templates

    for module in (summary, holdings, recommendations, executions,
                   history, rebalances, health, audit):
        app.include_router(module.router)

    @app.exception_handler(500)
    async def on_error(request: Request, exc: Exception):
        # Never leak a stack trace: it can contain the connection string.
        return templates.TemplateResponse(
            "error.html", {"request": request, "detail": "Something failed."},
            status_code=500)

    return app
```

### `app/settings.py`

```python
from dataclasses import dataclass
from functools import lru_cache
import os

class ConfigError(RuntimeError): ...

@dataclass(frozen=True, slots=True)
class Settings:
    mongodb_uri: str
    mongodb_database: str
    portfolio_id: str
    auth_user: str
    auth_password: str
    strategy_version: str = "momentum_v1"

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    def need(key: str) -> str:
        value = os.environ.get(key, "")
        if not value:
            raise ConfigError(f"{key} is not set")
        return value

    database = need("MONGODB_DATABASE")
    # A dashboard pointed at the wrong environment shows the wrong money.
    if database not in ("algotrade_paper", "algotrade_prod"):
        raise ConfigError(f"unexpected database {database!r}")

    return Settings(
        mongodb_uri=need("MONGODB_URI"),
        mongodb_database=database,
        portfolio_id=need("PORTFOLIO_ID"),
        auth_user=need("DASHBOARD_USER"),
        auth_password=need("DASHBOARD_PASSWORD"),
        strategy_version=os.environ.get("STRATEGY_VERSION", "momentum_v1"),
    )
```

`lru_cache` means one parse per container, and `get_settings()` raising at
import turns a missing variable into a deploy-time failure rather than a
mid-page 500.

---

## 3. Mongo repository layer

### The decimal problem

Every money field is `Decimal128`. Handed to Jinja it renders as
`Decimal128("284310.22")`, and converted to `float` it loses paise. Decode once,
at the boundary — the same rule the trading system follows.

### `app/repositories/base.py`

```python
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Iterable
from bson.decimal128 import Decimal128

def dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal128):
        return value.to_decimal()
    if isinstance(value, float):
        # A float in a money field means something bypassed the trading
        # system's codec. Surface it rather than displaying it as fact.
        raise TypeError(f"money field holds a float: {value!r}")
    return Decimal(str(value))

def day(value: Any) -> date | None:
    if value is None:
        return None
    return value if isinstance(value, date) else date.fromisoformat(str(value))

def instant(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value

def decode(doc: dict, *, money: Iterable[str] = (), dates: Iterable[str] = (),
           instants: Iterable[str] = ()) -> dict:
    out = dict(doc)
    for f in money:
        if f in out: out[f] = dec(out[f])
    for f in dates:
        if f in out: out[f] = day(out[f])
    for f in instants:
        if f in out: out[f] = instant(out[f])
    return out


@dataclass(frozen=True, slots=True)
class Page:
    items: list
    total: int | None      # None when counting is too expensive
    limit: int
    offset: int

    @property
    def has_next(self) -> bool:
        return (len(self.items) == self.limit
                and (self.total is None or self.offset + self.limit < self.total))

    @property
    def has_prev(self) -> bool:
        return self.offset > 0
```

### `app/repositories/snapshots.py`

```python
MONEY = ("nav", "cash", "invested_value", "market_value", "invested_capital",
         "realised_pnl", "unrealised_pnl", "total_pnl", "total_contributed")
# units and unit_nav are NOT money -- 3 and 4 decimals respectively. Rounding
# them to paise is a bug the trading system already had once.
PRECISE = ("units", "unit_nav", "peak_unit_nav")

class SnapshotRepository:
    def __init__(self, db): self._c = db["portfolio_snapshots"]

    def latest(self, portfolio_id: str) -> dict | None:
        doc = self._c.find_one({"portfolio_id": portfolio_id},
                               sort=[("date", -1)])
        return self._decode(doc) if doc else None

    def series(self, portfolio_id: str, *, since: date | None = None) -> list[dict]:
        """NAV history WITHOUT the positions array.

        Positions are ~90% of a snapshot's bytes and are never needed for a
        chart; projecting them away turns a 300 KB response into 20 KB, which
        matters on a serverless function with a 10 s budget.
        """
        query = {"portfolio_id": portfolio_id}
        if since:
            query["date"] = {"$gte": since.isoformat()}
        cursor = self._c.find(
            query,
            {"date": 1, "nav": 1, "unit_nav": 1, "drawdown": 1,
             "day_return": 1, "benchmark_close": 1},
        ).sort("date", 1)
        return [self._decode(d) for d in cursor]

    def page(self, portfolio_id: str, *, limit: int, offset: int) -> Page:
        query = {"portfolio_id": portfolio_id}
        total = self._c.count_documents(query)
        cursor = (self._c.find(query, {"positions": 0})
                  .sort("date", -1).skip(offset).limit(limit))
        return Page([self._decode(d) for d in cursor], total, limit, offset)

    def _decode(self, doc: dict) -> dict:
        out = decode(doc, money=MONEY, dates=("date", "price_date"))
        for f in PRECISE:
            if f in out: out[f] = dec(out[f])
        out["positions"] = [
            decode(p, money=("close", "value", "unrealised_pnl"))
            for p in doc.get("positions", [])
        ]
        return out
```

Both queries ride the existing `{portfolio_id: 1, date: -1}` index — no new
indexes are needed for the dashboard, which is worth verifying with `.explain()`
once rather than assuming.

### `app/repositories/audit.py` — the one that needs cursor pagination

```python
class AuditRepository:
    def __init__(self, db): self._c = db["audit_logs"]

    def page(self, *, portfolio_id=None, action=None, entity_id=None,
             before: datetime | None = None, limit: int = 50) -> tuple[list, datetime | None]:
        """Keyset pagination on `ts`.

        `skip` degrades linearly and audit_logs is the one collection that
        grows without bound (~15k rows/year). Paging by "older than the last
        row I showed" stays constant-time at any depth, and cannot skip or
        duplicate a row when new entries arrive mid-browse.
        """
        query: dict = {}
        if portfolio_id: query["portfolio_id"] = portfolio_id
        if entity_id:    query["entity_id"] = entity_id
        if action:       query["action"] = {"$regex": f"^{re.escape(action)}"}
        if before:       query["ts"] = {"$lt": before}

        rows = [decode(d, instants=("ts",))
                for d in self._c.find(query).sort("ts", -1).limit(limit + 1)]
        next_cursor = rows[limit]["ts"] if len(rows) > limit else None
        return rows[:limit], next_cursor
```

---

## 4. Pages

`P` = `portfolio_id` from settings. Every page inherits `base.html`.

### 4.1 Portfolio Summary — `/`

| | |
|---|---|
| **Collections** | `portfolio_snapshots`, `portfolios`, `recommendations`, `job_runs` |
| **Queries** | `latest()`, `series(since=-1y)`, `portfolios.find_one`, `recommendations.outstanding`, `job_runs.last_success` |
| **Pagination** | none |

**Metrics:** NAV · cash · positions · day P&L (₹ and %) · total return ·
drawdown · unit NAV · realised + unrealised P&L.

**Charts:** NAV vs benchmark (inline SVG line, rebased to 100) · drawdown
(filled area below zero, same x-axis).

**Show drawdown at the same visual weight as return.** §11p priced quitting
during a drawdown at 27–40% of terminal wealth; a dashboard that renders gains
large and losses small trains exactly the reflex the Investment Policy exists
to prevent.

**Banner:** if the latest snapshot's `price_date` ≠ `date`, say
*"prices carried forward from {price_date}"*. Silent carry-forward on a weekend
looks like a flat market.

### 4.2 Holdings — `/holdings`

| | |
|---|---|
| **Collections** | `holdings`, `portfolio_snapshots` (latest, for closes) |
| **Queries** | `holdings.find({portfolio_id})`, joined in Python to the latest snapshot's positions |
| **Pagination** | none — 30 rows |

**Table:** symbol · qty · avg cost · close · value · weight · unrealised P&L
(₹, %) · days held · **tax status**.

Tax status is `short_term` until a lot is held **more than** 365 days. Compute
it from the oldest lot, because FIFO sells that one first. Expandable row shows
the individual lots.

**Weight bars** coloured against the 1/30 = 3.33% target: a position at 2×
target is usually an unexecuted sell, which is the thing worth spotting.

### 4.3 Recommendations — `/recommendations`, `/recommendations/{set_id}`

| | |
|---|---|
| **Collections** | `recommendations`, `executions` (detail view) |
| **Queries** | list: `find({portfolio_id}).sort([("period",-1),("revision",-1)])`; detail: `find_one({_id})` + `executions.find({set_id})` |
| **Pagination** | offset, 24/page (~2 years) |

**List:** period · revision · status · trades · turnover · executed/total ·
realised slippage · expiry.

**Detail:** sells and buys in **separate blocks, sells first** — T+1
settlement funds the buys, and a list that mixes them is not executable
top-to-bottom. Per item: reason (`rank 14 <= entry 30`), reference vs executed
price, slippage bps, skip reason. Superseded revisions render struck through
with a link to the successor.

### 4.4 Executions — `/executions`

| | |
|---|---|
| **Collections** | `executions` |
| **Queries** | `find({portfolio_id, executed_on: {$gte,$lte}}).sort("executed_on",-1)` |
| **Pagination** | offset, 50/page |

**Filters:** date range · symbol · side. All ride
`{portfolio_id: 1, executed_on: -1}` or `symbol_history`.

**Table:** date · symbol · side · qty · price · charges · slippage bps · order id.

**Header metric — realised slippage, value-weighted.** This answers §11o's open
evidence item (a): *are real fills inside the modelled 30 bps round trip?*
Weight by value; an unweighted mean lets a ₹500 trade offset a ₹50,000 one.

**Financial-year selector**, defaulting to the current Indian FY (Apr–Mar), with
a CSV export. This is the view you will actually use at tax time.

### 4.5 Portfolio History — `/history`

| | |
|---|---|
| **Collections** | `portfolio_snapshots`, `cash_transactions` |
| **Queries** | `series()` projected to `nav, unit_nav, drawdown`; `cash.contributions()` |
| **Pagination** | offset, 60/page on the table |

**Charts:** unit NAV (not raw NAV — a contribution must not look like a gain) ·
drawdown · monthly return bars.

**Metrics:** TWR · XIRR · max drawdown · current drawdown · volatility ·
Sharpe · Martin · win rate.

**Report TWR and XIRR side by side.** They diverge materially for a monthly
contributor, and §11n's decision turned on that difference.

**Withhold annualised figures below 60 sessions.** A −0.67% move over three
days annualises to −70% CAGR — arithmetically correct, meaningless, and
alarming. The trading system already suppresses this; the dashboard must not
recompute it naively.

### 4.6 Rebalance History — `/rebalances`

| | |
|---|---|
| **Collections** | `rebalance_runs`, `recommendations` |
| **Queries** | `find({portfolio_id}).sort("period",-1)`, joined to the latest revision per period |
| **Pagination** | offset, 24/page |

**Table:** period · status · trades · turnover · executed · slippage · charges.

**Detail:** entering / leaving / held, plus `input_fingerprint` —
`panel_manifest_hash`, `strategy_params_hash`, `code_commit`. Those three make
a recommendation reproducible (§9.2) and are the first thing you would want if
one ever looked wrong.

**Acceptance-rate metric:** executed ÷ (executed + skipped). §9's behavioural
audit. If declined trades would have outperformed accepted ones, discretion is
destroying alpha — and this is the only place that becomes visible.

### 4.7 System Health — `/health`

| | |
|---|---|
| **Collections** | `job_runs`, `notifications`, `portfolio_snapshots`, `signals`, `recommendations` |
| **Queries** | `job_runs.find({status:"failed"}).limit(20)`, last success per job, `notifications.find({status:"failed"})`, latest snapshot and signal dates |
| **Pagination** | none — last 20 runs |

**Score:** reuse the trading system's weights (`ops/health.py`) so the two
never disagree — reconciliation −40, stale data −25, repeated job failure −15,
notification failure −10. GREEN ≥90, YELLOW 60–89, RED <60.

**Every deduction states its reason.** A score with no explanation is a number
you learn to ignore.

**Include `notifications`** — the collection missing from the original list. A
failed Telegram send is invisible by definition: you cannot be told that you
cannot be told. This page is the only place it surfaces.

**Cannot show:** whether the *dashboard itself* is up. That is what
healthchecks.io is for, and the page should say so rather than imply green
means everything is fine.

### 4.8 Audit Trail — `/audit`

| | |
|---|---|
| **Collections** | `audit_logs` |
| **Queries** | keyset on `ts`, filtered by portfolio / action prefix / entity |
| **Pagination** | **cursor**, 50/page — see §3 |

**Filters:** action prefix (`recommendation.`, `execution.`, `holdings.`) ·
entity id · severity · date range.

**Row:** timestamp · actor · action · entity · before → after diff · commit.

Filtering by `entity_id` gives the full life of one recommendation set —
generated → notified → each fill → completed. That is the §9.3 trace, and it is
the reason this page is worth building rather than reading Compass.

---

## 5. Navigation

Eight items is too many for a phone. Group into four, with sub-pages:

```
Portfolio  →  Summary · Holdings · History
Activity   →  Recommendations · Executions · Rebalances
System     →  Health · Audit
```

Bottom tab bar on mobile (thumb-reachable), horizontal bar on desktop. Pure
CSS via a media query — no JS, no framework.

```html
<!-- _nav.html -->
<nav class="nav">
  <a href="/" class="{{ 'active' if active == 'summary' }}">Portfolio</a>
  <a href="/recommendations" class="{{ 'active' if active in ('recs','exec','reb') }}">Activity</a>
  <a href="/health" class="{{ 'active' if active in ('health','audit') }}">System</a>
</nav>
```

**Health badge in the header on every page.** A red dot you see incidentally
beats a page you have to remember to visit.

---

## 6. Templates

```
base.html
├── {% block head %}      per-page <title>
├── header                portfolio label · as-of date · health dot
├── _nav.html
├── {% block content %}
└── footer                data as of · "read-only" · git sha
```

Partials worth having, and no more:

| Partial | Why |
|---|---|
| `_pagination.html` | Used by four pages; identical markup |
| `_money.html` | Negative amounts red, positive green, `-` for null |
| `_status_pill.html` | Consistent colours for every status enum |
| `_sparkline.html` | Wraps `charts.sparkline()` output |

Filters do the formatting, so templates carry no logic:

```python
def rupees(value, *, compact=False):
    """Indian grouping: 2,84,310 -- not 284,310."""
    if value is None: return "—"
    if compact and abs(value) >= 100_000:
        return f"₹{value / 100_000:.2f}L"
    ...
```

`₹2.84L` on mobile, full precision on desktop.

---

## 7. Vercel configuration

### `vercel.json`

```json
{
  "$schema": "https://openapi.vercel.sh/vercel.json",
  "functions": {
    "api/index.py": {
      "runtime": "python3.12",
      "memory": 512,
      "maxDuration": 10
    }
  },
  "rewrites": [
    { "source": "/static/(.*)", "destination": "/static/$1" },
    { "source": "/(.*)",        "destination": "/api/index" }
  ],
  "headers": [
    {
      "source": "/(.*)",
      "headers": [
        { "key": "X-Frame-Options",        "value": "DENY" },
        { "key": "X-Content-Type-Options", "value": "nosniff" },
        { "key": "Referrer-Policy",        "value": "no-referrer" },
        { "key": "X-Robots-Tag",           "value": "noindex, nofollow" },
        { "key": "Content-Security-Policy",
          "value": "default-src 'self'; style-src 'self'; img-src 'self' data:; script-src 'none'" }
      ]
    }
  ]
}
```

`script-src 'none'` is enforceable **because** §0.4 chose server-rendered SVG.
That is the security dividend of not using a chart library.

### `api/index.py`

```python
from app.main import create_app
app = create_app()          # Vercel's Python runtime serves an ASGI `app`
```

### `requirements.txt`

```
fastapi==0.115.6
jinja2==3.1.5
pymongo==4.17.0
python-multipart==0.0.20
```

No `uvicorn` — Vercel provides the server. Pin exactly; a surprise minor bump
on a Sunday deploy is not worth the currency.

---

## 8. Security

| Concern | Control |
|---|---|
| Public URL | HTTP Basic Auth on every route, `compare_digest`, ~32-char password |
| Write access | **Separate Atlas user with `read` only** — enforcement, not intent |
| Credential leak via traceback | 500 handler renders a generic page; `docs_url=None` |
| Wrong environment | `settings` refuses any database but `algotrade_paper`/`algotrade_prod` |
| Search indexing | `X-Robots-Tag: noindex` + auth |
| XSS | Jinja autoescaping on; **no `\|safe` on any database value** |
| Clickjacking | `X-Frame-Options: DENY` |
| Supply chain | Four pinned dependencies, no JS packages at all |

```python
# app/auth.py
import secrets
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

security = HTTPBasic()

def basic_auth(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    s = get_settings()
    # compare_digest on BOTH, and always both, so the comparison is
    # constant-time regardless of which field is wrong.
    ok_user = secrets.compare_digest(credentials.username, s.auth_user)
    ok_pass = secrets.compare_digest(credentials.password, s.auth_password)
    if not (ok_user and ok_pass):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Unauthorized",
            headers={"WWW-Authenticate": "Basic"})
    return credentials.username
```

> **Atlas network access.** Vercel function IPs are not stable, so `0.0.0.0/0`
> is already required — the same posture the trading system accepted. The
> read-only user is what limits the blast radius, and it is why §0.2 is not
> optional.

---

## 9. Environment variables

Vercel → Settings → Environment Variables. Set for **Production** and
**Preview** separately; a preview deploy pointed at prod data is a leak.

| Variable | Example | Notes |
|---|---|---|
| `MONGODB_URI` | `mongodb+srv://algotrade_dashboard:…@cluster0…` | **The read-only user**, not the trading one |
| `MONGODB_DATABASE` | `algotrade_paper` | Validated against an allow-list |
| `PORTFOLIO_ID` | `paper_momentum_v1_001` | |
| `DASHBOARD_USER` | `akash` | |
| `DASHBOARD_PASSWORD` | 32 random chars | `openssl rand -base64 24` |
| `STRATEGY_VERSION` | `momentum_v1` | Optional |

`.env.example` is committed; `.env` is not.

---

## 10. Testing

```python
# tests/conftest.py -- routes are tested against fakes, with no database
@pytest.fixture
def client(fake_repos):
    app = create_app()
    app.dependency_overrides[get_db] = lambda: fake_repos
    app.dependency_overrides[basic_auth] = lambda: "test"
    return TestClient(app)
```

Worth testing, in priority order:

1. **Every page renders** from a fake with realistic data — catches template
   typos, which are otherwise found in production
2. **Every page renders from an EMPTY database** — a brand-new portfolio has no
   snapshots, and `latest()` returning `None` must not 500
3. **Decimal formatting** — `Decimal128` never reaches a template
4. **Auth** — 401 without credentials, on every route
5. **Pagination boundaries** — first page, last page, empty page
6. **A float in a money field raises** rather than rendering

Point 2 is the one people skip and then hit on day one.

---

## 11. Deployment

```bash
# 1. Atlas: create the read-only user (§0.2)

# 2. repository
gh repo create akashdnb/algotrade-dashboard --private --clone
cd algotrade-dashboard
# ... build ...

# 3. local
cp .env.example .env && vim .env
uvicorn app.main:create_app --factory --reload
# http://127.0.0.1:8000

# 4. deploy
npm i -g vercel
vercel login
vercel link
vercel env add MONGODB_URI production        # repeat for each variable
vercel --prod

# 5. verify
curl -I https://<app>.vercel.app                       # expect 401
curl -u user:pass https://<app>.vercel.app | head -40  # expect HTML
```

**Verify the read-only user is genuinely read-only** before trusting it:

```python
db["portfolios"].insert_one({"_id": "__probe__"})   # must raise OperationFailure
```

If that insert succeeds, the role is wrong. (The trading system learned this
the hard way in the other direction: a stray one-document probe created an
unvalidated collection that `collMod` could not repair.)

---

## 12. Build order

Each step ends with something deployable.

| # | Deliverable | Done when |
|---|---|---|
| 1 | Skeleton: settings, db, auth, base template, `/health` | 401 without credentials; page renders on Vercel |
| 2 | Summary + snapshot repository + SVG charts | NAV and drawdown render on a phone |
| 3 | Holdings | 30 rows with weights and tax status |
| 4 | Recommendations list + detail | Sells-then-buys, slippage visible |
| 5 | Executions + FY filter + CSV | Tax export works |
| 6 | History + performance metrics | TWR and XIRR side by side |
| 7 | Rebalances + acceptance rate | Cost of discretion visible |
| 8 | Audit with keyset pagination | One set's full trace is filterable |

Steps 1–3 are the useful dashboard. 4–8 are depth. **If you stop after 3 you
still have something worth opening**, which is the right shape for a project
whose real job is to make you look at a drawdown calmly rather than to be
comprehensive.

---

## 13. Deliberately excluded

| Not building | Why |
|---|---|
| Writes of any kind | Execution recording goes through `record-execution.yml`, which has auth, an audit trail, and reconciliation. A web form would need all three rebuilt, worse. |
| React / SPA | One user, eight read-only pages, daily-changing data. A build step and a client bundle buy nothing. |
| A REST API | The dashboard is the consumer. `openapi_url=None`. |
| Real-time updates | The data changes once a day. WebSockets for a daily feed is theatre. |
| Multi-portfolio switching | Add it when `shadow_momentum_v1_001` exists and there is a second thing to look at. |
| Caching | Premature. Measure page latency first; if it is a problem, §0.1's static-JSON fallback beats a cache layer. |
```
