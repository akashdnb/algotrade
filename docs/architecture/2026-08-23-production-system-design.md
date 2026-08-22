# Production System Architecture — Momentum Sleeve

**Date:** 2026-08-23
**Status:** Design
**Scope:** The system manages the **momentum sleeve only**. NIFTYBEES (the other 50% of the
account) is held manually and is never tracked, traded, valued, or rebalanced here.
**Strategy:** Frozen. `momentum_v1` = 12-1 momentum (lookback 252, skip 21), liquid-300
universe, rank buffer entry 30 / exit 60, monthly cadence. Not revisited in this document.

---

## 0. Three decisions that shape everything else

Before the section-by-section design, three constraints that a generic "MongoDB + GitHub
Actions" answer would get wrong.

### 0.1 GitHub Actions cron is not a scheduler you can trust

Three documented behaviours matter over a multi-year horizon:

| Behaviour | Consequence |
|---|---|
| Scheduled workflows are **disabled after 60 days of repository inactivity** | The system silently stops. No alert, because nothing runs to alert. |
| `schedule` triggers are **best-effort** — routinely delayed 5–30 min, occasionally dropped entirely under platform load | A job that assumes "the cron fired, therefore today is unprocessed" corrupts state or skips a day. |
| Pushes made with the default `GITHUB_TOKEN` **do not trigger** further workflow runs | Workflow chaining via commit does not work. |

**Therefore: every job is reconciliation-driven, not trigger-driven.** A job never asks
"did my cron fire?" It asks **"what business dates are missing, and what is the desired
state?"** — then closes the gap, however many days wide it is. A missed Tuesday is repaired
by Wednesday's run with no human involvement. The cron is a *hint to wake up*, not the unit
of work.

This single principle is what makes "runs for years with minimal maintenance" achievable,
and it drives the idempotency keys throughout §2.

### 0.2 The 1.8 GB data problem

Current on-disk state: 720 MB of raw bhavcopy across 3,996 session files, 194 MB of curated
Parquet, 34 MB of index data. Committing this to git is not viable — the curated panel's
year partitions are *rewritten* on every build, so committing them would add ~21 MB of git
objects per day (≈5 GB/year of history for 194 MB of live data).

**Three tiers, each stored where its mutation pattern belongs:**

| Tier | Content | Size | Storage | Rationale |
|---|---|---|---|---|
| **1. Frozen archive** | Historical bhavcopy through deployment date | 720 MB | **GitHub Release asset** (`.tar.zst`), private to the repo | Immutable → perfect cache key, downloaded once, never re-uploaded |
| **2. Forward sessions** | One CSV per trading day, from deployment onward | ~180 KB/day, ~45 MB/year | **Committed to git**, `data/sessions/YYYY/` | Append-only (git-friendly), immutable once written, *is* the audit trail — and the daily commit keeps the repo active, defeating the 60-day cron disable |
| **3. Derived panel** | Curated + adjusted Parquet | 194 MB | **`actions/cache`**, never committed | Reproducible from tiers 1+2 in ~21 s. Already the project's stated principle: the adjusted panel is *"a cache, never a source of truth"* (`io/store.py`) |

MongoDB holds **zero** market data. Not one OHLCV row. It holds application state only,
per your constraint — and this also keeps Atlas M0's 512 MB free tier viable indefinitely
(§12).

> **Licensing caveat.** NSE bhavcopy redistribution terms are not clearly permissive. Keep
> the repo **private** and the Release asset with it (private-repo release assets inherit
> repo visibility). This is a legal posture, not a technical one — repo privacy is not a
> redistribution licence, so do not make this repo public later without checking.

### 0.3 GitHub Actions cannot host your REST API

Actions is a batch runner. It cannot serve HTTP. Since you want a React dashboard, one of
these must be true — and this is the only component of your stack that needs a decision
outside what you listed:

| Option | Cost | Maintenance | Verdict |
|---|---|---|---|
| **Static JSON + GitHub Pages** — the daily job writes `api/*.json`, React fetches it | ₹0 | Near-zero | **Recommended for reads.** The dashboard is ~95% read-only. Requires GitHub Pro for private Pages (~$4/mo), else the data is public. |
| **FastAPI on Vercel/Render free tier** | ₹0 | Cold starts, free tiers change | Needed only if you want live writes from the browser |
| **Atlas Data API** | — | — | **Do not use.** Deprecated by MongoDB (Sept 2025). |

**Recommendation:** design the REST contract now (§6), implement it as **static JSON
snapshots for reads** and **`workflow_dispatch` for writes** (marking a recommendation
executed is a handful of events per month, not an interactive workload). Add a real API
server only if the dashboard ever needs sub-daily interactivity. This keeps the running
system at exactly two moving parts — Actions and Atlas — which is what "runs for years"
requires.

---

## 1. Repository Structure

```
algotrade/
├── algotrade/                       # library: pure logic, no I/O at module scope
│   ├── core/                        # EXISTING — frozen strategy logic
│   │   ├── universe.py              #   liquid-300 selection (§10.4)
│   │   ├── features.py              #   momentum, realised_vol, ...
│   │   ├── strategies.py            #   cross-sectional construction
│   │   ├── buffering.py             #   rank buffer entry/exit
│   │   ├── costs.py                 #   Zerodha rate card, slippage
│   │   ├── adjust.py / actions.py   #   corporate actions
│   │   └── integrity.py             #   chain-break / missing-session detection
│   ├── backtest/                    # EXISTING — engine, metrics, drawdown
│   ├── io/                          # EXISTING — bhavcopy, nse, archive, store, indices
│   │
│   ├── signal/                      # NEW — the deployed signal (thin: composes core/)
│   │   └── momentum.py              #   deployed_weights(panel) -> (date, symbol, weight)
│   │
│   ├── domain/                      # NEW — pure domain model, zero MongoDB imports
│   │   ├── ids.py                   #   portfolio_id parsing/validation (§3)
│   │   ├── models.py                #   dataclasses: Portfolio, Holding, Lot,
│   │   │                            #     RecommendationSet, Item, Snapshot
│   │   ├── construction.py          #   target weights -> integer share quantities
│   │   ├── reconcile.py             #   current holdings vs target -> buy/sell deltas
│   │   ├── valuation.py             #   NAV, unit accounting, drawdown, XIRR
│   │   └── tax.py                   #   FIFO lot matching, STCG/LTCG classification
│   │
│   ├── services/                    # NEW — orchestration; depends on domain + repos
│   │   ├── ingestion.py             #   §5.1
│   │   ├── validation.py            #   §5.2
│   │   ├── ranking.py               #   §5.3
│   │   ├── portfolio.py             #   §5.4
│   │   ├── recommendation.py        #   §5.5
│   │   ├── notification.py          #   §5.6
│   │   └── tracking.py              #   §5.7
│   │
│   ├── repositories/                # NEW — the ONLY place pymongo is imported
│   │   ├── client.py                #   connection, retry policy, transactions
│   │   ├── portfolios.py            #   one module per collection
│   │   ├── holdings.py
│   │   ├── recommendations.py
│   │   ├── snapshots.py
│   │   ├── rebalance_runs.py
│   │   ├── audit.py
│   │   ├── jobs.py
│   │   └── notifications.py
│   │
│   └── platform/                    # NEW — cross-cutting infrastructure
│       ├── config.py                #   env -> typed Settings (§1.3)
│       ├── logging.py               #   structured JSON logs
│       ├── clock.py                 #   injectable now()/today() — never call date.today()
│       │                            #     directly outside this module (testability)
│       ├── telegram.py              #   Bot API client
│       └── idempotency.py           #   job_run guard / advisory lock (§0.1)
│
├── jobs/                            # NEW — thin CLI entrypoints, one per workflow step
│   ├── ingest_session.py            #   --date YYYY-MM-DD
│   ├── value_portfolios.py
│   ├── run_rebalance.py             #   --portfolio-id --period
│   ├── mark_executed.py             #   --set-id --fills-json
│   ├── publish_api.py               #   writes static JSON for the dashboard
│   ├── watchdog.py
│   └── recover.py                   #   --from --to  (gap backfill)
│
├── scripts/                         # EXISTING — research/diagnostics; NOT run in prod
├── tests/
│   ├── unit/                        #   domain + services with fakes
│   ├── integration/                 #   real MongoDB via mongodb-memory-server / testcontainers
│   └── fixtures/
│
├── config/
│   ├── costs.yaml                   # EXISTING — Zerodha rate card
│   ├── strategy/momentum_v1.yaml    # FROZEN params, hashed into every run (§9)
│   ├── settings.base.yaml
│   ├── settings.paper.yaml
│   └── settings.live.yaml
│
├── data/
│   ├── archive/                     # gitignored — tier 1, from Release asset
│   ├── sessions/YYYY/               # COMMITTED — tier 2, append-only
│   ├── curated/                     # gitignored — tier 3, cached
│   └── indices/                     # gitignored — tier 1
│
├── api/                             # generated static JSON (published to Pages)
├── dashboard/                       # React app (later)
├── docs/
└── .github/workflows/               # §4
```

### 1.1 Module boundaries — the dependency rule

```
jobs/ ──→ services/ ──→ domain/          (pure: no I/O, no pymongo, no network)
             │
             ├──→ repositories/ ──→ MongoDB
             ├──→ io/, signal/  ──→ files
             └──→ platform/     ──→ Telegram, config, clock
```

Enforced, not merely intended:

- **`domain/` imports nothing from `repositories/`, `services/`, `io/`, or `platform/`.**
  It is dataclasses and functions. This makes every rule about money, quantities, tax lots,
  and reconciliation testable with no database at all.
- **`pymongo` appears only under `repositories/`.** Add a CI check: `grep -rl pymongo
  algotrade/ | grep -v repositories/` must be empty. Without this, Mongo queries leak into
  business logic within a year and the domain becomes untestable.
- **`services/` may not import each other.** If two services need shared logic, it belongs
  in `domain/`. This prevents the service layer collapsing into a mutually-recursive ball.
- **`scripts/` is research and is never imported by `jobs/` or `services/`.** The current
  repo has research scripts importing each other (`importlib.import_module("scripts.run_hypothesis")`);
  production must not inherit that coupling.

### 1.2 Environment separation

Three environments, distinguished **by data, not by code path**:

| Env | Mongo database | Portfolios | Telegram chat | Secrets source |
|---|---|---|---|---|
| `local` | `algotrade_local` (Docker or Atlas M0) | `paper_momentum_v1_dev` | your test chat | `.env.local` (gitignored) |
| `paper` | `algotrade_paper` | `paper_momentum_v1_001`, `shadow_momentum_v1_001` | paper chat | GitHub Environment `paper` |
| `live` | `algotrade_live` | `live_momentum_v1_001`, `shadow_momentum_v1_001` | live chat | GitHub Environment `live` |

**Separate Atlas *databases*, same cluster** (M0 allows multiple DBs). Not separate
collections in one DB with an `env` field — a missing filter then silently mixes paper and
live money, and that is exactly the class of mistake that must be structurally impossible.

`live` uses a **GitHub Environment with a required reviewer**, so any workflow touching live
state pauses for your approval. Paper needs no approval.

