# Dashboard

Web view over the trading system's Atlas database. FastAPI + Jinja2,
server-rendered, no JavaScript.

> **Live since 2026-09-01** at <https://algotrade-six-chi.vercel.app>, running
> **read-only** — `DASHBOARD_ALLOW_WRITES` is unset, so the execution-recording
> routes exist but refuse. See
> [`docs/deployment/dashboard.md`](../docs/deployment/dashboard.md).

It lives **inside this repository on purpose.** It will eventually record
executions, and doing that safely means running the trading system's own
`ExecutionService`, `HoldingsService` and `ReconciliationService` — a second
implementation of the FIFO rebuild and the cash ledger would drift, and the
drift is silent until the book is already wrong.

## Run it locally

```bash
cp dashboard/.env.example dashboard/.env     # then fill it in
./dashboard/dev.sh                           # works from any directory
```

Or by hand, **from the repository root**:

```bash
.venv/bin/python -m uvicorn api.index:app --reload \
  --reload-dir dashboard --reload-dir algotrade --reload-dir api --port 8000
```

Two things that command gets right. The entrypoint is `api/index.py` at the
**repository root**, not inside `dashboard/` — that is Vercel's zero-config
location, and it needs the root on `sys.path` to import `algotrade`. And
`--reload-dir` is scoped: without it uvicorn watches the whole repo and polls
the 1.9 GB of market data in `data/` on every cycle.

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
| `/` | NAV, day P&L, drawdown against the expected worst case, and NAV vs NIFTYBEES |
| `/holdings` | Positions with cost basis, weight bars, tax status and lot breakdown |
| `/recommendations` | Rebalance sets; the review-and-submit form when writes are on |
| `/executions` | Fills, filtered by financial year, with a CSV export for tax |
| `/history` | Unit NAV, drawdown, monthly returns, TWR beside XIRR |
| `/rebalances` | Period history, membership diff, input fingerprint, acceptance rate |
| `/audit` | Every state change, cursor-paginated, filterable to one entity's whole life |
| `/health` | Data freshness and recent job runs |
| `/healthz` | Liveness. Unauthenticated, touches no database, reads no settings. |

Several of these deliberately withhold rather than mislead: annualised figures
below 60 sessions, XIRR below 90 elapsed days, and the acceptance rate until
something has actually been declined. A number computed from too little data
is not a measurement, and on this dashboard an alarming one is the specific
failure the Investment Policy exists to prevent.

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
