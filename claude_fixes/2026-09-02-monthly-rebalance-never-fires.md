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

- **(a) Rebalance to the latest closed period only (October).** Missed months
  are logged and skipped. For a momentum strategy this is almost certainly
  right — September's ranks are two months stale, its reference prices are
  wrong, and its recommendation would expire on TTL anyway. But "self-healing"
  then means *catch up to now*, not *replay what was missed*, and that is a
  weaker claim than the workflow currently makes.
- **(b) Rebalance the oldest unrebalanced closed period first (September),
  catching up one month per day.** Literally self-healing, and literally
  trading on stale signals. Almost certainly wrong here.

Recommendation: **(a)**, with the skipped periods named in the run so a gap is
visible rather than silent, and the workflow's header comment corrected to say
what it actually does.

Until this is decided, keep running the monthly rebalance by hand:

```bash
gh workflow run rebalance.yml -f period=2026-09
```
