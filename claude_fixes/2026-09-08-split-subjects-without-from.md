# Nine splits the subject parser could not read

**2026-09-08.** Found while checking whether pre-2010 NSE data is usable.
`_SPLIT_RE` required the word "From", and nine real splits in the 2010-2026
feed do not contain it.

```
2014-11-20  SBIN        'Face Value Split Rs.10/- To Re.1/- Per Share'   10:1
2013-07-30  ASIANPAINT  'Face Value Split Rs 10 To Re 1'                 10:1
2013-02-15  M&MFIN      'Face Value Split Rs 10 To Rs 2'                  5:1
2012-02-15  SYMPHONY    'Face Value Split Rs.10/- To Rs.2/-'              5:1
2015-03-23  GRANULES    'Face Value Split Rs 10 To Rs 1'                 10:1
2015-01-22  CORPBANK    'Face Valus Split ... Rs 10/- Per To Rs 2/-'      5:1
2013-01-28  20MICRONS   'Face Value Split Rs 10 To Rs 5'                  2:1
2010-07-06  TULIP       'Fv Split Rs.10/- To Rs.2/-'                      5:1
2010-07-30  PRATIBHA    'Fv Split Rs.10/- To Rs.2/-'                      5:1
```

The corpus behind the existing tests came from one two-month window
(Jul-Aug 2022), where every subject happened to include "From". Two more,
`ASHOKA` and `JBMA`, read `"Bonus 1:1 And Face Value Split Rs.10/- To Rs.5/-"`
— the bonus parsed, the split did not, so `price_factor` returned a *partial*
factor. That is worse than a clean miss: 2x where 4x was due, with nothing to
suggest a half-applied adjustment.

## What this did NOT break, contrary to my first reading

I measured `data/curated/**/panel.parquet` and found SBIN at **-89.8%** on its
ex-date, ASIANPAINT at -90.0%, and 301 similar events inside the liquid-300
across the window — and concluded the backtest was running on unadjusted
prices. **That was wrong**, and worth recording because the mistake is an easy
one to repeat.

The curated Parquet is the EQ-filtered *raw* panel. Adjustment happens in
`services/panel.load()`, downstream. And the factors do not come from the
subject strings at all — `reconcile` matches price discontinuities detected in
the data (`integrity.find_actions`) against the feed, so a split whose subject
would not parse is still adjusted **from the gap itself**. In the adjusted
panel:

```
SBIN        2014-11-20   +2.1%      (not -89.8%)
INFY        2014-12-02   -2.2%
ICICIBANK   2014-12-04   +0.9%
```

That is the "two independent catches" design earning its keep. The check to
run is `services.panel.load()`, never the curated store directly.

## What the fix is actually worth

Reconciliation over 2010-2016, 1,299 detected discontinuities:

| | matched | factor_mismatch | move_no_action |
|---|---|---|---|
| before | 298 | 12 | 989 |
| after | **308** | **11** | **980** |

Nine discontinuities move from "price jumped and nothing explains it" to
"explained by a known corporate action". The prices were already right; what
was missing was the **second** catch. An adjustment inferred from a gap alone
is a guess that happens to be correct — one confirmed by the feed is evidence.
It also shrinks the bucket a human is expected to read.

## And two adjustments it prevents

Making "From" optional would have introduced a false positive, so
`_NOT_A_SPLIT_RE` refuses capital reductions and consolidations outright:

```
2018-08-29  MONNETISPA  ' Capital Reduction Rs 10 To Rs 3.30 / Consolidation Rs 3.30 To Rs.10'
```

Two split-shaped clauses whose net price effect is roughly nil; reading the
first leg alone gives 3.03x. Two subjects were **already** being read wrongly
because they do contain "From":

```
2016-03-21  GRANDFONRY  'Capital Reduction - From Rs 10/- To Rs 4/- Per Share'   was 2.5x
2025-06-25  VERTOZ      'Consolidation ... From Re 1 Per Share To Rs 10 Per Share'  was 0.1x
```

A consolidation is a *reverse* split — the share count falls — so treating it
as a split adjusts the wrong way. Refused rather than guessed, per §"Fail
closed. Never guess, never fall back to a string match."

## Verified

- 15 new tests, every subject quoted verbatim with its symbol and ex-date
- 570 splits parsed, up from 561; **0 unmistakable splits missed**, down from 9
- zero regressions: all 561 previously-parsed subjects yield identical factors
  across the 34,659 cached actions
- 1,387 root tests (41 skipped)

## Bearing on pre-2010 data

Subject-parse miss rates: **2006 52%, 2008 64%, 2009 61%** against 2012's 5%
and 2020's 0%, because the older feed writes `'Fv Split Rs.10/- To Rs.5/'`.
Since factors come from detected gaps, that degrades *confirmation* rather
than correctness. The sharper obstacle is that legacy `cm*bhav` carries **no
ISIN**, and `reconcile.chains_from_isin` needs it to bridge renames — pre-2011
those chains would rest on price continuity alone.

## Decision: not extending the history

**2026-09-09. Closed — the data will not be backfilled.** The pre-2010 archive
is available (NSE still serves `cm<DDMMM><YYYY>bhav.csv.zip` back to at least
2001, verified 200 for 2001, 2006, 2008 and 2009) and the corporate-actions
API answers for those years too. So this was decided on worth, not access.

Asked directly: if a 2007-2009 run came back at -70%, would anything change?
Answer: no. The strategy is already carried knowing it fails its own
`max_drawdown_max = 0.20` gate at -53.1%. A second bad number against a
pre-registered threshold that is already breached changes no decision, and a
test whose result cannot move you is entertainment.

Recorded so it is not re-argued. If it is ever reopened, the blocker to solve
first is not the data but the **universe**: 2008-10-10 carried 1,265 traded EQ
rows against 2,552 symbols today, so a liquid-300 was the top 24% of the
market then versus the top 12% now. Whatever that measures, it is not
`momentum_v1`.
