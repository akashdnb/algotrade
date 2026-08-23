# Runbook

Written for you in six months, having forgotten all of it.

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

---

## Monthly rebalance

You get a `REBALANCE REQUIRED` message on the last trading day of the month.

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