### 1.3 Configuration layout

Layered, most-specific wins: `settings.base.yaml` → `settings.<env>.yaml` → environment
variables (`ALGOTRADE_*`) → CLI flags. Loaded once into a frozen typed `Settings` object at
process start; nothing reads `os.environ` afterwards.

**Strategy parameters are not settings.** `config/strategy/momentum_v1.yaml` is content-
addressed: its SHA-256 is stamped into every `rebalance_run`, and a portfolio records the
hash it was created with. If the file changes, the job **refuses to run** and alerts (§9.2).
A frozen strategy that can be edited without anyone noticing is not frozen — and §11q found
exactly this class of silent drift twice.

```yaml
# config/strategy/momentum_v1.yaml  — FROZEN. Changing this forks a new portfolio.
version: momentum_v1
feature: momentum
params: { lookback: 252, skip: 21 }
universe: { size: 300, min_turnover: 50000000, min_coverage: 0.80, lookback: 20 }
book: { entry: 30, exit: 60, cadence: monthly, direction: high, weighting: equal }
execution: { fill: next_open, min_trade_value: 500 }
```

---

## 2. MongoDB Design

Conventions used throughout:

- `_id` is a **deterministic natural key** wherever one exists. This is the primary
  idempotency mechanism: a retried job re-inserting the same document fails on duplicate
  `_id` rather than creating a second one (§0.1, §11.5).
- All timestamps are UTC `Date`. All business dates are `"YYYY-MM-DD"` **strings** — an IST
  trading date is a calendar fact, not an instant, and storing it as a `Date` invites a
  timezone shift to move it a day.
- All money is stored as **`Decimal128`**, never float. Float rupees accumulate error across
  years of compounding, and this system's entire purpose is measuring small differences.
- Every mutating write emits an `audit_logs` entry in the same transaction.

### 2.1 `portfolios`

One document per portfolio. Small, permanent, read on every job.

```javascript
{
  _id: "paper_momentum_v1_001",          // == portfolio_id (§3)
  mode: "paper",                          // paper | live | shadow
  strategy: "momentum",
  strategy_version: "momentum_v1",
  strategy_params_hash: "sha256:9f2c…",   // §1.3 guard
  status: "active",                       // active | paused | closed
  created_at: ISODate("2026-09-01T00:00:00Z"),
  closed_at: null,

  currency: "INR",
  initial_capital: Decimal128("200000.00"),
  monthly_contribution: Decimal128("20000.00"),
  contribution_day: 1,                    // day of month contributions land
  cash: Decimal128("14320.55"),           // uninvested rupees
  total_contributed: Decimal128("260000.00"),

  // Unit accounting: makes contribution-adjusted performance exact.
  units: Decimal128("2412.887"),
  // Sleeve is fully invested in itself; the 50% account allocation and the
  // NIFTYBEES half are OUTSIDE this system entirely (recorded for context only).
  account_note: "50% of account. NIFTYBEES half held manually; not tracked here.",

  broker: "zerodha",
  cost_model: "config/costs.yaml@sha256:41ab…",
  execution_mode: "manual",               // manual | auto_paper
  created_by_commit: "e5df354…"
}
```

**Indexes**

| Index | Type | Purpose |
|---|---|---|
| `_id` | unique (implicit) | Natural key |
| `{ mode: 1, status: 1 }` | — | "all active paper portfolios" |

**Retention:** permanent. A closed portfolio is never deleted — its snapshots and
recommendations must remain interpretable.

---

### 2.2 `holdings`

Current position per symbol, **with FIFO tax lots**. Lots are embedded rather than a
separate collection: a 30-name book accumulates maybe 200 lots total, far below the 16 MB
document limit, and embedding makes "read the whole position atomically" trivial.

Tax lots are not optional here. §11q established that the STCG/LTCG asymmetry (20% annual
vs 12.5% deferred) is large enough to reverse conclusions — a system that cannot classify a
lot cannot report after-tax P&L or support your filing.

```javascript
{
  _id: "paper_momentum_v1_001:TATAMOTORS",     // portfolio_id:symbol
  portfolio_id: "paper_momentum_v1_001",
  symbol: "TATAMOTORS",
  isin: "INE155A01022",                        // survives renames (§10.7)

  quantity: 47,
  avg_cost: Decimal128("981.4200"),
  cost_basis: Decimal128("46126.74"),

  lots: [                                       // FIFO, oldest first
    { lot_id: "L-2026-09-02-01", acquired_on: "2026-09-02",
      quantity: 30, price: Decimal128("975.10"),
      charges: Decimal128("31.85"),
      source_set_id: "paper_momentum_v1_001:2026-09:r1" },
    { lot_id: "L-2026-11-03-01", acquired_on: "2026-11-03",
      quantity: 17, price: Decimal128("992.55"),
      charges: Decimal128("22.40"),
      source_set_id: "paper_momentum_v1_001:2026-11:r1" }
  ],

  first_acquired: "2026-09-02",
  last_changed: "2026-11-03",
  realised_pnl_ytd: Decimal128("0.00"),
  updated_at: ISODate("2026-11-03T14:35:12Z")
}
```

**Indexes**

| Index | Type | Purpose |
|---|---|---|
| `_id` | unique | `portfolio_id:symbol` — makes a duplicate position structurally impossible |
| `{ portfolio_id: 1 }` | — | Load the whole book |
| `{ portfolio_id: 1, isin: 1 }` | — | Rename-safe lookup |

**Retention:** row is **deleted** when quantity reaches 0 (history lives in
`portfolio_snapshots` and `recommendations`). Deleting keeps "the book" a clean read with no
`quantity: 0` noise to filter — and forgetting that filter is how a zero position gets
reported as held.

---

### 2.3 `recommendations`

The trade record. One document per recommendation *set*; line items embedded.

```javascript
{
  _id: "paper_momentum_v1_001:2026-09:r1",   // portfolio:period:revision
  portfolio_id: "paper_momentum_v1_001",
  rebalance_run_id: "paper_momentum_v1_001:2026-09",
  period: "2026-09",
  revision: 1,                                // r2 = regenerated (§4.3)
  supersedes: null,

  generated_at: ISODate("2026-09-30T14:35:00Z"),
  as_of_session: "2026-09-30",                // the CLOSE the signal used
  fill_convention: "next_open",               // matches the backtest engine
  expires_on: "2026-10-03",                   // stale after 3 sessions

  status: "partially_executed",
  // pending | notified | partially_executed | completed | expired | superseded

  reference_nav: Decimal128("284310.22"),
  reference_cash: Decimal128("14320.55"),
  estimated_total_charges: Decimal128("612.40"),

  items: [
    {
      seq: 1, action: "sell", symbol: "ADANIENT", isin: "INE423A01024",
      reason: "rank 71 > exit 60",
      quantity: 12, reference_price: Decimal128("2410.00"),
      estimated_value: Decimal128("28920.00"),
      estimated_charges: Decimal128("48.20"),
      status: "executed",
      executed_quantity: 12,
      executed_price: Decimal128("2402.75"),
      executed_charges: Decimal128("49.05"),
      executed_at: ISODate("2026-10-01T03:47:00Z"),
      broker_order_id: "251001000123456",
      slippage_bps: -30.1,                     // vs reference_price; signed
      skip_reason: null
    },
    {
      seq: 2, action: "buy", symbol: "PERSISTENT", isin: "INE262H01013",
      reason: "rank 14 <= entry 30",
      quantity: 5, reference_price: Decimal128("5820.00"),
      estimated_value: Decimal128("29100.00"),
      estimated_charges: Decimal128("47.60"),
      status: "skipped",
      executed_quantity: 0, executed_price: null, executed_charges: null,
      executed_at: null, broker_order_id: null, slippage_bps: null,
      skip_reason: "insufficient funds after sell settlement (T+1)"
    }
  ],

  totals: { items: 9, executed: 7, partial: 0, skipped: 2 },
  realised_slippage_bps: -18.4,                // value-weighted, executed only
  code_commit: "e5df354…",
  strategy_params_hash: "sha256:9f2c…"
}
```

**Why `skip_reason` is mandatory on a skip.** §9 requires that every decline carry a logged
reason, and that acceptance rate and the P&L of declined-vs-accepted recommendations be
tracked. The backtest assumes every signal is taken; if you decline 30% of them you are
running a different strategy than the one that was validated. This field is the raw material
for measuring that, and the API rejects a `skipped` status without it.

**Indexes**

| Index | Type | Purpose |
|---|---|---|
| `_id` | unique | `portfolio:period:revision` — a retried generation cannot duplicate |
| `{ portfolio_id: 1, period: 1, revision: -1 }` | — | Latest revision for a period |
| `{ portfolio_id: 1, status: 1 }` | — | Outstanding work |
| `{ status: 1, expires_on: 1 }` | — | Expiry sweep |
| `{ "items.symbol": 1, generated_at: -1 }` | — | Per-symbol trade history |

**Retention:** permanent. This is your tax and audit record; Indian record-keeping
expectations run to ~6–7 years and the cost of keeping it forever is negligible.

---

### 2.4 `portfolio_snapshots`

Daily valuation. The mechanism by which holdings on any past date are reconstructed (§9.1).

```javascript
{
  _id: "paper_momentum_v1_001:2026-09-30",     // portfolio:date — idempotent
  portfolio_id: "paper_momentum_v1_001",
  date: "2026-09-30",

  nav: Decimal128("284310.22"),
  cash: Decimal128("14320.55"),
  invested_value: Decimal128("269989.67"),

  units: Decimal128("2412.887"),
  unit_nav: Decimal128("117.8305"),            // NAV/unit: contribution-neutral
  total_contributed: Decimal128("260000.00"),

  positions: [
    { symbol: "TATAMOTORS", quantity: 47, close: Decimal128("1012.30"),
      value: Decimal128("47578.10"), weight: 0.1673, unrealised_pnl: Decimal128("1451.36") }
  ],

  benchmark: { symbol: "NIFTY_50", close: Decimal128("26140.55") },
  peak_unit_nav: Decimal128("121.4400"),
  drawdown: -0.0297,
  panel_manifest_hash: "sha256:7d41…",         // which data produced this
  computed_at: ISODate("2026-09-30T14:40:03Z")
}
```

**Indexes**

| Index | Type | Purpose |
|---|---|---|
| `_id` | unique | `portfolio:date` — **the daily-job idempotency key** |
| `{ portfolio_id: 1, date: -1 }` | — | Time series reads, latest-first |
| `{ date: -1 }` | — | Cross-portfolio "as of" queries |

**Retention:** daily snapshots forever. ~30 positions × ~120 B × 250 sessions ≈ **1 MB per
portfolio-year** — trivial against M0's 512 MB, and downsampling would destroy the exact
reconstruction guarantee that justifies the collection. Revisit only past ~50 MB.

---

### 2.5 `rebalance_runs`

One document per (portfolio, period). **The lock that makes monthly execution exactly-once.**

