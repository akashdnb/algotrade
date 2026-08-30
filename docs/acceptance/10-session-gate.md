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
> ### ⚠️ Status 2026-08-30: the gate now prints PASS. Do not believe it yet.
>
> `acceptance.status` reports `11 / 10` sessions and ends with
> **READY FOR PAPER CAPITAL**. Ten of the eleven criteria have genuinely been
> met. **Criterion 5 has not.**
>
> The `failure recovery PASS` is *still* the 2026-08-23 simulated rehearsal
> described below — an injected ledger entry, for `value_portfolio
> 2026-08-19`, from before the system was even deployed. No real failure has
> been induced since. The warning written here on 2026-08-27 — "do the real
> injection before session 10, or the gate closes on hollow evidence" — was
> not acted on, and the gate has now closed on hollow evidence exactly as
> predicted.
>
> **The verdict logic is working as designed; the input to it is a
> simulation.** Treat the gate as OPEN until `verify_recovery` reports a
> recovery whose failed attempt was a real job failing, with the Telegram
> alert and the `/fail` heartbeat ping to show for it.
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
> | 08-30 | healthchecks.io configured "period 1 day / grace 6 h" alerts 26 h after last success; Friday→Monday is ~72 h, so it goes down **every weekend** on a healthy system | **Open — needs a UI change** |
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

```bash
# 1. break it: set MONGODB_URI in the `paper` environment to an invalid host
# 2. wait for the scheduled run -> expect a Telegram failure alert
# 3. restore the secret
# 4. wait for the next scheduled run -- do NOT run anything by hand
python -m algotrade.acceptance.verify_recovery
```

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
