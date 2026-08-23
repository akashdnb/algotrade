# Phase 2 — Daily Ingestion & GitHub Actions Deployment

**Status:** DEPLOYED 2026-08-23 — first workflow run green on GitHub Actions
**Depends on:** Phase 1 (domain + repositories + Atlas schema, 611 tests)
**Scope:** Market data reaching disk unattended, every trading day, with the
system able to tell you when it doesn't. No signal generation, no
recommendations, no valuation — those are Phase 3.

---

## 1. What Phase 2 actually delivers

| Delivered | Deferred |
|---|---|
| Reconciliation-driven daily ingestion | Signal generation (`signals` collection stays empty) |
| `daily.yml` running unattended | Recommendation generation |
| Config, validation, `check_config` | Portfolio valuation / snapshots |
| Telegram client + failure alerts | Daily summary message (needs valuation) |
| healthchecks.io dead-man switch | `rebalance.yml` schedule (**deliberately off**) |
| `watchdog.py` + `healthcheck.yml` | Manual execution recording |

**`rebalance.yml` exists but its schedule is commented out.** `jobs/run_rebalance.py`
is Phase 3. A scheduled workflow calling a missing module fails every night, and
an alert that always fires is an alert you learn to ignore — which is precisely
the reflex this system cannot afford. It is `workflow_dispatch`-only until the
job lands.

---

## 2. The reconciliation model

The single design decision everything else follows from.

```
                    NOT THIS                              THIS
        ┌───────────────────────────┐      ┌──────────────────────────────────┐
        │ cron fired?               │      │ last stored session = 2026-09-29 │
        │   └─> fetch today         │      │ target date         = 2026-10-02 │
        │                           │      │ gap = [09-30, 10-01, 10-02]      │
        │ cron dropped -> day lost  │      │   └─> fetch all three            │
        └───────────────────────────┘      └──────────────────────────────────┘
```

`IngestionService.missing_sessions()` is the whole mechanism:

1. Newest session on disk → start = the day after it.
2. Weekdays from start through the target date.
3. Minus anything already on disk (sessions dir **or** frozen archive).

Consequences, all tested:

- A dropped Tuesday is repaired by Wednesday's run. Nothing remembers Tuesday failed.
- Running twice is a no-op (`test_running_twice_is_idempotent`).
- An empty store is bounded by `lookback_days`, so a first run doesn't try to fetch from 2010.
- A future-dated file (clock skew, hand-edited name) yields an empty range rather than crashing.

**Holidays are discovered, not predicted.** NSE's archive returns **404** for a
non-session. That is a direct statement of fact, unlike `nsefin`'s holiday
calendar which fails silently and reported `False` for Independence Day
(PROJECT.md §10.9). A 404 → `status: holiday`, not an error. No file is written,
so the next run asks again — one wasted request, versus a wrong "holiday"
becoming permanent.

---

## 3. Market data flow

```
  NSE archive (nsearchives.nseindia.com)
        │  io/nse.fetch_csv — handles CSV-that-is-an-XLSX-in-a-ZIP, referer, retries
        ▼
  data/sessions/YYYY/sec_bhavdata_full_DDMMYYYY.csv     ← COMMITTED, append-only
        │                                                  immutable once written
        │  ┌── data/archive/  ← frozen 720 MB, GitHub Release asset, Actions cache
        ▼  ▼
  build_panel → data/curated/  ← CACHE, never committed, rebuilt in ~21s
        │
        ▼
  (Phase 3) signal → recommendations → MongoDB
```

**Three tiers by mutation pattern**, because that is what determines where data belongs:

| Tier | Mutation | Storage | Why |
|---|---|---|---|
| Frozen archive | Never | Release asset + `actions/cache` | Immutable → permanent cache hit, no repo bloat |
| Forward sessions | Append-only | **Git** | Git-friendly, is the audit trail, and the daily commit keeps the repo active |
| Curated panel | Rewritten every build | `actions/cache` | Committing it would add ~21 MB of git objects *per day* |

**Production needs a few hundred sessions, not 16 years.** The archive exists
for backtesting, which is finished.

**Deployment outcome (2026-08-23): the frozen-archive tier was dropped
entirely.** Seeding 500 sessions (170 MB) into git covers the requirement with
margin and removes a whole failure class — Release download, cache miss,
asset expiry. `daily.yml` still *attempts* the restore and warns benignly when
no Release exists, so the tier can be reinstated without a code change if the
repository ever gets uncomfortably large.

**How much history is actually needed is not 273.** The hard floor for
*computing* the signal is 273 (momentum) + 20 (universe window) = 293. But
computing it is not computing it *correctly*: corporate-action adjustment
reconciles against the session grid it is given, so near a short window's edge
an action has incomplete context and `drop_windows` removes different rows.
Measured against the research pipeline, **320 sessions produced a top-30
differing by one name; 340 and above matched exactly.** `MIN_SESSIONS` is 400 —
the boundary plus a quarter of adjustment warm-up, chosen for margin rather
than fitted, because the boundary moves with whichever actions sit near the
edge. `tests/integration/test_signal_parity.py` is what holds this honest.

