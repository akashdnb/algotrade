# Deployment — GitHub Actions

How the system runs unattended, what to do when it doesn't, and how to run the
10-session acceptance test.

**Division of responsibility:** MongoDB Atlas is the source of truth. GitHub
Actions is *only compute* — it holds no state, and every workflow can be
re-run from scratch without losing anything.

---

## 1. Secrets and variables

GitHub → Settings → Environments → **`paper`**.

| Secret | Example | Notes |
|---|---|---|
| `MONGODB_URI` | `mongodb+srv://user:pw@cluster0.xxx.mongodb.net/` | Atlas Network Access must allow `0.0.0.0/0` — runner IPs are not stable |
| `MONGODB_DATABASE` | `algotrade_paper` | Validation **refuses** a mismatch with `ENVIRONMENT` |
| `TELEGRAM_BOT_TOKEN` | `8123…:AAF…` | BotFather |
| `TELEGRAM_CHAT_ID` | `-1001234567890` | Group ids are negative |
| `HEALTHCHECKS_PING_URL` | `https://hc-ping.com/uuid` | Base URL only; `/start` and `/fail` are derived |

| Variable | Value |
|---|---|
| `DEFAULT_PORTFOLIO_ID` | `paper_momentum_v1_001` |

`ENVIRONMENT` is set in each workflow, not stored as a secret — it must be
visible in the YAML so you can see which database a workflow targets by reading
it.

> **Environment protection.** Add a required reviewer to a `prod` environment
> before any live portfolio exists. Paper needs none.

---

## 2. Workflows

| Workflow | Schedule (UTC) | IST | What it does |
|---|---|---|---|
| `daily.yml` | `30 14 * * 1-5` | 20:00 | ingest → signals → value → snapshot → reconcile → report |
| `rebalance.yml` | `0 15 * * 1-5` | 20:30 | **checks** whether a rebalance is due; usually exits in seconds |
| `healthcheck.yml` | `0 4 * * *` | 09:30 | watchdog: staleness, gaps, stuck runs, storage |
| `record-execution.yml` | manual | — | record a fill, rebuild, revalue, reconcile |

### Why `rebalance.yml` runs daily

A once-a-month cron fires on a calendar day that may not be a trading day, and
if that single firing is dropped — which GitHub documents as possible — the
month is silently missed with no second chance. The *check* runs every weekday
and exits unless the period's last session has passed and no completed run
exists. Cost is ~20 seconds a day; in exchange a missed rebalance repairs
itself within 24 hours.

### Why every job is reconciliation-driven

No step asks "did the cron fire?" Each asks **"what is missing?"**:

- `ingest_session` → weekdays with no session file
- `generate_signals` → sessions with data but no stored ranking
- `value_portfolio` → dates with no snapshot
- `generate_recommendations` → periods that closed with no completed run

Nothing remembers that yesterday failed. Today simply notices the gap.

---

## 3. Recovery

### A run failed

Check the Telegram alert — it states the blast radius and whether it
self-heals. Most ingestion and signal failures do:

```
tomorrow's run backfills automatically.
Act now only if this repeats 2 days running.
```

To repair immediately rather than waiting:

```bash
gh workflow run daily.yml
```

### Several sessions were missed

One run repairs all of them — the gap is the unit of work:

```bash
gh workflow run daily.yml -f through=2026-10-05 -f backfill=15
```

Locally:

```bash
python -m jobs.ingest_session --through 2026-10-05
python -m jobs.generate_signals --rebuild-panel --backfill 15
python -m jobs.value_portfolio --rebuild-holdings --backfill 15
```

### The schedule stopped firing entirely

GitHub disables scheduled workflows after **60 days of repository inactivity**.
The daily data commit normally prevents this. If it happens: push any commit,
then re-enable the workflow in the Actions tab.

The healthchecks.io alert is what tells you — nothing in-system can, because
nothing in-system is running.

### A rebalance was missed

It self-heals: the daily check notices the period closed with no completed run.
To force it:

```bash
gh workflow run rebalance.yml -f portfolio_id=paper_momentum_v1_001
```

If a **whole month** was missed, do **not** generate the stale period. Generate
the current one — the buffered strategy reads the book it actually has and
moves toward the current target. Executing a month-old signal is acting on
information the strategy did not produce.

### Reconciliation failed

**Stop. Do not trade on the reported NAV.** A book that disagrees with its
trade record makes every downstream valuation wrong.

```bash
python -m jobs.value_portfolio --reconcile-only
```

