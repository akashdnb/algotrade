# Runbook

Written for you in six months, having forgotten all of it.

> **Live since 2026-08-23.** `paper_momentum_v1_001` on Atlas
> `algotrade_paper`, driven by GitHub Actions. Nothing here needs doing daily
> except reading one Telegram message.

**Mental model.** Atlas holds the truth. GitHub Actions is a machine that wakes
up, asks *"what is missing?"*, fills it, and goes back to sleep. Nothing
remembers that yesterday failed — today just notices the gap.

---

## Daily (30 seconds)

Read the Telegram message. That is the whole routine.

```
Reconcile   PASS     <- the only line that must be true
Data        OK
Drawdown    -0.67%
```

**If `Reconcile FAIL`:** stop. The NAV above it means nothing. Go to
[disaster-recovery.md](disaster-recovery.md) §5.

**If no message arrived:** either the job did not run or Telegram failed.
healthchecks.io tells you which — it alerts only if the job itself stopped.

```bash
python -m algotrade.acceptance.status
```

---

## Weekly (2 minutes)

```bash
python -m algotrade.acceptance.status
python -m algotrade.acceptance.verify_recovery
```

Confirm the backup landed: GitHub → Releases → `backups`. A missing weekly
backup is not urgent today and is very urgent the day you need it.

```bash
gh release view backups --repo akashdnb/algotrade
```

---

## Monthly rebalance

You get a `REBALANCE REQUIRED` message once the month has closed — on the
**first trading session of the following month**, not the last one of the
month itself. A month cannot be known to be over while its latest session is
still the latest session there is.

> **This has never fired before 2026-09-02.** The check derived its period
> from the latest session, so it always asked about the month in progress and
> always answered "not due". The one rebalance this book has had was
> `triggered_by: manual`. Fixed; the first automatic firing will be the
> September rebalance, on the first October session.
> [`claude_fixes/2026-09-02-monthly-rebalance-never-fires.md`](../../claude_fixes/2026-09-02-monthly-rebalance-never-fires.md)
>
> **A month missed entirely is not gone back for.** The run catches up to the
> latest closed month and prints `MISSED 2026-09` rather than filing a later
> run under September's name. If you see that line, the gap is real and
> permanent — the book was never rebalanced for that month.

1. **Read it.** Sells first — T+1 settlement funds the buys.
2. **Place the orders at your broker.** Nothing here does that.
3. **Record each fill**, one dispatch per fill:

```bash
gh workflow run record-execution.yml \
  -f recommendation_id=paper_momentum_v1_001:2026-09:r1 \
  -f symbol=RELIANCE -f side=buy \
  -f quantity=300 -f price=2402.75 -f charges=49.05 \
  -f broker_order_id=251001000123456
```

Partial fills are normal — record each separately; they aggregate.

4. **Declining a trade needs a reason:**

```bash
python -m jobs.record_execution --set-id <id> \
  --fills '[{"seq": 5, "skip": "insufficient funds after T+1"}]'
```

The reasons are the dataset that measures whether your discretion helps or
hurts. §9 exists for this; skipping the reason discards the measurement.

5. **Confirm.** You get a ✅ per fill and reconciliation runs automatically.

> **Do not edit a recorded fill.** Executions are append-only. To correct one,
> record the opposite with a note. The history stays truthful about what was
> believed and when.

### The shadow book fills itself

`shadow_momentum_v1_001` gets the same recommendation and auto-fills it at the
reference price inside the rebalance workflow, before you have touched
anything. You get **no alert for it** and there is nothing to do.

That is the point: it is the version of your book where every instruction was
followed, so what you did instead is measurable. **Never record a fill against
it by hand** — not even to fix something. See
[shadow-portfolio.md](shadow-portfolio.md).

---

## Checking health

```bash
python -m algotrade.check_config       # connectivity + config
python -m jobs.value_portfolio --reconcile-only
cat data/runtime/system_health.json
```

| Score | Meaning |
|---|---|
| **GREEN** ≥90 | Act normally |
| **YELLOW** 60–89 | Look before you trade |
| **RED** <60 | The numbers cannot be trusted |

### Alerts that are not faults

Three false alarms were diagnosed and fixed in the first week. If you see one
of these shapes again, it is a regression, not a new incident:

| Symptom | Was |
|---|---|
| `[WARN] market_data: 1 trading day behind`, every morning | The watchdog runs 09:30 IST; that day's ingest is at 20:00 IST. It now measures against the last *completed* trading day |
| healthchecks.io down every Saturday | Simple mode alerts 26 h after the last success; Friday→Monday is ~72 h. Now cron mode |
| healthchecks.io down every morning, up each evening | `healthcheck.yml` pinged `/start` on a check it does not own. Only `daily.yml` owns the heartbeat; everything else passes `--skip-healthchecks-ping` |
| `[WARN] daily_job: 1 session without a success`, with a *weekend* date | A run delayed past IST midnight claimed the next calendar day. Fixed by `clock.session_date`, but the mislabelled rows from 08-27/28 stay (the ledger is append-only) and age out of the 10-day window around **2026-09-10** |