---

## 4. MongoDB usage in daily ingestion

**Market data never enters MongoDB.** Ingestion touches exactly one collection:

```javascript
// job_runs — the reconciliation ledger
{ _id: "ingest_session:2026-09-30:1",
  job_name: "ingest_session", business_date: "2026-09-30", attempt: 1,
  status: "success",
  result: { considered: 3, fetched: 2, holidays: 1, failed: 0 },
  github_run_id: "...", code_commit: "...",
  expires_at: <+365d> }
```

The unique partial index on `(job_name, business_date) WHERE status="success"`
means a duplicated workflow run **cannot** record a second success — enforced by
the database, not by an application check that could race.

`claim()` raising `JobAlreadySucceeded` is *not* treated as fatal: a later date
may still be missing, so the job logs it and continues repairing older gaps.

---

## 5. Configuration and safety

```
process environment  →  .env (dev only)  →  defaults
      (CI Secrets)        (gitignored)
```

Process environment wins so a stale local `.env` can never override CI. The
`.env` parser is ~20 lines rather than a dependency — no interpolation, so a
config file cannot evaluate anything.

**`validate()` reports every problem, then fails.** Setting up a new environment
is when this runs most, and fixing one variable per attempt is a bad loop.
Severity is environment-dependent:

| Variable | dev | paper / prod |
|---|---|---|
| `MONGODB_URI` | error | error |
| `TELEGRAM_*` | info | **error** — without it the system runs but cannot tell you anything |
| `HEALTHCHECKS_PING_URL` | info | **error** — without it, total failure is silent |
| `BROKER_*` | info | info — nothing places orders yet |
| `DEFAULT_PORTFOLIO_ID` | info | error |

**Two hard guards**, checked at startup *and* at the point of action (a job can
be handed a portfolio id on the command line that startup never saw):

1. `guard_portfolio` — a `live_` portfolio outside `ENVIRONMENT=prod` is refused.
2. `_check_mongo` — one environment pointed at another's database is refused
   (`algotrade_dev` / `algotrade_paper` / `algotrade_prod` are separate databases,
   not a shared one with an `env` field).

---

## 6. Telegram flow

```
event ──► render_*()  ──► NotificationService.send()
                              │
                              ├─ repo.claim(_id = kind:portfolio:date)
                              │     └─ already exists? → return None, send nothing
                              ├─ TelegramClient.send()  (MarkdownV2, auto-split at 4096)
                              └─ mark_sent / mark_failed   ← never raises
```

- **Dedupe before the network call**, so a re-run cannot post a second summary.
- **Failure alerts key on content hash**, so a *different* failure the same day still sends. Deduping those by date alone would suppress the second, unseen problem.
- **`send` never raises.** A broken alert channel must not fail a run that otherwise succeeded — but it is always recorded as failed, because a silent delivery failure is invisible by definition (§10.5), and the watchdog polls for it.

Phase 2 sends only failure alerts. The daily summary needs valuation (Phase 3).
Per the review decision, that summary will carry **status and drift only — no
trade list**: the strategy rebalances monthly, the rank buffer exists so drift
is *not* traded, and a daily buy/sell list is a machine for tempting you to
overtrade against a cost case that assumed ~21% annual turnover.

---

## 7. Monitoring

Three layers, because each covers the previous one's blind spot:

| Layer | Catches | Blind to |
|---|---|---|
| `if: failure()` in each workflow | A run that fails | A run that never starts |
| `healthcheck.yml` → `jobs/watchdog.py` | Gaps, stale data, stuck runs, failed sends, schema drift, storage | Its own failure |
| **healthchecks.io** (external) | Everything above being dead | — |

The external switch is the only monitor not hosted on the infrastructure it
watches. Configure **period 1 day, grace 6 hours** → alerts ~26 h after the last
success, long enough that one delayed run doesn't page you.

Thresholds (`jobs/watchdog.py`, design §10.1):

| Signal | Warn | Critical |
|---|---|---|
| Market data stale | 1 trading day | **2** — blocks valuation |
| Daily job failed | 1 consecutive | **2 consecutive** |
| Recommendation unexecuted | 2 days | **3 days** |
| Notification failures /24h | 1 | **3** |
| Mongo storage | 400 MB | **480 MB** (M0 = 512) |
| Stuck `running` job | — | **6 h** |

---

## 8. Files created

