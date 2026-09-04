# Does excluding SERIES == 'BE' cost the strategy anything? No.

**2026-09-04.** Measured over the full history rather than argued about.
`scripts/measure_series_filter.py`, read-only, two panels from the same raw
files.

## Result: 15 years, 3,729 periods, no material difference

```
                               EQ only         EQ + BE
------------------------------------------------------
sessions                         4,004           4,004
symbols                          4,063           4,308
weight rows                    111,376         111,755
dropped weights                    170              96
trades                           6,363           5,761
final capital                3,249,775       3,134,792
total return                  +1524.9%        +1467.4%
max drawdown                    -53.2%          -52.8%
cost drag                      290,500         255,591

window 2011-07-11 .. 2026-09-02
paired mean diff/period : -0.000009   t = -0.29 on 3,729 periods
```

**t = −0.29.** Indistinguishable from zero, and this time with an effective
sample of ~177 months rather than ten.

Supporting detail: BE names account for **1,764 weight rows (1.58%), 47
distinct names in 15 years**, and 104,497 of ~111,500 rows are common to both
arms. The books are 94% identical.

The EQ-only arm reproduces the published **−53.1%** max drawdown at −53.2%,
which is the sanity check that this pipeline is measuring the same thing the
project's own numbers came from.

## The hypothesis it kills

I argued the exclusion should flatter the backtest, because `engine.run` drops
a period with no exit price, and names moved to BE are strongly
momentum-selected (median +44.4% trailing for liquid movers). The mechanism is
real but the magnitude is not: **dropped weights fall only from 170 to 96 out
of 111,376** — 74 recovered trades in 15 years.

## The reversal that shows why the short test was worthless

An earlier run on committed sessions only (2025-10 → 2026-09, ~10 months)
showed EQ+BE **ahead by 8.7pp** with a paired t of +2.63. Over the full history
it is **behind by 57pp** with t = −0.29. Same code, same question, opposite
sign. The 10-month t-statistic was inflated by per-session equity rows against
month-constant holdings; the effective sample was ~10, not 209.

Worth remembering the next time a result arrives from a short window with a
convincing-looking t.

## What this means for the decisions on the table

- **Do not change the signal panel.** Forking to `momentum_v2`, re-running
  every published number, and overwriting the live book's signal history buys
  a difference the data says is not there. The frozen strategy stays frozen.
- **`INVESTMENT-POLICY.md` needs no recalibration.** Max drawdown moves −53.2%
  → −52.8%; the −60% falsification trigger and the drawdown tolerance are
  unaffected.
- **Including BE for *valuation* is still worth doing**, and is now clearly
  separable: it makes NAV exact (HFCL at its real ₹231.44 rather than the
  carried ₹226.27) with, as measured here, no research consequence at all. That
  is a much easier change to justify than it was this morning.

## The honest caveat

Choosing a data definition after seeing which one backtests better is a
researcher degree of freedom, and §8's pre-registration discipline exists to
refuse it. This result is used here only to size the change and to decline it —
never to justify adopting it. Had EQ+BE come out ahead by 57pp instead, the
right answer would still have been to decide on correctness grounds, not on
this number.