```javascript
{
  _id: "paper_momentum_v1_001:2026-09",        // portfolio:period — UNIQUE
  portfolio_id: "paper_momentum_v1_001",
  period: "2026-09",
  status: "completed",
  // pending | generating | generated | notified | executing | completed | failed
  triggered_by: "schedule",                     // schedule | manual | recovery
  attempt: 1,

  as_of_session: "2026-09-30",

  // Everything needed to reproduce this run byte-for-byte (§9.2)
  input_fingerprint: {
    panel_manifest_hash: "sha256:7d41…",
    last_session: "2026-09-30",
    strategy_params_hash: "sha256:9f2c…",
    cost_model_hash: "sha256:41ab…",
    code_commit: "e5df354…",
    universe_size: 300
  },

  target: [ { rank: 1, symbol: "PERSISTENT", weight: 0.03333,
              feature: 1.4821, adtv: Decimal128("842000000") } ],
  current: [ { symbol: "TATAMOTORS", weight: 0.0355 } ],
  turnover_estimate: 0.2143,

  recommendation_set_id: "paper_momentum_v1_001:2026-09:r1",
  started_at:  ISODate("2026-09-30T14:33:10Z"),
  completed_at: ISODate("2026-09-30T14:35:02Z"),
  github_run_id: "1198374652",
  error: null
}
```

**Indexes**

| Index | Type | Purpose |
|---|---|---|
| `_id` | unique | `portfolio:period` — **a second monthly run for the same period cannot insert** |
| `{ portfolio_id: 1, period: -1 }` | — | History |
| `{ status: 1 }` | partial, `status ∈ {pending, generating, executing}` | Find stuck runs |

**Retention:** permanent (≈12 docs/year/portfolio).

---

### 2.6 `audit_logs`

Append-only. Every state transition, with enough context to answer "why is the book in this
state?" without re-deriving it.

```javascript
{
  _id: ObjectId("…"),
  ts: ISODate("2026-10-01T03:47:11Z"),
  portfolio_id: "paper_momentum_v1_001",
  actor: "user",                                // system | user | github-actions
  action: "recommendation.item.executed",
  entity_type: "recommendation",
  entity_id: "paper_momentum_v1_001:2026-09:r1",
  before: { "items.0.status": "pending" },
  after:  { "items.0.status": "executed",
            "items.0.executed_price": Decimal128("2402.75") },
  context: { job_run_id: "mark_executed:2026-10-01:1",
             github_run_id: "1198401122",
             code_commit: "e5df354…" },
  severity: "info"                              // info | warning | error
}
```

**Indexes**

| Index | Type | Purpose |
|---|---|---|
| `{ portfolio_id: 1, ts: -1 }` | — | Portfolio timeline |
| `{ entity_type: 1, entity_id: 1, ts: 1 }` | — | Full history of one entity |
| `{ ts: -1 }` | — | Global recent activity |
| `{ severity: 1, ts: -1 }` | partial, `severity ≠ "info"` | Problem review |

**Retention:** **no TTL on financial actions** (`recommendation.*`, `holding.*`,
`portfolio.*`) — they are the audit trail. TTL of **90 days** on `severity: "info"` entries
whose action starts with `job.` or `data.`, which are operational noise. Implement as a TTL
index on a nullable `expires_at` field set only on those, rather than a TTL on `ts` — a TTL
on `ts` would eventually delete your trade history.

```javascript
db.audit_logs.createIndex({ expires_at: 1 }, { expireAfterSeconds: 0 })
```

Estimated volume: ~60 events/day ≈ 15 k/year ≈ **8 MB/year**.

---

### 2.7 `job_runs`

Every workflow execution. The reconciliation ledger that §0.1 depends on.

```javascript
{
  _id: "daily_ingest:2026-09-30:1",             // job:business_date:attempt
  job_name: "daily_ingest",
  business_date: "2026-09-30",
  attempt: 1,
  status: "success",                            // running | success | failed | skipped
  skip_reason: null,                            // "weekend" | "holiday" | "already_done"

  started_at:  ISODate("2026-09-30T14:30:04Z"),
  finished_at: ISODate("2026-09-30T14:32:41Z"),
  duration_ms: 157000,

  github_run_id: "1198374652",
  github_run_url: "https://github.com/akashdnb/algotrade/actions/runs/1198374652",
  code_commit: "e5df354…",
  runner_os: "ubuntu-24.04",

  result: { sessions_ingested: 1, symbols: 2041,
            integrity_breaks: 0, portfolios_valued: 2 },
  error: null
  // error: { type: "NseFetchError", message: "…", traceback_tail: "…" }
}
```

**Indexes**

| Index | Type | Purpose |
|---|---|---|
| `_id` | unique | `job:date:attempt` |
| `{ job_name: 1, business_date: 1 }` | **unique, partial: `status = "success"`** | **At most one successful run per job per business date — this is the exactly-once guarantee (§11.5)** |
| `{ status: 1, started_at: -1 }` | — | Find running/failed |
| `{ business_date: -1 }` | — | Gap detection: which dates have no success? |

**Retention:** TTL **365 days** on a `expires_at` field. Job telemetry older than a year has
no forensic value; the financial record lives elsewhere.

---

### 2.8 `notifications`

Outbound message log. Exists so a retry cannot spam you, and so a silent delivery failure is
visible.

```javascript
{
  _id: "daily_summary:paper_momentum_v1_001:2026-09-30",  // kind:portfolio:date
  kind: "daily_summary",
  portfolio_id: "paper_momentum_v1_001",
  business_date: "2026-09-30",
  channel: "telegram",
  chat_id: "-1002…",

  payload_hash: "sha256:c81f…",                 // dedupe identical resends
  rendered_text: "…",                            // exactly what was sent
  status: "sent",                                // queued | sent | failed | suppressed
  attempts: 1,
  sent_at: ISODate("2026-09-30T14:33:00Z"),
  telegram_message_id: 48213,
  error: null
}
```

**Indexes**

| Index | Type | Purpose |
|---|---|---|
| `_id` | unique | `kind:portfolio:date` — **a re-run cannot double-send** |
| `{ status: 1, business_date: -1 }` | — | Retry queue / failure review |

**Retention:** TTL **180 days**.

> **Deliberate exception:** alerts of kind `job_failure` and `system_health` use
> `kind:portfolio:date:hash` as `_id`, so a *different* failure on the same day still sends.
> Deduping those by date alone would suppress the second, different problem.

### 2.9 Schema validation

Apply MongoDB JSON Schema validators (`validationLevel: "strict"`) to all eight collections
— particularly enforcing `Decimal128` on money fields and enum membership on every `status`.
A typo'd status string that silently creates a new state is a bug class worth eliminating at
the database rather than in review.

---

## 3. `portfolio_id` Strategy

### 3.1 Format

```
{mode}_{strategy}_{version}_{seq}
   │        │         │       │
   │        │         │       └── 3-digit sequence, e.g. 001
   │        │         └────────── frozen strategy version, e.g. v1
   │        └──────────────────── strategy family, e.g. momentum
   └───────────────────────────── paper | live | shadow
```

Regex: `^(paper|live|shadow)_[a-z]+_v\d+_\d{3}$`. Validated in `domain/ids.py` and enforced
by the schema validator on `portfolios._id`.

| id | Role |
|---|---|
| `paper_momentum_v1_001` | Paper trading. Recommendations auto-filled at next open. |
| `live_momentum_v1_001` | Real money. Recommendations executed manually by you. |
| `shadow_momentum_v1_001` | Runs alongside live, takes **every** recommendation blindly. |

### 3.2 Why `shadow` exists

§9's core insight: *the backtest assumes every signal is taken.* `shadow` executes every
recommendation at the reference price with modelled costs, no exceptions, no skips.

- `live` vs `shadow` = **the cost of your discretion**, in rupees.
- `shadow` vs backtest = **the cost of reality** (real fills, real timing).

Both are unmeasurable without this portfolio, and §11o names "real fills" as the single
piece of missing evidence blocking scale-up. Running `shadow` from day one costs nothing —
it is a few hundred documents — and it is the only way to learn whether your manual
execution is helping or destroying alpha.

### 3.3 Immutability rules

1. **A `portfolio_id` is never reused, renamed, or reassigned.**
2. **Strategy parameters are frozen at creation.** `portfolios.strategy_params_hash` is
   compared against the live config on every job. Mismatch → refuse to run, alert (§9.2).
3. **A parameter change is a new portfolio, never an edit.** Bump to `v2`, create
   `live_momentum_v2_001`, and either close `v1` or run both in parallel. History stays
   interpretable because no document ever meant two different things.
4. **`mode` never changes.** Promoting paper→live creates a *new* live portfolio; the paper
   one continues. You keep the paper track record, and the live NAV starts honestly at its
   real starting capital rather than inheriting a simulated one.

### 3.4 Evolution path

```
paper_momentum_v1_001  ──(6–12 months, gate in §9.5)──▶  live_momentum_v1_001
        │                                                        │
        │                                                        ├─ shadow_momentum_v1_001
        │                                                        │
        └── continues running forever (free, and the control arm)

parameter change ──▶ paper_momentum_v2_001 ──▶ live_momentum_v2_001
                     (v1 keeps running until v2 has earned the switch)
```

---

## 4. GitHub Actions Architecture

All crons are **UTC**. IST = UTC+5:30. NSE `sec_bhavdata_full` publishes ~18:00–19:00 IST.

| Workflow | Cron (UTC) | IST | Purpose |
|---|---|---|---|
| `daily.yml` | `30 14 * * 1-5` | 20:00 | Ingest, validate, value, publish, notify |
| `rebalance.yml` | `0 15 * * 1-5` | 20:30 | **Checks** whether a rebalance is due; usually exits in seconds |
| `watchdog.yml` | `0 4 * * *` | 09:30 | Staleness / gap / stuck-run detection |
| `mark-executed.yml` | manual | — | Record actual fills |
| `regenerate.yml` | manual | — | Supersede a recommendation set |
| `recover.yml` | manual | — | Backfill a date range |

**`rebalance.yml` runs daily on purpose.** A monthly cron (`0 15 28-31 * *`) fires on
calendar days that may not be trading days, and if that single firing is dropped (§0.1) the
month is silently missed. Running the *check* daily and letting it exit early makes the
rebalance self-healing: whenever it notices the period is due and has no completed run, it
runs. Cost is ~20 seconds/day.

### 4.1 Daily workflow

