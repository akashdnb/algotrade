# The 10-Session Gate

Whether the system can be trusted to run itself, and what passing does — and
does not — mean.

> **Clock started 2026-08-23.** Schedules are live; the first `daily.yml` run
> completed green on GitHub Actions. Track progress with
> `python -m algotrade.acceptance.status`.
>
> Sessions are counted from `portfolio_snapshots`, so the five that existed
> before deployment count toward the ten. If you would rather the gate measure
> only genuinely unattended sessions, note today's count and add ten.
>
> ### ✅ Gate CLOSED 2026-09-01 — and this time it is earned
>
> ```
> PASS -- 13 sessions, reconciliation clean, self-healing demonstrated
> READY FOR PAPER CAPITAL
> ```
>
> That is the same string this printed on 2026-08-30. The difference is what
> stands behind it. Then, it rested on two rows hand-injected for
> `value_portfolio` — a job that writes no ledger entries, so they could only
> have been fabricated. Now it rests on a failure that actually happened and a
> repair nobody performed.
>
> **The evidence, read from `job_runs` directly rather than from the checker:**
>
> ```
> generate_signals:2026-08-31:1   failed    trigger='schedule'   <- injected fault
> generate_signals:2026-08-31:2   success   trigger='schedule'   <- repaired unaided
> ingest_session:2026-08-31:1     success   trigger='schedule'
> value_portfolio:2026-08-31:1    success   trigger='schedule'
> ```
>
> All four conditions hold for the same business date:
>
> | | |
> |---|---|
> | A run failed | `ALGOTRADE_FAULT_INJECT`, on a genuine scheduled run. Telegram alerted; the heartbeat got its `/fail` ping |
> | A later run succeeded for that date | attempt 2, found by the backfill window |
> | Triggered by `schedule` | not `workflow_dispatch` — nobody pressed anything |
> | The gap is filled and the book reconciles | snapshot ₹2,00,820 and signal `momentum_v1:2026-08-31` both exist |
>
> The 08-31 run failed at 20:14 UTC and the 09-01 run repaired it at 17:54 UTC
> — 3 h 24 m later than its 14:30 UTC schedule, which is why this took until
> the evening. The delay is itself the argument for the backup trigger being
> built now that the gate no longer depends on a `schedule` trigger.
>
> **What this does and does not mean is unchanged** — see *What READY FOR PAPER
> CAPITAL means* below. Ten sessions prove plumbing. They say nothing about
> whether the strategy works, which needs 3–5 years.
>
> ### Monitoring defects found while the clock ran
>
> None were gate criteria, but all corrupted the evidence the gate's own checks
> were reading:
>
> | Found | Defect | Status |
> |---|---|---|
> | 08-27 | `healthcheck.yml` pinged the shared heartbeat's `/start` with no completion ping, flapping it down every morning | Fixed |
> | 08-27 | `watchdog.py` measured freshness `through=today`, guaranteeing a false WARN at 09:30 IST | Fixed |
> | 08-30 | Runs delayed past IST midnight claimed the *next* calendar day — once a Saturday (`clock.session_date`) | Fixed |
> | 08-30 | healthchecks.io "period 1 day / grace 6 h" goes down every weekend on a weekday-only schedule | Fixed — cron mode |
> | 08-30 | **Criterion 5 measured a shape the jobs could not produce**: only `ingest_session` recorded failures, `value_portfolio` recorded nothing, and each run claims a new business date | Fixed — per-date claims, trigger recorded, synthetic rows excluded |
> | 08-30 | `weekly-backup.yml` and `record-execution.yml` ran `check_config` without `HEALTHCHECKS_PING_URL` | Fixed, static guard added |
>
> See `claude_fixes/2026-08-27-*`, `2026-08-30-*` and `2026-09-01-*`.

> **Criterion 2, honestly.** The window contains `workflow_dispatch` runs — a
> `daily.yml` bootstrap on 08-23, a `healthcheck.yml` verification on 08-27, a
> `weekly-backup.yml` re-run on 08-30. None repaired a failure; they were setup
> and verification. But criterion 2 says *zero manual intervention*, and
> `acceptance.status` does not check it — so the PASS above does not verify it.
> On a strict reading, criterion 2 is not met.
>
> Recorded rather than argued away: the point of a pre-registered gate is that
> it is not reinterpreted after the fact.

---

## What is being tested

**Not** whether the strategy works. That question was answered by backtesting
and cannot be re-answered in ten sessions: PROJECT.md §9 established that
distinguishing Sharpe 0.8 from Sharpe 0.0 needs **3–5 years** of forward data.
Ten sessions of P&L is noise, and treating it as evidence is dangerous in both
directions.

What is being tested is **operational**: does the machine run without you.

---

## Criteria

