# Checking that the numbers agree with each other, nightly

**2026-09-05.** Built after four defects in two days, and it found two more on
its first production run.

## Why

Every defect found on 2026-09-04/05 had one shape — **a number derived from
where the data happened to start rather than from where the thing being
measured began** — and every one was caught by a human reading a rendered page:

| Defect | How it surfaced |
|---|---|
| `total_return` measured from the first stored snapshot | User noticed Summary and History disagreed on the **sign** |
| Monthly returns measured within each month | User pasted the page; +2.1% and +0.4% compounded to +2.50% beside −0.47% |
| `day_return` compared against the newest row, not the prior session | User saw an empty Day column |
| Drawdown peak anchored to a high on a date the portfolio did not exist | Found while fixing the first |

Each is arithmetic between two stored numbers. No market, no network, no
judgement. That is the whole argument for checking it every night rather than
waiting for someone to notice a page looks wrong — three of the four were found
by the user, not the system.

## What it checks

`services/invariants.py`, pure functions over decoded rows:

- `total_return` against unit NAV and the base of 100
- `drawdown` against the peak, and zero at or above it
- `day_return` against the **prior session**, not the newest row
- the peak ratchets, and never starts below the base
- monthly returns compound to the total return

Wired into `value_portfolio` beside reconciliation. The two ask different
questions: **reconciliation asks whether the book agrees with its trade
record; this asks whether the stored figures agree with each other.** A
snapshot can reconcile perfectly and still carry a drawdown computed against
the wrong peak.

## It found two real defects immediately, both mine

**1. The peak was seeded from the global maximum.** `ValuationService` used
`peak_unit_nav()` — the maximum over the *whole* collection. A running maximum
is a function of the **past**, so re-valuing an old date inherited a high that
had not happened yet and rewrote its drawdown against a future peak.

Flooring the seed at `BASE_UNIT_NAV` the day before had fixed only half of it,
and the `--backfill 12` run afterwards silently re-contaminated the rows that
had just been repaired. It now seeds from the prior session's peak, which is
what a ratchet means.

**2. `repair_snapshots.py` used the wrong codec.** It read `unit_nav` and
`peak_unit_nav` with the **money** codec (2dp paise) when they are
**PRECISION** fields (4dp). That quantized 98.3742 to 98.37 and wrote the peak
back at paise — which is exactly how a peak of 100.5356 became `100.5400` and a
drawdown of −1.6258% became `−1.63%`.

The repair tool built to fix bad snapshots was itself writing slightly wrong
ones, and nothing would have noticed.

## Result

```
paper_momentum_v1_001:  12 checked, OK
shadow_momentum_v1_001: 12 checked, OK
```

Eleven tests, each reproducing a defect that actually shipped, so the test is
the regression and the check is what would have caught it a day later instead
of three weeks later.

## The limit worth stating

This catches *internal* inconsistency — one stored number disagreeing with
another. It cannot catch a figure that is wrong but self-consistent: if the
panel had the wrong price, unit NAV, drawdown and total return would all agree
with each other and all be wrong together. Reconciliation covers part of that
gap by checking the book against its executions; the panel itself is covered by
`panel_manifest_hash` and the integrity guard, not here.