```yaml
# .github/workflows/daily.yml
name: Daily

on:
  schedule:
    - cron: '30 14 * * 1-5'      # 20:00 IST, Mon-Fri
  workflow_dispatch:
    inputs:
      date:
        description: 'Business date (YYYY-MM-DD). Blank = today IST.'
        required: false
        type: string

permissions:
  contents: write                 # commit the day's session CSV

concurrency:
  group: daily-${{ github.ref }}
  cancel-in-progress: false       # never cancel a half-written run

jobs:
  daily:
    runs-on: ubuntu-latest
    timeout-minutes: 25
    environment: paper
    env:
      ALGOTRADE_ENV:  paper
      MONGODB_URI:    ${{ secrets.MONGODB_URI }}
      TELEGRAM_TOKEN: ${{ secrets.TELEGRAM_TOKEN }}
      TELEGRAM_CHAT:  ${{ secrets.TELEGRAM_CHAT }}

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.13'
          cache: pip

      - run: pip install -r requirements.txt

      # Tier 1: 720 MB frozen archive. Immutable key -> permanent cache hit.
      - name: Restore market-data archive
        id: archive
        uses: actions/cache@v4
        with:
          path: data/archive
          key: archive-v1

      - name: Download archive (cache miss only)
        if: steps.archive.outputs.cache-hit != 'true'
        run: |
          gh release download data-archive-v1 \
            --pattern 'archive.tar.zst' --dir /tmp
          mkdir -p data/archive
          tar --use-compress-program=unzstd -xf /tmp/archive.tar.zst -C data/archive
        env:
          GH_TOKEN: ${{ github.token }}

      # Tier 3: derived panel. Prefix restore-keys so yesterday's build seeds today's.
      - name: Restore curated panel
        uses: actions/cache@v4
        with:
          path: data/curated
          key: panel-${{ hashFiles('data/sessions/**/*.csv') }}
          restore-keys: panel-

      - name: Resolve business date
        id: bd
        run: |
          D="${{ inputs.date }}"
          [ -z "$D" ] && D=$(TZ=Asia/Kolkata date +%F)
          echo "date=$D" >> "$GITHUB_OUTPUT"

      # Reconciliation, not "today": fills EVERY missing session up to the target
      # date, so a dropped or delayed run self-heals with no intervention (§0.1).
      - name: Ingest missing sessions
        id: ingest
        run: python -m jobs.ingest_session --through ${{ steps.bd.outputs.date }}

      - name: Validate market data
        run: python -m jobs.ingest_session --validate-only --through ${{ steps.bd.outputs.date }}

      - name: Commit new session files
        if: steps.ingest.outputs.new_sessions != '0'
        run: |
          git config user.name  'algotrade-bot'
          git config user.email 'bot@users.noreply.github.com'
          git add data/sessions
          git commit -m "data: sessions through ${{ steps.bd.outputs.date }} [skip ci]"
          git push

      - name: Value portfolios
        run: python -m jobs.value_portfolios --date ${{ steps.bd.outputs.date }}

      - name: Publish dashboard JSON
        run: python -m jobs.publish_api --date ${{ steps.bd.outputs.date }}

      - name: Send daily summary
        run: python -m jobs.notify --kind daily_summary --date ${{ steps.bd.outputs.date }}

      # External dead-man's switch: if THIS ping stops arriving, healthchecks.io
      # alerts you. A system cannot alert on its own total failure (§10.6).
      - name: Heartbeat
        if: success()
        run: curl -fsS -m 10 --retry 3 "${{ secrets.HEALTHCHECK_DAILY_URL }}"

      - name: Alert on failure
        if: failure()
        run: python -m jobs.notify --kind job_failure --job daily --date ${{ steps.bd.outputs.date }}
        continue-on-error: true
```

Two details worth naming. `[skip ci]` in the commit message prevents any future push-
triggered workflow from looping; and the commit itself is what keeps the repository active,
defeating the 60-day scheduled-workflow disable (§0.1). The heartbeat is deliberately the
last successful step.

### 4.2 Monthly rebalance workflow

```yaml
# .github/workflows/rebalance.yml
name: Rebalance

on:
  schedule:
    - cron: '0 15 * * 1-5'       # 20:30 IST, after daily
  workflow_dispatch:
    inputs:
      portfolio_id: { required: true, type: string }
      period:       { description: 'YYYY-MM. Blank = current.', required: false, type: string }
      force:        { description: 'Regenerate even if completed', type: boolean, default: false }

permissions:
  contents: read

concurrency:
  group: rebalance-${{ inputs.portfolio_id || 'scheduled' }}
  cancel-in-progress: false

jobs:
  check:
    runs-on: ubuntu-latest
    timeout-minutes: 5
    outputs:
      due: ${{ steps.due.outputs.due }}
      portfolios: ${{ steps.due.outputs.portfolios }}
    env:
      MONGODB_URI: ${{ secrets.MONGODB_URI }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.13', cache: pip }
      - run: pip install -r requirements.txt
      # Due if: today is the last trading session of the period, AND no
      # completed rebalance_run exists for it. Both facts come from state,
      # never from "the cron fired" (§0.1).
      - id: due
        run: python -m jobs.run_rebalance --check-only

  rebalance:
    needs: check
    if: needs.check.outputs.due == 'true'
    runs-on: ubuntu-latest
    timeout-minutes: 20
    environment: paper
    strategy:
      matrix:
        portfolio_id: ${{ fromJSON(needs.check.outputs.portfolios) }}
      max-parallel: 1              # serialise writes; the book is small
    env:
      ALGOTRADE_ENV:  paper
      MONGODB_URI:    ${{ secrets.MONGODB_URI }}
      TELEGRAM_TOKEN: ${{ secrets.TELEGRAM_TOKEN }}
      TELEGRAM_CHAT:  ${{ secrets.TELEGRAM_CHAT }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.13', cache: pip }
      - run: pip install -r requirements.txt

      - uses: actions/cache@v4
        with: { path: data/archive, key: archive-v1 }
      - uses: actions/cache@v4
        with:
          path: data/curated
          key: panel-${{ hashFiles('data/sessions/**/*.csv') }}
          restore-keys: panel-

      # Refuses to run if the strategy params hash differs from the one the
      # portfolio was created with (§1.3, §9.2).
      - name: Verify frozen strategy
        run: python -m jobs.run_rebalance --verify-frozen
             --portfolio-id ${{ matrix.portfolio_id }}

      - name: Generate recommendations
        run: python -m jobs.run_rebalance
             --portfolio-id ${{ matrix.portfolio_id }}
             --period '${{ inputs.period }}'
             ${{ inputs.force && '--force' || '' }}

      - name: Notify
        run: python -m jobs.notify --kind rebalance
             --portfolio-id ${{ matrix.portfolio_id }}

      - name: Alert on failure
        if: failure()
        run: python -m jobs.notify --kind job_failure --job rebalance
             --portfolio-id ${{ matrix.portfolio_id }}
        continue-on-error: true
```

### 4.3 Manual workflows

```yaml
# .github/workflows/mark-executed.yml
name: Mark Executed

on:
  workflow_dispatch:
    inputs:
      set_id:
        description: 'Recommendation set id'
        required: true
        type: string
      fills:
        description: >
          JSON array of fills:
          [{"seq":1,"quantity":12,"price":"2402.75","charges":"49.05",
            "order_id":"2510010001"}]
          Omit an item to leave it pending; use "skip":"reason" to decline it.
        required: true
        type: string
      confirm:
        description: 'Type the set_id again to confirm'
        required: true
        type: string

permissions: { contents: read }
concurrency:
  group: mark-executed-${{ inputs.set_id }}
  cancel-in-progress: false

jobs:
  mark:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    environment: paper          # use `live` (with required reviewer) for real money
    env:
      MONGODB_URI:    ${{ secrets.MONGODB_URI }}
      TELEGRAM_TOKEN: ${{ secrets.TELEGRAM_TOKEN }}
      TELEGRAM_CHAT:  ${{ secrets.TELEGRAM_CHAT }}
    steps:
      - name: Confirm
        run: |
          [ "${{ inputs.set_id }}" = "${{ inputs.confirm }}" ] \
            || { echo "::error::confirmation does not match set_id"; exit 1; }
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.13', cache: pip }
      - run: pip install -r requirements.txt
      - name: Apply fills
        run: python -m jobs.mark_executed
             --set-id '${{ inputs.set_id }}'
             --fills '${{ inputs.fills }}'
      - name: Notify
        run: python -m jobs.notify --kind execution_recorded --set-id '${{ inputs.set_id }}'
```

```yaml
# .github/workflows/recover.yml
name: Recover

on:
  workflow_dispatch:
    inputs:
      from_date: { required: true, type: string }
      to_date:   { required: true, type: string }
      steps:
        type: choice
        default: all
        options: [all, ingest, value, snapshots]

permissions: { contents: write }
concurrency: { group: recover, cancel-in-progress: false }

jobs:
  recover:
    runs-on: ubuntu-latest
    timeout-minutes: 60          # a long gap may be many sessions
    environment: paper
    env:
      ALGOTRADE_ENV: paper
      MONGODB_URI:   ${{ secrets.MONGODB_URI }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.13', cache: pip }
      - run: pip install -r requirements.txt
      - uses: actions/cache@v4
        with: { path: data/archive, key: archive-v1 }
      # Idempotent by construction: every write is keyed by portfolio:date, so
      # re-running an already-processed range is a no-op (§2.4, §11.5).
      - run: python -m jobs.recover
             --from ${{ inputs.from_date }}
             --to   ${{ inputs.to_date }}
             --steps ${{ inputs.steps }}
      - run: |
          git config user.name  'algotrade-bot'
          git config user.email 'bot@users.noreply.github.com'
          git add data/sessions
          git diff --staged --quiet || \
            git commit -m "data: recover ${{ inputs.from_date }}..${{ inputs.to_date }} [skip ci]"
          git push
```

```yaml
# .github/workflows/watchdog.yml
name: Watchdog

on:
  schedule:
    - cron: '0 4 * * *'          # 09:30 IST, before market open
  workflow_dispatch:

permissions: { contents: read }

jobs:
  watchdog:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    env:
      MONGODB_URI:    ${{ secrets.MONGODB_URI }}
      TELEGRAM_TOKEN: ${{ secrets.TELEGRAM_TOKEN }}
      TELEGRAM_CHAT:  ${{ secrets.TELEGRAM_CHAT }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.13', cache: pip }
      - run: pip install -r requirements.txt
      # Checks every threshold in §10 and alerts on breach.
      - run: python -m jobs.watchdog --alert
```

---

## 5. Application Services

Each service is a class with an explicit constructor (dependencies injected, never
imported globally) so that every one is testable with fakes and none reaches for a
connection at import time.

### 5.1 Data Ingestion Service

**Responsibility:** get raw NSE sessions onto disk. Nothing else. No parsing decisions, no
domain logic.

```python
class IngestionService:
    def __init__(self, nse_client, session_dir: Path, jobs: JobRepository, clock: Clock): ...

    def missing_sessions(self, through: date) -> list[date]:
        """Weekdays with no session file, from last-present through `through`.
        The gap list -- NOT 'today' -- is the unit of work (§0.1)."""

    def ingest(self, through: date) -> IngestResult:
        """Fetch every missing session. A 404 is a holiday, recorded, not an error
        (NSE's archive 404 is trustworthy; nsefin's holiday calendar is not, §10.9).
        Returns counts of fetched / holiday / failed."""
```

Reuses the existing, tested `algotrade.io.nse` (handles NSE's CSV-that-is-actually-XLSX
quirk, retries, atomic writes).

### 5.2 Market Data Validation Service

**Responsibility:** decide whether the data is *fit to trade on*. This service holds a veto.

```python
class ValidationService:
    def validate(self, panel: pl.DataFrame, session: date) -> ValidationReport:
        """Runs, in order:
          - session present and non-empty
          - symbol count within tolerance of trailing median (±15%)
          - prev_close chain breaks explained by corporate actions (integrity.py)
          - no zero/negative prices; no null closes in the universe
          - index series present for the session
        Returns a report with severity: ok | warn | block."""
```