A genuinely failing job is distinguishable from all of the above by a `failed`
row in `job_runs` and a `🔴 Job failed` Telegram message. A warning with
neither is a measurement artefact.

### Forcing the daily run

Three ways, in increasing order of "counts as a human intervening":

```bash
# 1. The backup trigger. Reports source=manual, which is honest -- Vercel
#    stamps only its own cron, so this does NOT count as self-healing.
curl -s -H "Authorization: Bearer $CRON_SECRET" \
  "https://<job-search>.vercel.app/api/index?action=trigger-algotrade"

# 2. GitHub, same effect, recorded as workflow_dispatch
gh workflow run daily.yml

# 3. Locally, bypassing CI entirely
python -m jobs.ingest_session && python -m jobs.generate_signals --rebuild-panel
```

Running it when the day is already done is safe and does nothing — proven
2026-09-01 — so if in doubt, run it.

### Deliberate failure injection

For the acceptance gate's self-healing criterion only. Arm, let one
**scheduled** run fail, then **disarm before the next one**:

```bash
gh variable set    ALGOTRADE_FAULT_INJECT --body generate_signals --env paper
gh variable delete ALGOTRADE_FAULT_INJECT --env paper
gh variable list   --env paper            # confirm it is gone
```

`check_config` prints `ARMED ... this run is MEANT to fail` on every run while
it is set, and it is refused outright in `prod`. Left armed, every run fails
and it looks exactly like a real recurring fault.

Do **not** inject by breaking `MONGODB_URI`: that fails the config check before
any job starts, and the job ledger lives in the database you disconnected, so
nothing is recorded and the exercise proves nothing.

---

## Restoring a backup

Never restore straight into the live database.

```bash
gh release download backups --pattern 'algotrade-backup-*-20261004.json.gz'
python -m jobs.backup --inspect algotrade-backup-algotrade_paper-20261004.json.gz
python -m jobs.backup --restore <file> --into algotrade_scratch

ENVIRONMENT=paper MONGODB_DATABASE=algotrade_scratch \
  python -m jobs.value_portfolio --reconcile-only     # must PASS

python -m jobs.backup --restore <file> --into algotrade_paper --force
python -m jobs.value_portfolio --rebuild-holdings --backfill 30
```

---

## Replacing secrets

GitHub → Settings → Environments → `paper`.

| Secret | Rotate when |
|---|---|
| `MONGODB_URI` | Atlas password changed, or suspected exposure |
| `TELEGRAM_BOT_TOKEN` | BotFather `/revoke`, or exposure |
| `HEALTHCHECKS_PING_URL` | New check created |

After any rotation:

```bash
gh workflow run daily.yml        # must go green
```

**If you rotate the Atlas password**, update `.env` too, or your local
commands break while CI keeps working — a confusing half-hour.

---

## Validating portfolio state by hand

```bash
python - <<'PY'
from algotrade.config import settings
from algotrade.repositories import client
from algotrade.repositories.cash import CashRepository
from algotrade.repositories.holdings import HoldingRepository
from algotrade.repositories.snapshots import SnapshotRepository
s = settings.load(); db = client.connect(s.mongodb_uri, database=s.mongodb_database)
pid = s.default_portfolio_id
snap = SnapshotRepository(db).latest(pid)
print("NAV      ", snap.nav)
print("cash     ", CashRepository(db).balance(pid), "(ledger)")
print("holdings ", len(HoldingRepository(db).book(pid)))
print("unit NAV ", snap.unit_nav, "x", snap.units, "=", snap.unit_nav * snap.units)
PY
```

`unit_nav × units` must equal NAV. If it does not, the reconciler will already
have said so.

---

## When something is wrong

| Symptom | First command |
|---|---|
| No Telegram for 2 days | `python -m algotrade.acceptance.status` |
| `Reconcile FAIL` | `python -m jobs.value_portfolio --reconcile-only` |
| Stale data | `python -m jobs.ingest_session --check` |
| Rebalance never arrived | `python -m jobs.generate_recommendations --check-only` |
| Everything silent | Check GitHub Actions is enabled; the repo may have gone inactive |

Full procedures: [disaster-recovery.md](disaster-recovery.md).

---

## What this system will never do

Deliberate, not missing:

- **Place an order.** Ever. You trade; you report back.
- **Sell because a price fell.** There are no stop losses; that is not the
  validated strategy.
- **Trade between monthly rebalances.** The rank buffer exists so drift is
  held, not corrected.
- **Tell you to sell during a drawdown.** §11p priced quitting during an
  underperformance streak at 27–40% of terminal wealth. The daily message
  shows drawdown *and* the historical worst streak (1,203 days) so a bad
  stretch reads as normal rather than as breakage.