```
algotrade/
  config/
    settings.py           typed Settings, .env parsing, redaction
    validation.py         fail-fast findings + guard_portfolio / guard_write
  check_config.py         python -m algotrade.check_config
  platform/
    clock.py              IST business dates, injectable
    telegram.py           Bot API, MarkdownV2 escaping, 4096 splitting
    health.py             healthchecks.io dead-man switch
  services/
    ingestion.py          reconciliation-driven fetching
    notification.py       templates + dedupe/send/record
jobs/
  ingest_session.py       daily fetch CLI
  watchdog.py             all §10.1 thresholds
  init_db.py              schema apply/verify
.github/workflows/
  daily.yml               14:30 UTC Mon–Fri (20:00 IST)
  rebalance.yml           workflow_dispatch only until Phase 3
  healthcheck.yml         04:00 UTC daily (09:30 IST)
.env.example
config/strategy/momentum_v1.yaml    FROZEN, content-addressed
```

---

## 9. Deployment sequence

Each step is verifiable before the next.

| # | Step | Verify |
|---|---|---|
| 1 | Atlas M0: cluster, DB user (`readWrite` on `algotrade_paper` only), Network Access `0.0.0.0/0` | Atlas UI |
| 2 | `cp .env.example .env`, fill in, `python -m algotrade.check_config` | exit 0 |
| 3 | `python -m jobs.init_db` against Atlas | "10 collections match schema.py" |
| 4 | `pytest tests/integration` against Atlas | **DONE** — 37 passed in 3m35s |
| 5 | Telegram: BotFather → bot → add to channel as admin → chat id | `check_config` shows `@yourbot` |
| 6 | healthchecks.io check: period 1d, grace 6h | `check_config` reports reachable |
| 7 | Seed sessions: `python -m jobs.ingest_session --bootstrap --days 730`, commit | ~500 files in `data/sessions/` |
| 8 | ~~Upload frozen archive as a Release~~ | **SKIPPED — see below** |
| 9 | GitHub: Environment `paper` + Secrets + var `DEFAULT_PORTFOLIO_ID` | Settings UI |
| 10 | `gh workflow run daily.yml -f through=<yesterday>` | green; heartbeat pings |
| 11 | Enable the schedule; watch 10 sessions | §11 below |

**Secrets** (Environment `paper`): `MONGODB_URI`, `MONGODB_DATABASE`,
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `HEALTHCHECKS_PING_URL`.
**Variable**: `DEFAULT_PORTFOLIO_ID`.

---

## 10. Risks before deployment

Ordered by expected cost, not likelihood.

| # | Risk | Why it matters | Mitigation | Residual |
|---|---|---|---|---|
| **R1** | ~~Atlas M0 untested~~ | **CLOSED 2026-08-23.** 37/37 integration tests pass against a real M0. It found a genuine bug: Atlas's built-in `readWrite` role excludes `collMod`, so `apply_schema` succeeded on a fresh database and failed on every re-run. Docker never showed it (root user). `_update_validator` now degrades gracefully when the installed validator already matches, and names the missing privilege when it does not. | Fixed in `repositories/client.py` | Closed |
| **R2** | **Atlas requires `0.0.0.0/0`** | GitHub runner IPs are not stable, so the database is reachable from anywhere with the credential | Single-purpose user, `readWrite` on one DB, long random password, rotate on any suspicion | Accepted; the alternative is a self-hosted runner |
| **R3** | ~~M0 has no automated backups~~ | **CLOSED 2026-08-23.** `weekly-backup.yml` exports all 11 collections to gzipped JSON, verifies against the live database, and uploads to an Actions artifact plus a permanent Release. **The restore was rehearsed**, not merely implemented: a full restore into a scratch Atlas database reconciled clean with identical NAV, cash and holdings. | Closed |
| **R4** | Repo goes inactive → schedules disabled | System stops silently | Daily commit keeps it active; `healthcheck.yml` runs Saturdays too; healthchecks.io catches the rest | Low |
| **R5** | NSE changes the archive URL or format | No data | `io/nse` sniffs content rather than trusting the extension; failures alert within 2 sessions | Medium — external dependency |
| **R6** | Secrets leak via logs | Credential exposure | `redacted()` on every log path; tested that passwords never appear | Low |
| **R7** | Git repo growth | 45 MB/yr forward + 90 MB seed → ~265 MB at 5 years | Inside GitHub's 1 GB guidance; roll into the archive annually if needed | Low |
| **R8** | Cache eviction (7-day idle, 10 GB cap) | Slow rebuild, not data loss | `restore-keys` prefix fallback; archive re-downloadable from the Release | Low |
| **R9** | Actions minutes | Private repo = 2,000/mo; usage ~150 | Watchdog frequency is the lever | Very low |
| **R10** | Clock/timezone error | Wrong business date | IST is explicit in `clock.py`, tested at the 18:30 boundary | Low |

---

## 11. Acceptance criteria

### 11a. Ten consecutive unattended trading sessions

