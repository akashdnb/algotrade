# Two "Total return" figures, opposite signs, and six days of invented history

**2026-09-05.** Reported as a display inconsistency; the display was the
symptom.

## The symptom

Summary said **−0.47%**, History said **+0.49%**. Both labelled "Total return".

Two denominators:

```python
# services/valuation.py -- Summary reads this off the snapshot
total_return = float((unit_nav - val.BASE_UNIT_NAV) / val.BASE_UNIT_NAV)   # vs 100

# services/performance.py -- History computes this from the series
first, last = navs[0], navs[-1]
total_return = float((last - first) / first)                    # vs first snapshot
```

They agree only when the first stored snapshot's unit NAV is exactly 100. It
was 99.0460.

## Why it was not 100

```
portfolio created_at : 2026-08-23
first execution      : 2026-08-20
first ledger entry   : 2026-08-20
first snapshot       : 2026-08-14   <- six days before the book existed
```

And those snapshots were not empty:

```
2026-08-14: positions=30  nav=198,091.93  cash=0.00  unit_nav=99.0460
2026-08-19: positions=30  nav=199,068.09  cash=0.00  unit_nav=99.5340
```

Thirty positions and zero cash on a date the portfolio held **no positions and
₹2,00,000 cash**.

`_value_one` takes `holdings = holdings_repo.book(portfolio_id)` — the
*current* book — and marks it at each backfill date's prices. For filling a
genuinely missed recent session that is correct: the book did not change. For a
date before the book existed it invents history, and cash is resolved as-of
correctly, so the rows combine as-of cash with present-day holdings and are
wrong in both components. `--backfill 5` on the first daily run manufactured
2026-08-14 through 08-19.

## The part that actually mattered

```
highest unit_nav ever observed : 100.5550 on 2026-08-18   <- an invented date
peak on latest snapshot        : 100.5600
```

**The all-time high the drawdown was measured against came from a date the
portfolio did not exist.** Every drawdown since was a real numerator over a
fictional denominator, and `new_peak` ratchets up and is never revised, so it
would have persisted indefinitely.

## Three fixes

- **`CashRepository.inception` + `valuation.valuable_dates`** refuse any target
  before the ledger's first entry. This cannot recur.
- **The peak is floored at `BASE_UNIT_NAV`.** A fresh run seeds from
  `peak_unit_nav()`, which over an empty collection is ZERO, so the first
  snapshot's peak became its own unit NAV and its drawdown was 0 by
  construction. `units` is `initial_capital / BASE_UNIT_NAV`, so a book is
  worth exactly 100/unit when funded and the peak cannot honestly start below
  that. Without this the repair would have reported **0.00%** drawdown on
  2026-08-20, hiding a real 1.63% loss.
- **Both tiles are labelled** — "since inception" and "since ⟨date⟩". They
  answer different questions, so both keep their answer and neither keeps the
  bare label.

`scripts/repair_snapshots.py` deletes the pre-inception rows and replays
peak/drawdown over the survivors. Dry run by default. Applied to both books: 4
deleted, 12 recomputed each. Max drawdown is now −2.25% against the real high
of 100.5356.

## Labelling them was the wrong fix

The two still differed after the repair — −0.47% against +1.18% — and I
labelled them "since inception" and "since ⟨date⟩" and called it correct,
because they were measuring different things.

They were, and that was the problem. **The first snapshot is written at the
END of the first valued session**, so it already contains that session's move;
measuring from it drops the move. On this book that is 1.63%, and "return since
the close of day one" is not a figure anyone wants — it is an artifact of when
snapshots begin.

`PerformanceService.total_return` now measures from `BASE_UNIT_NAV` like the
snapshot field does, because unit NAV is 100 at inception by construction. Both
pages read **−0.4679%** on both books. A window-scoped call still measures
within its window, since base 100 is not an arbitrary window's starting point —
nothing does that today; every call site is `compute(portfolio_id)`.

Two numbers under one label is a bug. Two numbers under two labels is the same
bug with better manners.

## Process note

I ran `value_portfolio --backfill 12` against the production book to verify the
inception guard, then `--backfill 1` again later. Both re-marked real snapshots
to check a read-only property. Values came out unchanged and the bogus rows
were being deleted anyway, so nothing was harmed — but that was luck. The
verification that should have been reached for first, and was in the end, is a
scratch panel plus direct calls to the pure functions.