**A `block` stops the pipeline before any portfolio is valued or any recommendation
generated.** This project has repeatedly found that bad data produces confident, plausible,
wrong numbers — an unadjusted 1:10 split reads as a 90% crash and momentum ranking would
act on it. Failing loudly is strictly better than trading on it.

### 5.3 Momentum Ranking Service

**Responsibility:** the frozen signal. Thin by design.

```python
class RankingService:
    def __init__(self, params: StrategyParams): ...

    def rank(self, panel: pl.DataFrame, as_of: date) -> list[Ranking]:
        """Top-N by 12-1 momentum within the liquid-300 universe as of `as_of`.
        Delegates entirely to algotrade.core (universe -> features -> strategies
        -> buffering). No signal logic lives here -- there must be exactly one
        implementation of the strategy, shared with the backtester."""

    def target_weights(self, panel: pl.DataFrame, held: set[str], as_of: date) -> dict[str, float]:
        """Buffered target: keep held names inside exit=60, refill from entry=30.
        `held` matters -- the buffer is path-dependent, so the target is a function
        of the CURRENT book, not of the ranking alone."""
```

### 5.4 Portfolio Construction Service

**Responsibility:** turn fractional target weights into **integer share counts** that fit
available cash. This is where the backtest's clean arithmetic meets reality.

```python
class ConstructionService:
    def build(self, targets: dict[str, float], nav: Decimal, cash: Decimal,
              prices: dict[str, Decimal], card: RateCard) -> ProposedBook:
        """Integer quantities, largest-remainder allocation, min_trade_value=500
        honoured (§ engine.run). Reserves an estimated-charges buffer so the last
        buy does not fail for want of a few rupees. Returns the residual cash."""
```

Two realities the backtest does not model and this service must:

- **T+1 settlement.** Sale proceeds are not spendable the same day. `build` must sequence
  sells before buys and flag any buy that depends on unsettled funds — otherwise the
  recommendation is not executable and you discover that at the broker.
- **Integer share rounding at ₹2 L.** A 1/30 weight is ~₹9,500; a ₹5,800 share means 1 share
  = 0.6 of a target position. Rounding error is material at this size and must be reported,
  not hidden.

### 5.5 Recommendation Service

**Responsibility:** diff current book against target, produce an ordered, executable
instruction set, and own its lifecycle.

```python
class RecommendationService:
    def generate(self, portfolio_id: str, period: str, as_of: date,
                 *, force: bool = False) -> RecommendationSet:
        """Idempotent: an existing completed run for (portfolio, period) returns it
        untouched unless force=True, in which case a NEW revision is created and
        the prior one marked superseded -- never mutated (§9.3)."""

    def record_fills(self, set_id: str, fills: list[Fill]) -> RecommendationSet:
        """Apply ACTUAL executed quantities/prices. Computes realised slippage vs
        the reference price, updates holdings and FIFO lots, recomputes cash.
        Rejects a 'skipped' item with no skip_reason (§2.3)."""

    def expire_stale(self, as_of: date) -> int:
        """Sets expire after `expires_on`. A 4-day-old buy list is not a decision
        the signal actually made -- acting on it is acting on stale information."""
```

**Ordering rule:** sells first (frees cash and is settlement-critical), then buys in
descending target weight, so a partial execution leaves the book closest to target.

### 5.6 Notification Service

```python
class NotificationService:
    def send(self, kind: str, portfolio_id: str | None, business_date: str,
             payload: dict) -> Notification:
        """Renders a template, dedupes on the notification _id, sends via Telegram,
        records status. Never raises: a broken alert channel must not fail an
        otherwise-successful run -- but it MUST be recorded as failed so the
        watchdog can surface it (§10.5)."""
```

### 5.7 Portfolio Tracking Service

```python
class TrackingService:
    def value(self, portfolio_id: str, as_of: date) -> Snapshot:
        """Mark holdings to that session's close, apply any contribution due,
        recompute units/unit_nav, drawdown vs peak, benchmark level. Writes with
        _id = portfolio:date, so re-running is a no-op (§2.4)."""

    def drift(self, portfolio_id: str, as_of: date) -> DriftReport:
        """Actual weights vs the last recommendation's target. Distinguishes drift
        from price movement (expected, ignored between rebalances) from drift
        caused by UNEXECUTED recommendations (a real problem, §10.4)."""

    def performance(self, portfolio_id: str, since: date | None) -> Performance:
        """Time-weighted (unit_nav) AND money-weighted (XIRR). With ₹20k monthly
        contributions the two diverge materially, and only XIRR answers 'what did
        I actually earn' -- the point §11n turned on."""
```

---

## 6. REST API Design

Base: `/api/v1`. Reads are served as static JSON (§0.3); the paths below are the contract
either implementation satisfies. All money is a **string** in JSON to preserve decimal
precision through JavaScript's float64.

### 6.1 Portfolios

```http
GET /api/v1/portfolios
```
```json
{
  "data": [
    { "portfolio_id": "paper_momentum_v1_001", "mode": "paper",
      "strategy_version": "momentum_v1", "status": "active",
      "nav": "284310.22", "cash": "14320.55",
      "total_contributed": "260000.00", "unit_nav": "117.8305",
      "return_twr": 0.1783, "return_xirr": 0.1512,
      "drawdown": -0.0297, "as_of": "2026-09-30" }
  ],
  "meta": { "generated_at": "2026-09-30T14:40:11Z", "count": 2 }
}
```

```http
GET /api/v1/portfolios/paper_momentum_v1_001
```
```json
{
  "data": {
    "portfolio_id": "paper_momentum_v1_001", "mode": "paper",
    "strategy": { "version": "momentum_v1",
                  "params": { "lookback": 252, "skip": 21, "entry": 30, "exit": 60,
                              "cadence": "monthly" },
                  "params_hash": "sha256:9f2c…" },
    "capital": { "initial": "200000.00", "monthly_contribution": "20000.00",
                 "total_contributed": "260000.00", "cash": "14320.55" },
    "valuation": { "nav": "284310.22", "invested": "269989.67",
                   "units": "2412.887", "unit_nav": "117.8305",
                   "peak_unit_nav": "121.4400", "drawdown": -0.0297 },
    "performance": { "twr": 0.1783, "xirr": 0.1512,
                     "benchmark_twr": 0.1104, "excess": 0.0679 },
    "as_of": "2026-09-30"
  }
}
```

### 6.2 Holdings

```http
GET /api/v1/portfolios/{id}/holdings
```
```json
{
  "data": [
    { "symbol": "TATAMOTORS", "isin": "INE155A01022",
      "quantity": 47, "avg_cost": "981.4200", "cost_basis": "46126.74",
      "close": "1012.30", "value": "47578.10", "weight": 0.1673,
      "unrealised_pnl": "1451.36", "unrealised_pct": 0.0315,
      "first_acquired": "2026-09-02", "days_held": 28,
      "tax_status": "short_term",
      "lots": [ { "lot_id": "L-2026-09-02-01", "acquired_on": "2026-09-02",
                  "quantity": 30, "price": "975.10", "days_held": 28,
                  "tax_status": "short_term" } ] }
  ],
  "meta": { "as_of": "2026-09-30", "count": 29,
            "total_value": "269989.67", "cash": "14320.55" }
}
```

### 6.3 Recommendations

```http
GET /api/v1/portfolios/{id}/recommendations?status=pending&limit=10
GET /api/v1/recommendations/{set_id}
```
```json
{
  "data": {
    "set_id": "paper_momentum_v1_001:2026-09:r1",
    "period": "2026-09", "revision": 1, "status": "partially_executed",
    "generated_at": "2026-09-30T14:35:00Z",
    "as_of_session": "2026-09-30", "expires_on": "2026-10-03",
    "reference_nav": "284310.22",
    "summary": { "items": 9, "executed": 7, "skipped": 2,
                 "estimated_charges": "612.40", "realised_slippage_bps": -18.4,
                 "turnover": 0.2143 },
    "items": [
      { "seq": 1, "action": "sell", "symbol": "ADANIENT",
        "reason": "rank 71 > exit 60",
        "quantity": 12, "reference_price": "2410.00",
        "estimated_value": "28920.00",
        "status": "executed", "executed_quantity": 12,
        "executed_price": "2402.75", "executed_at": "2026-10-01T03:47:00Z",
        "slippage_bps": -30.1 },
      { "seq": 2, "action": "buy", "symbol": "PERSISTENT",
        "reason": "rank 14 <= entry 30",
        "quantity": 5, "reference_price": "5820.00",
        "estimated_value": "29100.00",
        "status": "skipped",
        "skip_reason": "insufficient funds after sell settlement (T+1)" }
    ]
  }
}
```

**Write** (via `workflow_dispatch`, or `POST` if a server exists):

```http
POST /api/v1/recommendations/{set_id}/fills
```
```json
{
  "fills": [
    { "seq": 1, "quantity": 12, "price": "2402.75", "charges": "49.05",
      "order_id": "251001000123456", "executed_at": "2026-10-01T03:47:00Z" },
    { "seq": 2, "skip": "insufficient funds after sell settlement (T+1)" }
  ]
}
```
```json
{
  "data": { "set_id": "paper_momentum_v1_001:2026-09:r1",
            "status": "partially_executed",
            "applied": 1, "skipped": 1, "remaining_pending": 7,
            "cash_after": "42191.30",
            "warnings": ["item 2 skipped: target weight will remain unfilled at 0.0%"] }
}
```

### 6.4 Snapshots

```http
GET /api/v1/portfolios/{id}/snapshots?from=2026-09-01&to=2026-09-30&fields=nav,unit_nav
```
```json
{
  "data": [
    { "date": "2026-09-29", "nav": "281044.10", "unit_nav": "116.4771", "drawdown": -0.0409 },
    { "date": "2026-09-30", "nav": "284310.22", "unit_nav": "117.8305", "drawdown": -0.0297 }
  ],
  "meta": { "count": 2, "from": "2026-09-01", "to": "2026-09-30" }
}
```

```http
GET /api/v1/portfolios/{id}/snapshots/2026-09-30      # full, with positions
```

### 6.5 Audit logs

```http
GET /api/v1/audit?portfolio_id={id}&entity_id={set_id}&from=2026-09-01&limit=50
```
```json
{
  "data": [
    { "ts": "2026-10-01T03:47:11Z", "actor": "user",
      "action": "recommendation.item.executed",
      "entity_type": "recommendation",
      "entity_id": "paper_momentum_v1_001:2026-09:r1",
      "before": { "items.0.status": "pending" },
      "after":  { "items.0.status": "executed", "items.0.executed_price": "2402.75" },
      "context": { "github_run_id": "1198401122", "code_commit": "e5df354" } }
  ],
  "meta": { "count": 1, "has_more": false }
}
```

### 6.6 Job status

