# The monthly rebalance has never been able to fire on its own

**Found 2026-09-02** while writing tests for `due_portfolios`. Not fixed — the
fix needs one decision that is yours, below.

## The claim

`rebalance.yml` runs a due-ness check every weekday and states its contract at
the top of the file:

> the CHECK runs every weekday and exits in seconds unless: the period's last
> trading session has passed, AND no completed rebalance_run exists for that
> period. Cost is ~20 seconds a day. In exchange, **a missed rebalance repairs
> itself within 24 hours with no human involved.**

It cannot. On a scheduled run the check computes

```python
period = args.period or clock.period_of(as_of)     # as_of = latest signal date
```

so it always asks about **the month the latest session is in** — the month
currently in progress. `is_due` then refuses, correctly, because a month cannot
be known to be over while its latest session is still the latest session there
is:

```python
if clock.period_of(as_of) == period and as_of == last_session:
    following = [d for d in sessions if d > as_of]
    if not following:
        return False, f"{as_of} may not be {period}'s final session yet"
```

`as_of` is `max(sessions)` by construction, so `following` is **always** empty.
The branch is unreachable in the other direction.

## The proof

A full year of month-end sessions, nothing ever rebalanced, evaluated the way
the scheduled check evaluates it:

```
as_of=2026-02-28  period=2026-02  due=False
as_of=2026-03-28  period=2026-03  due=False
...
as_of=2026-12-28  period=2026-12  due=False

Explicit --period 2026-11 on 2026-12-28: due=True  (2026-11 is complete and
                                                    has no rebalance run)
```

Twelve months, twelve refusals. `due=True` is reachable only by passing
`--period` explicitly — which only a `workflow_dispatch` does.

The database agrees. The single rebalance run in `algotrade_paper`:

```
{'_id': 'paper_momentum_v1_001:2026-08', 'period': '2026-08',
 'status': 'completed', 'triggered_by': 'manual', 'as_of_session': '2026-08-20',
 'attempt': 3}
```

`triggered_by: manual`, and `as_of_session` is the 20th — mid-month, so it was
forced by hand. **The scheduled path has never produced a recommendation.**

## Why it went unnoticed

Every symptom reads as correct behaviour. The daily log line is

```
due  False  -- 2026-09-02 may not be 2026-09's final session yet
```

which is *true*, sounds deliberate, and is exactly what you would expect to see
on the 2nd of the month. It would have gone on being true on the 30th.

This is the failure mode PROJECT.md §11q keeps finding: not a crash, a
plausible-looking output nobody had reason to question. The system's own
reconciliation-driven design is what makes it invisible — "not due" is the
normal answer 20 days out of 21.

## The fix, and the decision it needs

The intended semantics is already written down: *the period's last trading
session has passed*. So the check should ask about **the most recent CLOSED
period** — the latest period for which a session in a later period exists —
not the period the latest session belongs to. `is_due` already handles a closed
period correctly, as the proof above shows; only the derivation of `period` is
wrong.

**What needs deciding: what happens to a month that was missed entirely?**

Say September and October both pass with no rebalance and it is now November.

First, what does *not* differ. `period` is a bookkeeping key, not a data
selector — `generate` always uses the current signals:

```python
result = service.generate(portfolio_id=portfolio_id, period=period,
                          signals=latest, prices=prices, as_of=as_of, ...)
```

So neither option trades on stale ranks. The difference is what the record
claims afterwards.

- **(a) Ask only about the latest closed period (October).** September is never
  rebalanced and the ledger keeps saying so. The gap stays visible.
- **(b) Ask about the oldest unrebalanced closed period first**, catching up one
  period per day. Each run trades on current data, and the second is close to a
  no-op because the first already moved the book to that target. But the
  November run is filed under `portfolio:2026-09` and marked completed, so the
  gap disappears and the record asserts a September rebalance that never
  happened.

Recommendation: **(a)**, with skipped periods named in the run output so the gap
is legible rather than merely absent, and `rebalance.yml`'s header comment
corrected — "repairs itself within 24 hours" becomes "catches up to the current
month within 24 hours", which is what it would actually do.

This is the same principle as append-only executions: a correction is recorded,
never written over the top of what was believed at the time.

Until this is decided, keep running the monthly rebalance by hand:

```bash
gh workflow run rebalance.yml -f period=2026-09
```
