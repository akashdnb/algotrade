# Dashboard

Read-only web view over the trading system's Atlas database. FastAPI + Jinja2,
server-rendered, no JavaScript.

It lives **inside this repository on purpose.** It will eventually record
executions, and doing that safely means running the trading system's own
`ExecutionService`, `HoldingsService` and `ReconciliationService` — a second
implementation of the FIFO rebuild and the cash ledger would drift, and the
drift is silent until the book is already wrong.

## Run it locally

```bash
cp dashboard/.env.example dashboard/.env     # then fill it in
.venv/bin/python -m uvicorn api.index:app --reload --port 8000
```

Run it from the **repository root**, not from `dashboard/` — the entrypoint
lives at `api/index.py` because that is Vercel's zero-config location, and it
needs the repo root on `sys.path` to import the `algotrade` package.

Then open <http://127.0.0.1:8000> and log in with `DASHBOARD_USER` /
`DASHBOARD_PASSWORD`.

`dashboard/.env` is gitignored. The process environment always wins over it, so
a stale local file can never override a deployed configuration.

## Deploying

See [`docs/deployment/dashboard.md`](../docs/deployment/dashboard.md). Deploy
read-only first: leave `DASHBOARD_ALLOW_WRITES` unset until you have used it.

## Tests

```bash
cd dashboard && ../.venv/bin/python -m pytest tests -q
```

No database required — `tests/conftest.py` provides fakes. A suite that needs
a live Atlas connection is a suite that stops being run.

## Pages

| Path | Shows |
|---|---|
| `/` | NAV, cash, day P&L, drawdown, and the Investment Policy's expected pain |
| `/holdings` | 30 positions with cost basis, weight bars, and tax status |
| `/health` | Data freshness and recent job runs |
| `/healthz` | Liveness. Touches no database, deliberately. |

## Configuration

| Variable | Notes |
|---|---|
| `MONGODB_URI` | Should be a **separate Atlas user** from the trading system's |
| `MONGODB_DATABASE` | `algotrade_paper` or `algotrade_prod`; `algotrade_dev` is refused |
| `PORTFOLIO_ID` | e.g. `paper_momentum_v1_001` |
| `DASHBOARD_USER` / `DASHBOARD_PASSWORD` | Basic auth. Minimum 12 characters, enforced at startup |
| `DASHBOARD_ALLOW_WRITES` | Off unless set. Gates the execution-recording routes |

## Notes

- **Charts are server-rendered SVG**, which is why the CSP can be
  `script-src 'none'`. The data changes once a day; a charting library would
  buy nothing and add a CDN dependency that can break on its own.
- **Money is never a float.** `Decimal128` is decoded once at the repository
  boundary, and a float in a money field raises rather than rendering.
- **Drawdown is shown at the same visual weight as return.** §11p priced
  abandoning the strategy during a drawdown at 27–40% of terminal wealth; a
  page that draws gains large and losses small trains that reflex.
- **No NIFTY comparison yet** — `benchmark_close` is never populated. Tracked
  as next step 6 in `PROJECT.md`.