| Failure | Meaning | Fix |
|---|---|---|
| `holdings` | Stored book ≠ execution history | `python -m jobs.value_portfolio --rebuild-holdings` |
| `cash` | `portfolios.cash` ≠ ledger sum | The ledger is authoritative; the daily run refreshes the cache from it |
| `cash.ledger` | An execution moved no cash, or vice versa | Investigate — this should be impossible; both are written together |
| `rollups` | Item rollup ≠ its executions | Re-recording any fill for that set recomputes the rollup from truth |
| `snapshot` | NAV ≠ its own components | Re-run valuation for that date |

### A fill was recorded wrongly

Executions are **append-only** — there is no edit. Record a compensating fill
in the opposite direction with a note, so both remain in the audit trail. The
history stays truthful about what was believed and when.

### Duplicate execution

Cannot happen with a `broker_order_id`: it is a unique index, and the service
checks it *before* the over-fill validation, so re-dispatching the same fill is
a clean no-op. Paper fills without an order id are each distinct by design.

---

## 4. healthchecks.io

Create one check:

| Setting | Value | Why |
|---|---|---|
| Period | **1 day** | The daily run is the heartbeat |
| Grace | **6 hours** | Alerts ~26 h after the last success — long enough that one delayed run does not page you |
| Schedule | Simple | Cron mode would also alert on weekends |

Copy the ping URL into `HEALTHCHECKS_PING_URL`. The workflow pings `/start` at
the beginning, the base URL on success, and `/fail` on failure — so a failure
is immediate rather than waiting 26 hours for a timeout.

**This is the only monitor not hosted on the infrastructure it watches.** If
Actions is down, the repo is disabled, or the workflow is deleted, nothing
in-system runs, so nothing in-system can tell you. This can.

---

## 5. Telegram

1. `@BotFather` → `/newbot` → copy the token
2. Add the bot to your channel **as an admin that can post**
3. Get the chat id: `https://api.telegram.org/bot<TOKEN>/getUpdates`

Verify without posting:

```bash
python -m algotrade.check_config
```

`getChat` proves reachability; it does **not** prove the bot can post. To
confirm posting, run the daily report once by hand:

```bash
python -m jobs.daily_report --dry-run   # print only
python -m jobs.daily_report             # actually send
```

---

## 6. The 10-session acceptance test

### Procedure

1. Confirm a green baseline:
   ```bash
   python -m algotrade.check_config
   python -m algotrade.acceptance.status
   ```
2. Enable the schedules (they are on by default once merged).
3. **Do not intervene.** The point is unattended operation.
4. Each morning:
   ```bash
   python -m algotrade.acceptance.status
   ```
5. Around session 5, **inject a failure** (see below).
6. At session 10, all checks must pass.

### Criteria

| # | Criterion | How it is verified |
|---|---|---|
| 1 | 10 sessions valued, no manual action | `sessions 10 / 10` |
| 2 | Every session ingested and committed | `git log --oneline data/sessions` |
| 3 | Holidays recorded as holidays, not failures | no `failed` job runs on NSE holidays |
| 4 | Heartbeat pinged every success | healthchecks.io shows no "late" events |
| 5 | Watchdog green each morning | 10 runs, 0 critical |
| 6 | **A failure was injected and self-healed** | `failure recovery  PASS` |
| 7 | No duplicate documents | `reconciliation  PASS` |
| 8 | Actions usage on track | <20 min/week |
| 9 | Mongo storage flat | <5 MB (market data must not be in there) |
| 10 | No credential in any log | `gh run view --log \| grep -i token` → nothing |

### Criterion 6 is the one that matters

Ten quiet sessions prove the happy path. **Deliberately breaking one and
watching it recover unaided is the only evidence for the property being
claimed.**

```bash
# 1. break it: temporarily set MONGODB_URI to an invalid value in the
#    `paper` environment, or delete a session file and push
# 2. wait for the scheduled run -> expect a Telegram failure alert
# 3. restore the secret
# 4. wait for the next run -> it should backfill with no help
python -m algotrade.acceptance.status   # failure recovery -> PASS
```

`acceptance.status` detects this by looking for a job that failed for a
business date and later succeeded for the same date — which is precisely the
self-healing property, not a proxy for it.

---

## 7. Cost

Private repo, GitHub Free: **2,000 minutes/month**.

| Workflow | Runs/month | Min/run | Total |
|---|---|---|---|
| Daily | 22 | ~5 | 110 |
| Rebalance check (early exit) | 21 | 0.5 | 11 |
| Rebalance (actual) | 1 | 3 | 3 |
| Watchdog | 30 | 1 | 30 |
| Manual | ~10 | 2 | 20 |
| **Total** | | | **≈174 min (9%)** |

Atlas M0 and Telegram are free. Infrastructure cost is **flat in AUM** — the
system stores 30 positions whether they are worth ₹2 L or ₹50 L. What scales is
market impact, not compute.