```http
GET /api/v1/jobs?status=failed&limit=20
GET /api/v1/health
```
```json
{
  "data": {
    "status": "degraded",
    "checks": [
      { "name": "market_data_freshness", "status": "ok",
        "detail": "last session 2026-09-30, 0 trading days behind" },
      { "name": "daily_job", "status": "ok",  "last_success": "2026-09-30T14:32:41Z" },
      { "name": "rebalance_current_period", "status": "warn",
        "detail": "2026-09 generated, 2 items pending for 3 days" },
      { "name": "notifications", "status": "ok", "failed_last_7d": 0 },
      { "name": "mongodb", "status": "ok", "latency_ms": 41 }
    ],
    "as_of": "2026-09-30T14:40:11Z"
  }
}
```

### 6.7 Conventions

| Concern | Decision |
|---|---|
| Money in JSON | **Strings.** `"284310.22"`, never `284310.22`. |
| Dates | Business dates `"YYYY-MM-DD"`; instants ISO-8601 UTC with `Z`. |
| Errors | `{"error": {"code": "RECOMMENDATION_EXPIRED", "message": "…", "details": {…}}}` |
| Pagination | `limit` + `cursor`; `meta.has_more`. Never offset (snapshots grow). |
| Auth | Static JSON: repo/Pages access. Server: single bearer token in `Authorization`. |
| Versioning | `/v1` in the path. Additive changes only within a version. |

---

## 7. Telegram Integration

Design rules: **every message states the portfolio and the as-of date** (you will read these
months later out of context); **every actionable message carries the exact command** to act
on it; and **numbers are pre-formatted server-side** — no mental arithmetic at 8pm.

Use `parse_mode: MarkdownV2` and escape reserved characters. Keep under 4,096 chars; the
rebalance message with 30 names needs splitting or truncation with a link.

### 7.1 Daily summary

```
📊 *Daily* · paper_momentum_v1_001 · 30 Sep 2026

NAV        ₹2,84,310  (+₹3,266 · +1.16%)
Cash       ₹14,321
Holdings   29 names

Month      +4.21%      NIFTY +2.08%   (+2.13%)
YTD        +17.83%     NIFTY +11.04%  (+6.79%)
Drawdown   −2.97%      (peak 12 Sep)

Top    PERSISTENT +4.8%  ·  DIXON +3.9%  ·  TRENT +3.1%
Worst  ADANIENT  −3.2%  ·  ZOMATO −2.4%

⚠️ 2 items pending from 2026-09 for 3 days
Next rebalance: 31 Oct 2026 (22 sessions)
```

Sent only on trading days, after successful valuation. **If the daily summary stops
arriving, that is itself the alert** — which is why the external heartbeat (§10.6) exists to
catch the case where the job is too broken to tell you.

### 7.2 Monthly rebalance

```
🔄 *Rebalance* · paper_momentum_v1_001 · 2026-09
Signal as of close 30 Sep 2026 · fills at next open
Set: `paper_momentum_v1_001:2026-09:r1`

Turnover 21.4%  ·  9 trades  ·  est. charges ₹612

*SELL FIRST* (T+1 settlement)
1. ADANIENT      12 @ ~₹2,410   ₹28,920   rank 71 → out
2. ZOMATO        94 @ ~₹  312   ₹29,328   rank 66 → out

*THEN BUY*
3. PERSISTENT     5 @ ~₹5,820   ₹29,100   rank 14
4. DIXON          2 @ ~₹14,050  ₹28,100   rank 19

Cash after: ~₹13,900
⏳ Expires 3 Oct 2026 — after that the signal is stale

Record fills:
`gh workflow run mark-executed.yml -f set_id=paper_momentum_v1_001:2026-09:r1 -f fills='[...]' -f confirm=paper_momentum_v1_001:2026-09:r1`
```

### 7.3 Recommendation generated (paper / shadow, auto-filled)

```
🤖 *Auto-executed* · shadow_momentum_v1_001 · 2026-09
9/9 filled at 1 Oct open · slippage −18.4 bps vs reference
NAV ₹2,86,004  ·  cash ₹9,112

vs live_momentum_v1_001: shadow +₹1,694 (+0.59%)
   ↳ cost of discretion, cumulative: −₹4,220 (−1.44%)
```

That last line is §9's behavioural audit rendered as a habit. Seeing it monthly is what
turns "am I helping?" from a feeling into a number.

### 7.4 Job failure

```
🔴 *Job failed* · daily · 2026-09-30 · attempt 1

NseFetchError: 2026-09-30: giving up after 3 attempts
  (HTTP 503, 0b)

Stage: ingest_session
Run:   https://github.com/akashdnb/algotrade/actions/runs/1198374652

Impact: no snapshot for 2026-09-30. Valuation is stale.
Self-heals: tomorrow's run backfills automatically.
Act now only if this repeats 2 days running.
```

Stating the blast radius and whether it self-heals is the difference between an alert you
act on and an alert you learn to ignore.

### 7.5 Stale data warning

```
🟠 *Stale market data* · 2026-10-02

Last session ingested: 2026-09-29 (2 trading days behind)
Missing: 2026-09-30, 2026-10-01
Daily job: last success 2026-09-29T14:32Z

All valuations and any rebalance are BLOCKED until this clears.
Recover: `gh workflow run recover.yml -f from_date=2026-09-30 -f to_date=2026-10-01`
```

### 7.6 System health

```
🩺 *Weekly health* · 2026-10-04

Data       ✅ current through 2026-10-03
Jobs       ✅ 22/22 succeeded (7d)
Mongo      ✅ 41 ms · 8.2 MB / 512 MB
Actions    ✅ 142 / 2000 min this month
Pending    ⚠️ 2 items unexecuted since 2026-09-30 (4 days)
Shadow     ℹ️ discretion cost −₹4,220 since inception

Next rebalance: 31 Oct 2026
```

---

## 8. Dashboard Design

React + Vite, static build, data from the JSON in §6. No client-side secrets — the API is
read-only and the repo/Pages access controls it.

### 8.1 Dashboard (home)

```
┌────────────────────────────────────────────────────────────────────┐
│  paper_momentum_v1_001  ▾        as of 30 Sep 2026    ● healthy    │
├────────────────────────────────────────────────────────────────────┤
│  ₹2,84,310        +1.16% today      XIRR 15.1%    TWR 17.8%        │
│  NAV              +₹3,266           vs NIFTY 11.0%  (+6.8%)        │
├────────────────────────────────────────────────────────────────────┤
│  ┌── NAV vs NIFTY (rebased) ─────────────────────────────────────┐ │
│  │                                                    ╭──── you  │ │
│  │                                      ╭──╯╰─────╯              │ │
│  │            ╭────╯╰────╮  ╭───╯                                │ │
│  │  ─────────╯           ╰──╯              ────── NIFTY          │ │
│  │  [1M] [3M] [6M] [1Y] [ALL]                                    │ │
│  └───────────────────────────────────────────────────────────────┘ │
│  ┌── Drawdown ───────────────────────────────────────────────────┐ │
│  │  0% ─────╮      ╭──────╮        ╭───────────                  │ │
│  │          ╰──────╯      ╰────────╯       now −2.97%            │ │
│  └───────────────────────────────────────────────────────────────┘ │
├──────────────────────────────┬─────────────────────────────────────┤
│  ⚠️ 2 items pending (3 days) │  Next rebalance: 31 Oct (22 sess.)  │
│     → Recommendations        │  Cash: ₹14,321                      │
└──────────────────────────────┴─────────────────────────────────────┘
```
**API:** `GET /portfolios`, `GET /portfolios/{id}`, `GET /portfolios/{id}/snapshots?fields=nav,unit_nav,drawdown`, `GET /health`

> The drawdown panel is deliberately as prominent as the return panel. §11p priced quitting
> during an underperformance streak at 27–40% of terminal wealth, and §11o's P4 concluded
> the behavioural risk cannot be sized away. A dashboard that shows only gains trains
> exactly the wrong reflex.

### 8.2 Current Holdings

```
┌────────────────────────────────────────────────────────────────────┐
│  Holdings · 29 names · ₹2,69,990 invested · ₹14,321 cash           │
│  [ search ]                        sort: weight ▾   [export CSV]   │
├──────────┬─────┬─────────┬─────────┬────────┬───────┬─────┬────────┤
│ Symbol   │ Qty │ Avg cost│  Close  │  Value │Weight │ P&L │ Tax    │
├──────────┼─────┼─────────┼─────────┼────────┼───────┼─────┼────────┤
│TATAMOTORS│  47 │  981.42 │ 1012.30 │ 47,578 │ 16.7% │+3.1%│ ST 28d │
│PERSISTENT│   5 │ 5820.00 │ 5901.15 │ 29,506 │ 10.4% │+1.4%│ ST  1d │
│ …        │     │         │         │        │       │     │        │
├──────────┴─────┴─────────┴─────────┴────────┴───────┴─────┴────────┤
│  ▸ expand a row for FIFO lots and acquisition history              │
└────────────────────────────────────────────────────────────────────┘
```
**API:** `GET /portfolios/{id}/holdings`

Weight bars are colour-coded against the 1/30 = 3.33% target so an over-concentrated
position from an unexecuted sell is visible at a glance.

### 8.3 Recommendations

```
┌────────────────────────────────────────────────────────────────────┐
│  2026-09 · r1 · partially executed · expires 3 Oct  ⏳ 1 day       │
│  Generated 30 Sep from close · fills at next open                  │
├────────────────────────────────────────────────────────────────────┤
│  SELL FIRST — T+1 settlement funds the buys                        │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ ✅ 1. ADANIENT   sell 12 @ ~2,410 → filled 2,402.75 (−30 bps)│  │
│  │ ✅ 2. ZOMATO     sell 94 @ ~  312 → filled   311.20 (−26 bps)│  │
│  └──────────────────────────────────────────────────────────────┘  │
│  THEN BUY                                                          │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ ⬜ 3. PERSISTENT  buy 5 @ ~5,820   ₹29,100    [mark] [skip]  │  │
│  │ ⏭️ 4. DIXON       skipped: insufficient funds after T+1       │  │
│  └──────────────────────────────────────────────────────────────┘  │
├────────────────────────────────────────────────────────────────────┤
│  7/9 executed · realised slippage −18.4 bps · charges ₹601         │
│  [ copy gh command ]    [ regenerate (supersedes r1) ]             │
└────────────────────────────────────────────────────────────────────┘
```
**API:** `GET /portfolios/{id}/recommendations`, `GET /recommendations/{set_id}`; writes via the copied `gh workflow run` command (or `POST /fills` if a server exists).

**A skip requires a typed reason before the button enables.** That friction is intentional —
it is the §9 mechanism that turns discretion into data.

### 8.4 Rebalance History

```
┌────────────────────────────────────────────────────────────────────┐
│  Period  │ Status    │Trades│Turnover│Executed│Slippage│ Charges   │
├──────────┼───────────┼──────┼────────┼────────┼────────┼───────────┤
│ 2026-09  │ partial   │   9  │ 21.4%  │  7/9   │ −18 bps│  ₹601     │
│ 2026-08  │ completed │  11  │ 26.1%  │ 11/11  │ −22 bps│  ₹744     │
│ 2026-07  │ completed │   7  │ 15.8%  │  7/7   │  −9 bps│  ₹455     │
├──────────┴───────────┴──────┴────────┴────────┴────────┴───────────┤
│  Acceptance rate: 92% (25/27) · avg slippage −16 bps               │
│  ┌── Slippage vs modelled (30 bps round trip) ────────────────────┐│
│  │  actual ▁▂▁▃▂▁▂  consistently inside model — headroom intact   ││
│  └────────────────────────────────────────────────────────────────┘│
└────────────────────────────────────────────────────────────────────┘
```
**API:** `GET /portfolios/{id}/rebalances`

