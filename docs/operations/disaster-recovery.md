# Disaster Recovery

For each failure: how you find out, what it costs, how to fix it, how long.

**Premise:** Atlas is the source of truth; GitHub Actions is compute only.
Almost everything is rebuildable from *(session CSVs + executions + code)*. The
one thing that is not is the audit log — which is why it is in the backup.

| Failure | Detection | Impact | RTO |
|---|---|---|---|
| Atlas database loss | Heartbeat silence; job connection errors | Total — nothing runs | **2–4 h** |
| GitHub Actions outage | Heartbeat silence | System pauses; nothing corrupts | **0** (self-heals) |
| Telegram outage | `notifications.status = failed`; watchdog | Blind, not broken | **< 1 h** |
| Corrupt recommendation | Reconciliation `rollups` | One period's instructions | **15 min** |
| Corrupt holdings | Reconciliation `holdings` | Every valuation is wrong | **15 min** |
| Corrupt execution history | Reconciliation `cash.ledger` | Severe — this is the authority | **1–2 h** |

---

## 1. Atlas database loss

**Detection.** healthchecks.io alerts ~26 h after the last success. Jobs fail
with connection errors and Telegram carries the failure.

**Impact.** Total. Market data is unaffected (it lives in git), but portfolio
state is gone.

**Recovery**

```bash
# 1. new cluster / database, then apply the schema
python -m jobs.init_db

# 2. restore into a SCRATCH database first and inspect it
python -m jobs.backup --inspect backups/algotrade-backup-*.json.gz
python -m jobs.backup --restore backups/<file> --into algotrade_scratch

# 3. verify the restored state reconciles before trusting it
ENVIRONMENT=paper MONGODB_DATABASE=algotrade_scratch \
  python -m jobs.value_portfolio --reconcile-only

# 4. only then restore into the real database
python -m jobs.backup --restore backups/<file> --into algotrade_paper --force

# 5. rebuild anything after the backup date
python -m jobs.value_portfolio --rebuild-holdings --backfill 30
```

**Data loss** is bounded by backup age — at most one week. Everything after the
backup is reconstructible: sessions from git, holdings from executions, cash
from the ledger, snapshots by re-valuing.

**If there is no backup**, portfolios/executions/audit are unrecoverable.
Market data and signals are not. This is why the weekly job alerts loudly.

---

## 2. GitHub Actions outage

**Detection.** Heartbeat silence. Nothing in-system can report it.

**Impact.** None to data. Jobs do not run; nothing half-writes.

**Recovery.** None needed — the next successful run backfills every gap,
because each job asks what is missing rather than assuming yesterday worked.
For an outage spanning a month-end, the rebalance check notices the period
closed with no completed run and generates it late.

**Manual fallback**, if the outage is long:

```bash
python -m jobs.ingest_session --through $(TZ=Asia/Kolkata date +%F)
python -m jobs.generate_signals --rebuild-panel --backfill 20
python -m jobs.value_portfolio --rebuild-holdings --backfill 20
python -m jobs.daily_report
```

A system whose only execution path is a third party has a single point of
failure. Keep the archive on your laptop so this stays possible.

---

## 3. Telegram outage

**Detection.** `notifications.status = failed`; the watchdog escalates at 3 in
24 h. `send` never raises, so runs still succeed.

**Impact.** You are blind, not broken. Trading state is unaffected.

**Recovery.** Usually none — Telegram recovers and the next message sends.
If the *bot* is the problem (revoked token, removed from the channel):

```bash
python -m algotrade.check_config          # shows exactly which call fails
# rotate TELEGRAM_BOT_TOKEN in the paper environment, then:
python -m jobs.daily_report --dry-run     # confirm rendering
python -m jobs.daily_report               # confirm delivery
```

Messages are deduped by `(kind, portfolio, date)`, so a missed day's summary
cannot be re-sent. Read it from `data/runtime/portfolio_summary.json` instead.

---

## 4. Corrupt recommendation

**Detection.** Reconciliation `rollups`, or a recommendation that looks wrong.

**Impact.** One period. Nothing is executed automatically, so a bad
recommendation costs nothing until you act on it.

**Recovery.** Sets are **immutable** — regenerate rather than edit:

```bash
gh workflow run rebalance.yml \
  -f portfolio_id=paper_momentum_v1_001 -f period=2026-09 -f force=true
```

`r2` is created, `r1` is marked superseded, and both remain readable. If items
in `r1` were already executed, `r2` is generated against the post-execution
book, so no trade is duplicated.

---

## 5. Corrupt holdings

**Detection.** Reconciliation `holdings`: the stored book disagrees with the
execution history.

**Impact.** Every valuation is wrong. **Do not trade on the reported NAV.**

**Recovery.** Holdings are a derived view, so this is cheap:

```bash
python -m jobs.value_portfolio --rebuild-holdings
python -m jobs.value_portfolio --reconcile-only
```

If it still disagrees, the executions themselves are wrong — see §6.

---

## 6. Corrupt execution history

The serious one: executions are the **authority**. If they are wrong, holdings,
cash, P&L and tax are all wrong, and there is nothing to rebuild *from*.

**Detection.** Reconciliation `cash.ledger` — an execution with no ledger
entry, or the reverse. Both are written together, so either means something
wrote outside the service.

**Recovery**

```bash
# 1. what does the trade record actually say?
python - <<'PY'
from datetime import date
from algotrade.config import settings
from algotrade.repositories import client
from algotrade.repositories.executions import ExecutionRepository
s = settings.load(); db = client.connect(s.mongodb_uri, database=s.mongodb_database)
for e in ExecutionRepository(db).between(s.default_portfolio_id,
                                         start=date(2020,1,1), end=date(2100,1,1)):
    print(e.execution_id, e.symbol, e.action.value, e.quantity, e.price)
PY

# 2. compare against your BROKER contract notes -- the external truth
# 3. restore from the last good backup if the divergence predates it
# 4. otherwise append COMPENSATING executions; never edit or delete
```

**Executions are append-only by design.** A wrong fill is corrected by
recording its opposite with a note, so the history stays truthful about what
was believed and when. Deleting it would make the audit trail a fiction.

---

## 7. Backup verification

A backup nobody has read is a hypothesis. The weekly job verifies against the
live database immediately after writing, and **the restore path has been
rehearsed** (2026-08-23): a full restore into a scratch database reconciled
clean with identical NAV, cash and holdings.

Re-rehearse after any schema change:

```bash
python -m jobs.backup --out backups
python -m jobs.backup --restore backups/<file> --into algotrade_scratch
ENVIRONMENT=paper MONGODB_DATABASE=algotrade_scratch \
  python -m jobs.value_portfolio --reconcile-only
```