The gate for calling Phase 2 done. **Zero manual intervention** across 10
consecutive NSE trading sessions.

| # | Criterion | How to verify |
|---|---|---|
| 1 | 10/10 sessions ingested, no human action | `db.job_runs.find({job_name:"ingest_session", status:"success"}).count() >= 10` |
| 2 | Every session file committed | `git log --oneline data/sessions \| wc -l` |
| 3 | Holidays recorded as holidays, not failures | `result.holidays` non-empty on any NSE holiday in the window |
| 4 | Heartbeat pinged on every success | healthchecks.io shows no "late" events |
| 5 | Watchdog green each morning | 10 runs, 0 critical |
| 6 | **≥1 injected failure detected and self-healed** | Revoke a secret for one run → alert fires; restore → next run backfills without help |
| 7 | No duplicate documents | `job_runs` count == distinct `(job_name, business_date)` count |
| 8 | Actions usage on track | <20 min/week |
| 9 | Mongo storage flat | <1 MB after 10 sessions (market data must not be in there) |
| 10 | No credential in any log | `gh run view --log \| grep -i` password/token → nothing |

**Criterion 6 is the one that matters.** Ten quiet sessions prove the happy
path. Deliberately breaking one and watching it recover unaided is the only
evidence for the property actually being claimed — that it survives failure
without you.

### 11b. Production readiness (gate to Phase 3)

- [ ] All of §11a passed
- [x] R1 closed: integration tests green against Atlas M0 (2026-08-23)
- [x] R3 closed: weekly backup + **restore rehearsed** (2026-08-23)
- [ ] `check_config` exits 0 in the `paper` environment with zero warnings
- [ ] Telegram alert received and *acted on* at least once
- [ ] A recovery rehearsed: delete 3 sessions, run `daily.yml`, confirm repair
- [ ] Restore rehearsed: drop a scratch DB, restore from dump, `init_db --verify` clean
- [ ] Runbook written: what to do for each watchdog critical

---

### 11c. Atlas findings (recorded 2026-08-23)

Discovered by running the gate rather than by reasoning about it.

| Finding | Consequence | Handling |
|---|---|---|
| `readWrite` excludes **`collMod`** | First `init_db` works, every later one fails | `_update_validator` checks whether the change was needed; only raises if the validator genuinely differs, and then names the privilege |
| `readWrite` excludes **`dropDatabase`** | A per-test throwaway database cannot be torn down | Integration suite uses one database, clearing documents between tests |
| Atlas users are **scoped per database** | A new database name is inaccessible without a fresh grant | Tests default to a scratch database; `algotrade_dev` allowed, paper/prod refused |
| Cold-start latency **~2.4 s**, full suite 3m35s | Slow but workable | Session-scoped schema setup, not per test |

**To update a validator later** you will need `dbAdmin` on that database
(Atlas → Database Access → Edit user → add `dbAdmin`, scoped). Until then the
schema can be created but not altered — acceptable while it is stable, and it
fails loudly rather than silently if it ever needs to change.

---

## 11d. What deployment actually found (2026-08-23)

Three defects, none of which local testing could have surfaced, because all
three were about the difference between a developer machine and a runner.

| # | Defect | Why local testing missed it |
|---|---|---|
| 1 | `archive.discover` globbed **non-recursively** | Ingestion writes to `data/sessions/YYYY/`; the flat research mirror hid the partitioned layout entirely. CI would have built an **empty panel every run**. |
| 2 | The first seed copied from the mirror alone, missing **43 real sessions** that exist only in `data/raw/backfill` | The backfill directory exists *precisely because* the mirror drops sessions (§10.8). The CI panel came out sparser and produced a **different top-30** — a silent signal-parity break. |
| 3 | Slicing the panel to exactly `MIN_SESSIONS` broke parity | Introduced while fixing #2. Caught by the parity test, which has now earned its keep twice. |

The lesson is the same each time: **simulate the target environment rather than
reasoning about it.** Every one of these was found by building the panel from
`data/sessions/` alone and comparing the result to the research pipeline —
not by reading code.

---

## 12. Open items carried into Phase 3

1. **`jobs/run_rebalance.py`** — the schedule in `rebalance.yml` stays commented out until it exists.
2. ~~Weekly backup~~ — **done.** `weekly-backup.yml`, JSON rather than `mongodump` so the file stays readable without matching tooling.
3. **Corporate actions on live holdings** — `io/actions.py` adjusts the historical panel, but a 1:2 split doubling your actual share count is a separate daily step. Depends on the still-open `nsefin` 1.0.0 re-validation (PROJECT.md §12 item 3).
4. **Panel rebuild in CI** — `build_panel.py` runs locally; the daily workflow does not yet invoke it (nothing consumes the panel until Phase 3).
5. **Daily summary message** — needs valuation. Status + drift, no trade list.