This page answers §11o's open evidence item (a): *are real fills inside the modelled cost?*

### 8.5 Portfolio Performance

```
┌────────────────────────────────────────────────────────────────────┐
│  Performance          [ TWR | XIRR ]     [1Y|3Y|ALL]  vs NIFTY 50  │
├────────────────────────────────────────────────────────────────────┤
│  Metric          │  Portfolio  │  NIFTY 50  │  Excess              │
│  TWR (ann.)      │    17.8%    │    11.0%   │   +6.8%              │
│  XIRR            │    15.1%    │     9.8%   │   +5.3%              │
│  Max drawdown    │   −12.4%    │   −8.1%    │   −4.3%              │
│  Ulcer index     │      4.2    │     2.9    │                      │
│  Best / worst mo │  +9.1/−6.2% │ +5.4/−4.1% │                      │
├────────────────────────────────────────────────────────────────────┤
│  ┌── Rolling 12m excess vs NIFTY ────────────────────────────────┐ │
│  │  +10% ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄  │ │
│  │    0% ────╮  ╭───────────╮   ╭──────────────────────────      │ │
│  │  −10%     ╰──╯           ╰───╯   ← underperformance streak    │ │
│  └───────────────────────────────────────────────────────────────┘ │
│  Longest underperformance streak so far: 84 days                   │
│  Historical worst (2011–2026 backtest): 1,203 days ≈ 3.3 years     │
└────────────────────────────────────────────────────────────────────┘
```
**API:** `GET /portfolios/{id}/performance`, `GET /portfolios/{id}/snapshots`

> The last two lines are the most important pixels in the application. §11o P5 found that
> dilution does **not** shorten the underperformance streak (1,203 → 1,196 days across every
> allocation) — the behavioural problem cannot be sized away, only made shallower. Showing
> the historical worst *next to* the current streak is what makes an 84-day streak legible
> as normal rather than as evidence the strategy is broken.

### 8.6 Audit Trail

```
┌────────────────────────────────────────────────────────────────────┐
│  Audit   [portfolio ▾] [entity ▾] [action ▾] [date range]  [export]│
├────────────────────────────────────────────────────────────────────┤
│ 01 Oct 03:47  user    recommendation.item.executed                 │
│               …:2026-09:r1 item 1  pending → executed @ 2402.75    │
│               run 1198401122 · commit e5df354                      │
│ 30 Sep 14:35  system  recommendation.generated                     │
│               …:2026-09:r1  9 items · turnover 21.4%               │
│ 30 Sep 14:33  system  rebalance.started    period 2026-09          │
└────────────────────────────────────────────────────────────────────┘
```
**API:** `GET /audit`

### 8.7 System Health

```
┌────────────────────────────────────────────────────────────────────┐
│  System Health                                    ● degraded       │
├────────────────────────────────────────────────────────────────────┤
│  ✅ Market data     current through 2026-10-03                     │
│  ✅ Daily job       22/22 succeeded (7d) · last 14:32Z             │
│  ⚠️ Recommendations 2 items pending 4 days (threshold 3)           │
│  ✅ Notifications   0 failures (7d)                                │
│  ✅ MongoDB         41 ms · 8.2 MB / 512 MB (1.6%)                 │
│  ✅ Actions minutes 142 / 2000 this month (7%)                     │
│  ✅ Heartbeat       last ping 30 Sep 14:33Z                        │
├────────────────────────────────────────────────────────────────────┤
│  Recent job runs                          [ recover… ] [ watchdog ]│
│  daily      2026-09-30  success  2m37s                             │
│  rebalance  2026-09-30  success  1m52s                             │
│  daily      2026-09-29  success  2m11s                             │
└────────────────────────────────────────────────────────────────────┘
```
**API:** `GET /health`, `GET /jobs`

---

## 9. Auditability & Reproducibility

### 9.1 Reconstruct holdings on any date

**Primary path — O(1):** read `portfolio_snapshots._id = "{portfolio_id}:{date}"`. It
embeds the full position list with quantities, closes, values, and weights. This is why
snapshots are written daily and kept forever (§2.4).

**Verification path — independent:** replay from inception. Start at
`portfolios.initial_capital`, apply every `recommendations.items` with
`status ∈ {executed, partial}` in `executed_at` order, apply contributions, apply corporate
actions. The two must agree.

```python
def reconstruct(portfolio_id: str, as_of: date) -> Book:
    """Replay from trade records. Independent of snapshots BY DESIGN -- a
    reconstruction that reads the snapshot cannot detect a corrupt snapshot."""
```

Run this comparison weekly in the watchdog. Any divergence between the snapshot and the
replay is a corruption alert. The project's own history (§11q, four audits, ~55 defects)
argues strongly for a redundant check rather than a single trusted path.

### 9.2 Reproduce a recommendation

`rebalance_runs.input_fingerprint` captures every input:

```javascript
{ panel_manifest_hash: "sha256:7d41…",  // exact market data
  last_session:        "2026-09-30",     // exact cutoff
  strategy_params_hash:"sha256:9f2c…",   // exact frozen params
  cost_model_hash:     "sha256:41ab…",   // exact rate card
  code_commit:         "e5df354…" }      // exact code
```

```bash
python -m jobs.run_rebalance --reproduce paper_momentum_v1_001:2026-09
```

Checks out `code_commit`, rebuilds the panel, asserts the manifest hash matches, re-runs,
and **diffs the produced target against the stored one**. A mismatch means one of: the panel
changed under you (a backfill), the params changed (§1.3 guard should have caught it), or
the code is non-deterministic. All three are bugs worth an alert.

This is the same mechanism as `shadow/parity.py` from the previous design — recomputing and
comparing against the stored answer — generalised to the full input set.

### 9.3 Trace recommendation generation

`audit_logs` filtered by `entity_id = "{set_id}"` yields the complete chain:

```
rebalance.started      → rebalance_run created, fingerprint stamped
data.validated         → ValidationReport ok, panel hash recorded
ranking.computed       → top-30 with feature values and ranks
target.constructed     → integer quantities, cash reserved, rounding residual
recommendation.generated → set r1 with 9 items
notification.sent      → telegram message_id 48213
item.executed ×7       → each with actual price, charges, slippage
item.skipped ×2        → each with a typed reason
recommendation.completed
```

Because `rebalance_runs.target` stores the **feature value and rank per name**, you can
always answer "why was PERSISTENT bought?" with `rank 14, momentum 1.4821` rather than by
re-deriving it a year later against data that may have been backfilled since.

### 9.4 Compare target vs actual

`TrackingService.drift` (§5.7), surfaced on the health page and in the weekly Telegram:

| Source of drift | Expected? | Action |
|---|---|---|
| Price movement between rebalances | Yes — the buffer exists precisely so you do not trade it | None |
| Unexecuted buy | **No** | Alert after 3 days; the book is not the validated strategy |
| Unexecuted sell | **No — worse** | Alert immediately; carrying a name past exit=60 is an active deviation |
| Integer rounding | Yes, ±1 share | Reported, not alerted |

### 9.5 Investigate a failed rebalance

1. `rebalance_runs._id = "{portfolio}:{period}"` → `status: "failed"`, `error`, `attempt`,
   `github_run_id`.
2. `job_runs` for the same business date → stage, traceback tail, runner, duration.
3. `audit_logs` by `entity_id` → exactly how far it got before failing.
4. `GET /jobs` → the GitHub run URL for full logs.
5. Recovery: `gh workflow run rebalance.yml -f portfolio_id=… -f period=… -f force=true`.
   The unique index on `(portfolio_id, period)` guarantees this creates a **new revision**
   rather than a duplicate run (§11.4).

**A failed rebalance never leaves partial state.** Recommendation generation is a single
document insert; either the set exists complete or it does not exist. Holdings are only
mutated by `record_fills`, which is a separate, explicitly-triggered step.

---

## 10. Production Monitoring

### 10.1 Thresholds

| Signal | Warn | Critical | Detection | Action |
|---|---|---|---|---|
| **Market data stale** | 1 trading day behind | **2 trading days** | Watchdog compares last `data/sessions` file to NSE calendar | Critical **blocks** valuation and rebalance |
| **Daily job failed** | 1 consecutive | **2 consecutive** | `job_runs` no `status:success` for a business date | 1 self-heals; 2 needs you |
| **Rebalance missing** | period ended, none generated by T+1 | **T+3** | No `rebalance_runs` with `status:completed` for the period | Run `rebalance.yml` manually |
| **Recommendation unexecuted** | 2 days | **3 days** (= `expires_on`) | `recommendations.status = pending/partial` | Execute or explicitly skip with a reason |
| **Portfolio drift** | any name >1.5× target weight | **>2×**, or a name past exit=60 still held | `TrackingService.drift` | Sells are critical, buys are warnings |
| **Notification failure** | 1 in 24h | **3 in 24h** | `notifications.status = failed` | Check bot token / chat id |
| **Heartbeat missing** | — | **26 h** | healthchecks.io (external) | The system is down and cannot tell you itself |
| **Mongo storage** | 400 MB (78%) | **480 MB (94%)** | Watchdog reads `dbStats` | Prune `job_runs` TTL, or upgrade to M10 |
| **Actions minutes** | 1,500/2,000 | **1,800/2,000** | Billing API | Reduce watchdog frequency |
| **Snapshot ≠ replay** | any divergence | any divergence | Weekly reconstruction check (§9.1) | Investigate immediately — implies corruption |

### 10.2 Stale market data

Compare the newest file in `data/sessions/` against expected sessions. Expected sessions
come from the data itself, not an external calendar: NSE's archive returning 404 for a date
is a trustworthy holiday signal, whereas `nsefin`'s holiday calendar fails silently and
returned `False` for Independence Day (§10.9). Two trading days behind **blocks** every
downstream write.

### 10.3 Failed GitHub Actions

Each workflow's `if: failure()` step notifies. But a workflow that never *starts* cannot
report — so the watchdog independently queries `job_runs` for business dates with no
successful run, and the external heartbeat covers total failure.

### 10.4 Missing recommendations

The watchdog computes: is the current period's last trading session in the past, and is
there no `completed` rebalance run for it? Warn at T+1, critical at T+3. This is the check
that catches a dropped monthly cron — and it is why `rebalance.yml` runs daily (§4).

### 10.5 Notification failures

`NotificationService.send` never raises, but always records status. A failed Telegram send
is invisible by definition — you cannot be told that you cannot be told — so the watchdog
queries `notifications` for `status: failed` and escalates to the heartbeat channel.

### 10.6 The unmonitorable case

**A system cannot alert on its own total failure.** If GitHub Actions is down, the repo is
disabled, or the workflow is deleted, no in-system check runs. The only remedy is an
external observer:

- **healthchecks.io** (free tier, 20 checks): the daily job pings on success; if no ping
  arrives within 26 hours, healthchecks emails/Telegrams you *from outside*.
