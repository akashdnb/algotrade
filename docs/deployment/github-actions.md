# Deployment — GitHub Actions

How the system runs unattended, what to do when it doesn't, and how to run the
10-session acceptance test.

> **Status: DEPLOYED 2026-08-23.** The `paper` environment exists with all five
> secrets, 500 sessions are committed, and the first `daily.yml` run completed
> green in 1m09s against Atlas from a GitHub runner. Steps 1–3 below are done;
> they are kept as the record of what was configured and what to redo on a new
> machine or a fresh clone.

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
| `daily.yml` | `30 14 * * 1-5` **+ backup 16:30** | 20:00 | ingest → signals → value → snapshot → reconcile → report |
| `rebalance.yml` | `0 15 * * 1-5` | 20:30 | **checks** whether a rebalance is due; usually exits in seconds |
| `healthcheck.yml` | `0 4 * * *` | 09:30 | watchdog: staleness, gaps, stuck runs, storage |
| `weekly-backup.yml` | `0 3 * * 0` | 08:30 Sun | export, verify, upload to artifact + Release |
| `record-execution.yml` | manual | — | record a fill, rebuild, revalue, reconcile |

### Why `rebalance.yml` runs daily

A once-a-month cron fires on a calendar day that may not be a trading day, and
if that single firing is dropped — which GitHub documents as possible — the
month is silently missed with no second chance. The *check* runs every weekday
and exits unless the period's last session has passed and no completed run
exists. Cost is ~20 seconds a day; in exchange a missed rebalance repairs
itself within 24 hours.

### The backup trigger, and why one was needed

**GitHub's `schedule:` is best-effort, and in practice it has been very late:**

| Workflow | Scheduled | 24–26 Aug | 27 Aug onward |
|---|---|---|---|
| `daily.yml` | 14:30 | +39 m, +43 m, +88 m | **+9h21, +9h06, +5h44, +3h24** |
| `healthcheck.yml` | 04:00 | +38 m, +32 m, +32 m | **+10h54, +11h49, +6h38** |
| `weekly-backup.yml` | 03:00 Sun | — | **+5h51** |

A 20:00 IST report arriving at 05:00 IST is not useful, and the delay caused a
real correctness bug: runs crossing IST midnight claimed the wrong business
date until `clock.session_date` was written.

So `daily.yml` also accepts a `repository_dispatch` of type **`run-daily`**,
fired at **16:30 UTC** — two hours after its own schedule — by a Vercel cron in
the `linkedin-job-search` service, which is already deployed and already holds
a GitHub token. Vercel documents its Hobby cron precision as ±59 minutes,
against GitHub's unbounded delay.

**`schedule:` remains primary.** The dispatch is a second chance, not a
replacement: if GitHub was merely slow, the backup run is a clean no-op.

#### Why a duplicate run is safe

Verified live on 2026-09-01, not merely asserted. A dispatch fired 95 minutes
after that day's scheduled run had already completed:

```
Business date (session this run is for): 2026-09-01
nothing to do -- already current
signals already stored for 2026-08-26..2026-09-01; nothing to do
```

Afterwards: one snapshot, one signal, three `job_runs` rows — all from the
scheduled run, none from the dispatch. **Four** independent guards each refused
the work:

| Guard | What it refused |
|---|---|
| Ingestion gap query | no missing session — *"nothing to do, already current"* |
| Stored-signal check | *"signals already stored for 2026-08-26..2026-09-01"* |
| `JobAlreadySucceeded` | the unique index on `(job_name, business_date)` where status is success |
| Notification dedupe | *"notification daily_summary already sent for 2026-09-01"* |

The `concurrency: group: daily` setting means the two cannot even run at the
same time.

#### Why the dispatch records where it came from

`verify_recovery` counts a repair as self-healing only when no human was
involved, which it used to approximate as `trigger == "schedule"`. A
`repository_dispatch` is indistinguishable between the backup cron and someone
calling the endpoint by hand, so the dispatcher stamps the difference:
`vercel-cron` only for requests carrying Vercel's own cron headers
(`x-vercel-cron-schedule`, a `vercel-cron/` user agent), `manual` otherwise.
Jobs record it as `trigger_source`, and only the cron stamp counts as unaided.

To fire it by hand — which will correctly report `manual`:

```bash
curl -s -H "Authorization: Bearer $CRON_SECRET" \
  "https://<job-search>.vercel.app/api/index?action=trigger-algotrade"
# {"status": "dispatched", "repo": "algotrade", "event": "run-daily", "source": "manual"}
```

#### No duplicate Telegram message

An earlier draft of this section claimed the report would arrive twice. It does
not, and the correction is worth keeping because the reasoning was wrong in a
specific way: deduplication is not per-run, it is **persisted**. A
`notifications` document keyed `daily_summary:{portfolio}:{date}` is claimed
*before* the network call, so a second run finds it already sent and never
posts. Confirmed 2026-09-01 — one row, one message, despite two runs.

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
| Schedule | **Cron** | Knows the next run after Friday is *Monday*, so it does not count the weekend against you |
| Cron | `30 14 * * 1-5` | The same expression `daily.yml` uses |
| Timezone | **UTC** | Match the workflow; the cron above is UTC, not IST |
| Grace | **12 hours** | Absorbs the 9–12 h scheduling delays GitHub has actually imposed |

