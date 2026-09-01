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
> ### Status 2026-08-30: the checker was wrong, and has been fixed
>
> Earlier today `acceptance.status` printed **READY FOR PAPER CAPITAL** on
> `11 / 10` sessions. That was investigated before acting on it, and criterion
> 5's evidence did not survive the look. Three independent defects, all
> flattering the result:
>
> 1. **The failures it hunts for were mostly unrecordable.** Only
>    `ingest_session` ever wrote a `failed` row. `generate_signals` wrote to
>    the ledger *only when nothing had failed*; `value_portfolio` declared
>    `JOB_NAME` from the start and never wrote at all.
> 2. **It needed two attempts at one business date, which the design never
>    produces.** Each daily run claims a *new* date, so real self-healing was
>    invisible — `2026-08-21` has a snapshot (its data was backfilled unaided
>    on the 24th) and **no `job_run` at all**.
> 3. **The one qualifying pair was fabricated.** The 2026-08-23 rehearsal
>    hand-injected `value_portfolio:2026-08-19` attempts 1 and 2 — for a job
>    that writes no rows — reproducing the checker's exact shape.
>
> It also promised to verify "no manual job in between" and never checked, so
> a repair someone pressed *Run workflow* for would have counted.
>
> **All fixed.** Jobs now claim per business date and record failures; runs
> record their GitHub trigger and only `schedule` counts; the fabricated rows
> are marked `synthetic` and excluded. The live verdict is now
> **`WARN — session count reached but self-healing has not been
> demonstrated`**, which is the truth.
>
> ### Injection fired 2026-08-31 — half the proof is in
>
> | Half | State |
> |---|---|
> | A real scheduled run failed, was recorded, and alerted | **Verified** |
> | A later scheduled run repaired it unaided | **Pending** — 09-01 run |
>
> Run `33434995161`, `event: schedule`, failed at `Generate signals` and
> nowhere else. Ingest succeeded first, then the `/fail` heartbeat and the
> Telegram alert both fired. The ledger holds what it previously could not:
>
> ```
> generate_signals:2026-08-31:1   status=failed   trigger='schedule'
> ingest_session:2026-08-31:1     status=success  trigger='schedule'
> ```
>
> There is no snapshot for 08-31, so the gap is real and unfilled.
> `verify_recovery` reports **`FAILURES NOT RECOVERED`** — the first true
> statement it has ever made about a real event.
>
> The injector was disarmed 09-01 10:35 UTC, before that day's run. **Nothing
> is being touched until the 09-01 run completes**; intervening would
> invalidate the property being measured. Treat the gate as OPEN until
> `verify_recovery` reports `SELF-HEALING DEMONSTRATED`.
>
> Details: `claude_fixes/2026-09-01-failure-injection-fired.md`.
>
> ### Monitoring bugs found while the clock ran
>
> None are gate criteria, but all three corrupted the evidence the gate's own
> checks were reading:
>
> | Found | Defect | Status |
> |---|---|---|
> | 08-27 | `healthcheck.yml` pinged the shared healthchecks.io `/start` with no matching completion ping, flapping the check down every morning for a run that was never late | Fixed, verified live |
> | 08-27 | `watchdog.py` checked freshness `through=today`, structurally always "1 session behind" at 09:30 IST — a guaranteed false WARN | Fixed, verified live |
> | 08-30 | Runs delayed past IST midnight recorded their `job_run` against the *next* calendar day — once a Saturday — leaving no run for the session actually ingested (`clock.session_date`) | Fixed, tests pin both incidents |
> | 08-30 | healthchecks.io configured "period 1 day / grace 6 h" alerts 26 h after last success; Friday→Monday is ~72 h, so it goes down **every weekend** on a healthy system | Fixed — switched to cron mode, grace 12 h |
> | 08-30 | `weekly-backup.yml` and `record-execution.yml` ran `check_config` without passing `HEALTHCHECKS_PING_URL`, which it treats as a hard error in paper — the first-ever backup failed on its own first step, and the latent copy would have failed the first time a real fill was recorded | Fixed, backup re-run green, static guard added |
>
> See `claude_fixes/2026-08-27-heartbeat-and-staleness-false-alarms.md` and
> `claude_fixes/2026-08-30-business-date-drift-and-weekend-false-alarm.md`.
>
> **Criterion 2 note.** The window contains `workflow_dispatch` runs (a
> `daily.yml` bootstrap on 08-23, a `healthcheck.yml` verification on 08-27).
> Neither repaired a failure — they were setup and verification — but a strict
> reading of criterion 2 counts them.

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
