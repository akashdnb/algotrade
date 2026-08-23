# The 10-Session Gate

Whether the system can be trusted to run itself, and what passing does — and
does not — mean.

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

> **Rehearsed 2026-08-23** against `algotrade_paper`: a failure was recorded
> for `value_portfolio 2026-08-19`, the gap query found it, a later attempt
> succeeded with no manual step, and reconciliation still passed →
> `SELF-HEALING DEMONSTRATED`. That rehearsal used an injected ledger entry
> rather than a genuinely broken secret; **the gate requires the real thing**,
> because a simulated failure does not exercise the alerting path.

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