- Grace period is deliberately >24 h so a weekend or holiday does not page you. Use a
  separate check with a monthly period for the rebalance.

This is the single most important monitor in the design, and the only one not hosted on the
infrastructure it is monitoring.

---

## 11. Disaster Recovery

Design premise: **the market data on disk and in git is the source of truth; MongoDB holds
derived application state.** Almost everything is rebuildable from
(sessions + recommendations + code).

### 11.1 MongoDB outage

| Aspect | Handling |
|---|---|
| **Detection** | Connection failure in any job → `job_runs` cannot be written either, so the heartbeat's absence is the signal |
| **During** | Jobs fail fast and do not partially write. Market data ingestion **still succeeds** (it writes to git, not Mongo), so no data is lost — only state updates are deferred |
| **Recovery** | `gh workflow run recover.yml -f from_date=… -f to_date=…`. Snapshots are keyed `portfolio:date`, so replaying the outage window is idempotent |
| **Backup** | Atlas M0 has **no automated backups.** Add a weekly `mongodump` in the watchdog, gzip, and upload as a GitHub Release asset (a few MB). Retain 12 weeks. **This is a real gap on the free tier — do not skip it.** |
| **Total loss** | Rebuild: portfolios from config, holdings + cash by replaying `recommendations` (§9.1), snapshots by re-valuing each date from the panel. Only `audit_logs` are unrecoverable — which is why they are backed up weekly |

### 11.2 GitHub outage

| Aspect | Handling |
|---|---|
| **During** | No jobs run. No data ingested. Nothing corrupts — the system simply pauses |
| **Impact** | If it spans a month-end, the rebalance is late. The strategy is monthly with a rank buffer; being a few days late is a real but small cost, and §11o's P2 showed 4× cost headroom |
| **Recovery** | The next successful daily run backfills every missing session automatically (§0.1). No manual step for outages under ~2 weeks |
| **Manual fallback** | The signal can be produced locally: `python -m jobs.run_rebalance --portfolio-id … --local`. Keep the archive on your laptop so this is always possible. **A system whose only execution path is a third party is a system with a single point of failure** |

### 11.3 Missed rebalance

| Aspect | Handling |
|---|---|
| **Detection** | Watchdog, T+1 warn / T+3 critical (§10.4) |
| **Automatic** | `rebalance.yml` runs *daily* and generates as soon as it notices the period is due and incomplete — most misses self-heal within 24 h |
| **Manual** | `gh workflow run rebalance.yml -f portfolio_id=… -f period=2026-09` |
| **If a full month is missed** | Do **not** generate the stale period. Generate the *current* period from current data. The buffered strategy is path-dependent but self-correcting: it reads the book it actually has and moves toward the current target. Executing a month-old signal is acting on information the strategy did not produce |

### 11.4 Corrupted recommendation run

Recommendation sets are **immutable**. A bad set is never edited.

```bash
gh workflow run regenerate.yml -f set_id=paper_momentum_v1_001:2026-09:r1 \
                               -f reason="stale prices: panel was 2 sessions behind"
```

Creates `:r2`, sets `r1.status = "superseded"` and `r2.supersedes = "…:r1"`. Both remain
readable forever. If items in `r1` were already executed, `r2` is generated against the
**post-execution** book, so no trade is duplicated.

### 11.5 Accidental duplicate execution

Four independent layers, in order of who stops it first:

1. **`concurrency` groups** in every workflow prevent two runs of the same job overlapping.
2. **Unique partial index** on `job_runs {job_name, business_date}` where `status:"success"`
   — a second successful run for the same date **cannot insert**.
3. **Deterministic `_id`s** — `portfolio:date` for snapshots, `portfolio:period` for
   rebalance runs, `portfolio:period:revision` for recommendation sets. Re-running produces
   a duplicate-key error, never a second document.
4. **`record_fills` is idempotent per item.** Applying the same fill twice is rejected: an
   item already `executed` with a matching `order_id` is a no-op, and one with a *different*
   `order_id` raises a conflict for you to resolve rather than silently double-counting
   shares.

Layer 4 matters most, because it is the only one guarding a *human* re-submitting the same
`gh workflow run` command twice — the realistic duplicate in a manual-execution system.

---

## 12. Cost Analysis

### 12.1 GitHub Actions

Private repo, GitHub Free: **2,000 minutes/month**.

| Workflow | Runs/month | Min/run | Total |
|---|---|---|---|
| Daily | 22 | 3.0 | 66 |
| Rebalance check (early exit) | 21 | 0.5 | 11 |
| Rebalance (actual) | 1 | 3.0 | 3 |
| Watchdog | 30 | 1.0 | 30 |
| Manual (mark-executed etc.) | ~4 | 1.5 | 6 |
| **Total** | | | **≈116 min (5.8%)** |

Comfortable. The archive cache is the reason: without it, a 720 MB download would add ~2
min/run and roughly double consumption.

**Storage:** Actions cache 10 GB free — using ~0.9 GB (archive + panel). Release assets do
not count against it.

### 12.2 MongoDB Atlas

**M0 free tier: 512 MB, shared vCPU, no automated backup.**

| Collection | Growth/year | 5-year |
|---|---|---|
| `portfolio_snapshots` (3 portfolios) | ~3 MB | 15 MB |
| `audit_logs` (financial, no TTL) | ~8 MB | 40 MB |
| `recommendations` | ~0.5 MB | 2.5 MB |
| `job_runs` (365-day TTL) | ~2 MB steady | 2 MB |
| `notifications` (180-day TTL) | ~1 MB steady | 1 MB |
| Others | negligible | ~1 MB |
| **Total** | **~14 MB/yr** | **≈62 MB (12% of M0)** |

**M0 fits for well over a decade.** Two caveats: M0 clusters are **paused after 60 days of
inactivity** (daily writes prevent this), and M0 has **no automated backups** — hence §11.1's
weekly `mongodump` to a Release asset.

Upgrade trigger: 400 MB, or if shared-vCPU latency becomes a problem. M10 is ~$57/mo — do
not pre-buy it.

### 12.3 Telegram

**₹0.** Bot API is free; limits (30 msg/s, 20/min per group) are orders of magnitude above
~2 messages/day.

### 12.4 Storage growth

| Store | Year 1 | Year 5 |
|---|---|---|
| Git — session CSVs (append-only) | 45 MB | 225 MB |
| Git — code and history | ~15 MB | ~40 MB |
| Release asset — frozen archive | 720 MB (once) | 720 MB |
| Release assets — Mongo backups (12-week window) | ~60 MB steady | ~60 MB |
| Actions cache | ~900 MB (evicting) | ~900 MB |

Git repo reaches ~265 MB by year 5 — well inside GitHub's 1 GB recommendation and 5 GB
limit. Should the session CSVs ever become a problem, roll them into the frozen archive
annually and start a fresh forward tier.

### 12.5 Cost by AUM

| AUM | Infra | GH Actions | Atlas | Telegram | **Total/month** |
|---|---|---|---|---|---|
| ₹2 L | unchanged | ₹0 | ₹0 | ₹0 | **₹0** |
| ₹10 L | unchanged | ₹0 | ₹0 | ₹0 | **₹0** |
| ₹50 L | unchanged | ₹0 | ₹0 (M0 still fits) | ₹0 | **₹0** |

**Infrastructure cost is flat in AUM and effectively zero.** The system stores 30 positions
and ~12 recommendation sets a year whether those positions are worth ₹2 L or ₹50 L.

**What actually scales with AUM is not infrastructure — it is market impact.** §11o's P2
found ~4× headroom over the modelled 30 bps round-trip at ₹2 L, and §11i established the
edge is mid-cap concentrated (Sharpe 0.51 in the liquid 100 vs 0.94 in the liquid 300). A
1/30 position at ₹50 L is ~₹1.7 L in a name that may trade ₹5 crore/day — approaching a
meaningful share of daily volume in the smaller names.

| AUM | Position size | Est. cost/round trip | Note |
|---|---|---|---|
| ₹2 L | ₹6,700 | ~30 bps (modelled) | Flat DP charge dominates on small trades |
| ₹10 L | ₹33,000 | ~30 bps | Sweet spot: DP charge amortised, impact still trivial |
| ₹50 L | ₹1,67,000 | 40–70 bps (**unmeasured**) | Impact in the thinner half of the liquid 300 |

The ₹50 L row is an estimate the backtest cannot validate, which is precisely why
`shadow_momentum_v1_001` and the realised-slippage tracking in §8.4 exist: they measure the
real number as AUM grows, so the scaling limit is discovered from data rather than assumed.

**Budget the real costs instead:** brokerage and taxes. At ₹10 L with ~21% annual turnover
and 20% STCG, charges plus tax dwarf every infrastructure line by three orders of magnitude.

---

## Appendix A — Build order

Each phase ends with something that works and is worth keeping.

| Phase | Deliverable | Gate to proceed |
|---|---|---|
| **1** | `domain/` + `repositories/` + Atlas schemas and indexes | Unit tests green; indexes verified against a real M0 |
| **2** | `jobs/ingest_session.py` + `daily.yml` (ingest and validate only) | 10 consecutive sessions ingested unattended |
| **3** | `services/tracking.py` + snapshots + `paper_momentum_v1_001` | Snapshot ≠ replay reconstruction check passes |
| **4** | Telegram daily summary + heartbeat | Daily message arrives for 10 sessions |
| **5** | `rebalance.yml` + recommendation generation (paper, auto-fill) | One full month-end cycle unattended |
| **6** | `mark-executed.yml` + FIFO lots + slippage tracking | One manual execution recorded end-to-end |
| **7** | `shadow_momentum_v1_001` | Discretion cost reported in the monthly message |
| **8** | Watchdog + all §10 thresholds | A deliberately-induced failure alerts correctly |
| **9** | `publish_api.py` + static JSON | `/health` and `/portfolios` served |
| **10** | React dashboard | — |
| **11** | `live_momentum_v1_001` | **6+ months of paper, §11o's evidence gates met** |

**Phases 1–8 have no dashboard and no API, and are the entire production system.** The
dashboard is a convenience layered on top; Telegram is the operational interface. Building
in this order means that if you stop at phase 8, you still have a system that runs itself
for years.

## Appendix B — Open items

1. **Contribution mechanics.** ₹20 k/month — does it arrive as cash on a fixed day and sit
   until the monthly rebalance deploys it, or is it deployed on arrival? This design assumes
   the former (`contribution_day`, deployed at the next rebalance). It matters for cash drag.
2. **Corporate actions on held positions.** `io/actions.py` exists but the pipeline must
   adjust *live holdings* (a 1:2 split doubles your share count) — not just the historical
   panel. Needs a dedicated daily step before valuation.
3. **`nsefin` 1.0.0 re-validation** — PROJECT.md §12 item 3, still open, and corporate
   actions on live holdings now depend on it.
4. **Private GitHub Pages** requires GitHub Pro (~$4/mo) if the dashboard must stay private.
   Decide before phase 9.
5. **Atlas M0 backup** — the weekly `mongodump` in §11.1 is required, not optional. It is
   the only backup that will exist.