> **Corrected 2026-08-30.** This section previously specified *period 1 day,
> grace 6 hours, schedule Simple*, and claimed cron mode "would also alert on
> weekends." **Both claims were wrong, and backwards.** Simple mode alerts 26 h
> after the last success; Friday→Monday is ~72 h, so the check went down every
> single weekend on a perfectly healthy system — observed Sun 2026-08-30
> 01:37 UTC, exactly 26 h after Friday's last ping. Cron mode is precisely
> what prevents that, because it waits for the next *scheduled* firing.
>
> **Applied 2026-08-30.** The check now runs in cron mode. The Saturday false
> "down" should not recur; the first weekend to confirm it is 09-05/09-06.

Copy the ping URL into `HEALTHCHECKS_PING_URL`. The workflow pings `/start` at
the beginning, the base URL on success, and `/fail` on failure — so a failure
is immediate rather than waiting 26 hours for a timeout.

**This is the only monitor not hosted on the infrastructure it watches.** If
Actions is down, the repo is disabled, or the workflow is deleted, nothing
in-system runs, so nothing in-system can tell you. This can.

**Only `daily.yml` owns this heartbeat.** It is the one workflow whose `/start`
ping is always followed by a matching `success`/`fail` ping. Every other
workflow that calls `check_config` must pass `--skip-healthchecks-ping`, or it
will register a `/start` with nothing to close it.

> **Bug, found and fixed 2026-08-27.** `healthcheck.yml`'s config-check step
> called `check_config` without that flag. `check_healthchecks()` pinged the
> shared `/start` endpoint every morning at 09:30 IST as a reachability probe,
> and `healthcheck.yml` never sent a matching completion ping — so the check
> was marked "down" a couple of hours later, every day, for a `daily.yml` run
> that was never actually late. Fixed by adding `--skip-healthchecks-ping` to
> `check_config.py` and passing it from `healthcheck.yml`. Verified live via a
> manual `workflow_dispatch` the same day: `healthchecks: ping skipped; not
> the heartbeat owner`. See `claude_fixes/2026-08-27-heartbeat-and-staleness-false-alarms.md`.

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

The authoritative, numbered criteria list — with the exact verification
command for each and what a PASS/WARN/FAIL verdict means — lives in
[`docs/acceptance/10-session-gate.md`](../acceptance/10-session-gate.md).
Don't duplicate it here; the two lists drifted out of sync once already
(this doc briefly had its own differently-numbered 10 items).

**The one that matters is self-healing** (criterion 5 in that doc). Ten quiet
sessions prove only the happy path. Deliberately breaking a run and watching
it recover unaided is the only evidence for the property actually being
claimed — see that doc's "Injecting the failure" section for the procedure,
and its honesty note: the 2026-08-23 rehearsal injected a ledger entry
directly rather than making a real job fail, so it did **not** satisfy the
gate.

**The real injection fired on 2026-09-01.** `ALGOTRADE_FAULT_INJECT` failed
`generate_signals` for business date 2026-08-31 on a *scheduled* run, and
attempt 2 succeeded unaided on another scheduled run — a failed/succeeded pair
for the same business date, which is what the criterion actually asks for. See
[`claude_fixes/2026-09-01-failure-injection-fired.md`](../../claude_fixes/2026-09-01-failure-injection-fired.md).

---

## 6b. Market data in CI

There is **no Release asset.** 500 sessions (170 MB) are committed to
`data/sessions/`, which covers `MIN_SESSIONS = 400` with margin and removes the
Release download, its cache, and its expiry as failure modes. `daily.yml` still
attempts a restore and logs a benign warning when none exists:

```
::warning::no archive release; relying on committed sessions
```

That warning is expected and is not a fault.

**Re-seeding, if you ever need to.** Seed from the mirror **union** the
backfill directory, never the mirror alone — the backfill exists precisely
because the mirror drops real sessions (§10.8), and seeding without it produced
a sparser panel and a **different top-30** than the research pipeline:

```python
union = index("data/raw/nse-data-bank/data") | index("data/raw/backfill")
seed  = sorted(union.items())[-500:]
```

Always confirm parity afterwards:

```bash
pytest tests/integration/test_signal_parity.py -q
```

---

## 7. Cost

Private repo, GitHub Free: **2,000 minutes/month**.

| Workflow | Runs/month | Min/run | Total |
|---|---|---|---|
| Daily | 22 | ~5 | 110 |
| Rebalance check (early exit) | 21 | 0.5 | 11 |
| Rebalance (actual) | 1 | 3 | 3 |
| Watchdog | 30 | 1 | 30 |
| Weekly backup | 4 | 2 | 8 |
| Manual | ~10 | 2 | 20 |
| **Total** | | | **≈182 min (9%)** |

Measured: the first real `daily.yml` run took **1m09s**, comfortably under the
5-minute estimate, because the committed sessions need no download.

Atlas M0 and Telegram are free. Infrastructure cost is **flat in AUM** — the
system stores 30 positions whether they are worth ₹2 L or ₹50 L. What scales is
market impact, not compute.