| # | Criterion | Verified by |
|---|---|---|
| 1 | 10 business sessions valued | `sessions 10 / 10` |
| 2 | Zero manual intervention | No `workflow_dispatch` runs in the window |
| 3 | Reconciliation passes every session | `reconciliation PASS` |
| 4 | Alerts work | Daily message received on every session |
| 5 | **Self-healing demonstrated** | `verify_recovery` → `SELF-HEALING DEMONSTRATED` |
| 6 | Snapshots generated | 10 documents in `portfolio_snapshots` |
| 7 | Signals generated | 10 documents in `signals` |
| 8 | Recommendation generated when due | A completed `rebalance_run` for the period |
| 9 | Backup taken and verified | ≥1 asset in the `backups` release |
| 10 | No credential in any log | `gh run view --log \| grep -i token` → nothing |

```bash
python -m algotrade.acceptance.status          # 1-4, 6-8
python -m algotrade.acceptance.verify_recovery # 5
```

---

## Criterion 5 is the one that matters

The other nine confirm the happy path. Ten quiet sessions prove only that
nothing went wrong — not that the system survives something going wrong, which
is the property actually being claimed.

`verify_recovery` counts a recovery only when **all three** hold for the same
business date:

1. a run **failed**,
2. a **later** run succeeded, with no manual job between, and
3. the portfolio **reconciles** afterwards.

Two of three is not enough. A job that succeeds on retry but leaves an
inconsistent book has not recovered — it has papered over the failure, which
is worse than staying visibly broken.

### Injecting the failure

> **The old instruction here — "set `MONGODB_URI` to an invalid host" — could
> never have worked, and was replaced 2026-08-30.** It kills `check_config` at
> the workflow's *first step*, before any job starts. And `job_runs`, the
> ledger `verify_recovery` reads, lives in the database you just disconnected,
> so no failure is recorded anywhere. You would get a real alert, a real
> `/fail` ping, and zero evidence. Deleting a session file fails differently:
> the next ingest silently repairs it and never records a failure at all.

Use the fault injector, which is refused outright in prod and warns loudly
while armed:

```bash
# 1. arm it (a repo VARIABLE, so it is readable and deletable -- unlike a
#    secret, it cannot lock you out of the database)
gh variable set ALGOTRADE_FAULT_INJECT --body generate_signals --env paper

# 2. wait for the next SCHEDULED daily run. It fails for that session:
#    a job_run is claimed, raises, and is recorded `failed`; Telegram alerts;
#    the heartbeat gets its /fail ping. Do not dispatch anything by hand.

# 3. DISARM, before the following day's run
gh variable delete ALGOTRADE_FAULT_INJECT --env paper

# 4. wait for the next SCHEDULED run. Its --backfill window revisits the
#    failed session, claims attempt 2, and succeeds unaided.
python -m algotrade.acceptance.verify_recovery
```

Step 3 is the one to get right. Left armed, every subsequent run fails and it
looks exactly like a real recurring fault — which is precisely the confusion
the exercise exists to resolve. `check_config` prints
`ARMED ... this run is MEANT to fail` on every run while it is set.

> **Partially rehearsed 2026-08-23** against `algotrade_paper`: a failure was
> recorded for `value_portfolio 2026-08-19`, the gap query found it, a later
> attempt succeeded with no manual step, and reconciliation still passed →
> `SELF-HEALING DEMONSTRATED`.
>
> **That rehearsal does not satisfy the gate.** It injected a ledger entry
> directly, which exercises the *detector* but never touches the failing job,
> the Telegram alert, or the heartbeat `/fail` ping. `acceptance.status`
> currently reports PASS for this criterion on the strength of a simulation —
> which is exactly the kind of plausible-but-hollow evidence this project keeps
> finding. Do the real injection before treating the gate as met.

---

## Verdict

`acceptance.status` ends with one of:

| Verdict | Meaning |
|---|---|
| **PASS** | Sessions reached, reconciliation clean, self-healing shown |
| **WARN** | On track; something is incomplete but nothing is wrong |
| **FAIL** | A check is failing — do not proceed |

A **PASS** prints:

```
READY FOR PAPER CAPITAL
```

---

## What READY FOR PAPER CAPITAL means

**It means:** the plumbing works unattended. Data arrives, signals compute,
recommendations generate, executions record, the book reconciles, and you are
told when something breaks. You can run a paper portfolio for months without
babysitting it.

**It does not mean:**

- the strategy works — that is backtested, at ~25–30% odds (§13)
- real fills match the model — the slippage model has never met a real order
- you are ready for real money — see
  [production-checklist.md](../deployment/production-checklist.md), whose
  first gate is **6+ months of paper trading**, not ten sessions

---

## If it fails

A FAIL is information, not a setback. Fix the cause, **restart the count**.

Nine good sessions and one silent gap is not a pass. The whole claim is that
the system runs unattended, and a gap you had to notice yourself falsifies it.

---

## Recorded honestly

Two things this gate does not test, carried here so they are not lost:

**The strategy fails two of its own pre-registered gates** —
`max_drawdown_max = 0.20` and `oos_sharpe_min = 0.8` (§11o). It is carried on a
judgement call. No amount of operational reliability changes that.

**Ten sessions cannot surface a month-end corporate action, a delisting, an
NSE holiday sequence, or a drawdown.** The production checklist asks for six
months for exactly this reason. Passing this gate earns you the right to *keep
running*, not to scale up.
