# Investment Policy Statement

**Portfolio:** Momentum sleeve (`paper_momentum_v1_001` → `live_momentum_v1_001`)
**Written:** 2026-08-23, before any capital was committed
**Author:** Akash Ranjan

---

## Why this document exists

Fourteen months of research examined signal quality, transaction costs, tax
drag, data integrity, and execution. It concluded that **none of those is the
largest risk.**

The largest risk is me abandoning a working strategy during a period when it
looks broken.

That is not a guess. §11p measured it: across all 54 ten-year starting months
in the archive, **abandoning the strategy costs 27–40% of terminal wealth**,
and the trigger to abandon fires for **100% of investors** at every threshold
tested. Not most. All.

This document is written now, calmly, so that a frightened version of me in
2029 has to argue with evidence rather than with a feeling.

---

## 1. Allocation

| Sleeve | Weight | Managed by |
|---|---|---|
| Momentum (30 stocks) | 50% of equity allocation | This system |
| NIFTYBEES | 50% | Manually, outside this system |

The 50/50 split is not a compromise, it is the measured optimum. Return per
unit of maximum drawdown is **flat at 0.34** from 100% momentum down to 50%, so
the first half of the dilution is free on a risk-adjusted basis while cutting
max drawdown from −53.1% to −41.6% and the ulcer index from 17.0 to 9.8.

Below 50% the trade stops being free — per-unit-of-drawdown falls to 0.31 at
25% momentum. **50/50 is the last point on the flat part of the curve.**

## 2. Construction

- Top 30 by 12-1 cross-sectional momentum, liquid-300 universe
- Equal weighted
- Rank-buffered: buy at rank ≤30, sell only past rank 60
- **Monthly** rebalance; daily monitoring only
- Frozen in `config/strategy/momentum_v1.yaml`, hashed, and enforced in code

---

## 3. Behavioural rules

These are the commitments. Everything above is context.

### Rule 1 — Positions are sold only by strategy rules

A holding leaves the book when its momentum rank falls past 60 at a monthly
rebalance. That is the only reason.

### Rule 2 — Underperformance versus NIFTY is not a reason to sell

Not at −10%. Not at −30%. Not after three years.

The strategy is *expected* to trail the index for multi-year stretches. That is
a property of momentum, not evidence of failure.

### Rule 3 — Contributions may be paused, but this is not free

Pausing is the permitted release valve, because it preserves the position and
is recoverable. **It is not costless:**

| Paused after | Median cost of terminal wealth |
|---|---|
| 12 months of underperformance | **−27.2%** |
| 24 months | −11.4% |
| 36 months | −3.7% |

Pausing early is nearly as expensive as it gets. If I pause, I write down the
date and the reason, and I resume as soon as the constraint that caused it
lifts — not when performance improves.

### Rule 4 — Existing positions are never liquidated because of drawdown

This is the rule that matters most. Selling the book costs **33–40% of terminal
wealth no matter when it is done** — 38.5% at 12 months, 40.2% at 24, 33.4% at
36. Unlike every other panic response, **it does not get cheaper by waiting.**

At the single worst moment in the archive — the 2016 trough, 10.3 months in and
−21.3% behind — capitulating cost **−51.8%**. Pausing at the same instant cost
−21.8%. The difference between the two was more than half the portfolio.

### Rule 5 — Discretionary override is permitted only for

1. A **strategy bug** — the code does not implement the documented strategy
2. **Data corruption** — reconciliation fails, or the signal diverges from the
   research pipeline
3. A **regulatory** change making the strategy impermissible
4. A **broker** failure preventing execution

Poor performance is not on this list. Neither is a compelling macro argument,
nor a news story about any holding.

**An override must be written into this file, dated, before it is acted on.**

---

## 4. Expected pain — read this part twice

These are measured from the archive, not imagined:

| | 50/50 allocation |
|---|---|
| Maximum drawdown | **−41.6%** |
| Longest drawdown | **1,027 days** (2.8 years) |
| Longest underperformance streak vs NIFTY | **1,198 days** (3.3 years) |
| Worst relative shortfall | **−39.7%**, over 39.5 months |

**Dilution does not shorten the streak.** 1,203 days at 100% momentum, 1,198 at
50/50, 1,196 at 25%. Holding *any* momentum buys the full 3.3 years. Only 0%
escapes it. The behavioural problem cannot be sized away — only made shallower.

### The path does not announce itself

From §11p, the five historical streaks as they felt from *inside*:

| Streak began | Behind at 12mo | at 24mo | Trough | Ran for |
|---|---|---|---|---|
| 2012-06 | −15.2% | −21.5% | −27.4% | 27.3 mo |
| 2016-01 | −5.7% | ended | −21.3% | 19.8 mo |
| **2018-01** | **−32.1%** | **−35.9%** | **−39.7%** | **39.5 mo** |
| 2022-04 | −22.8% | ended | −27.4% | 16.8 mo |
| 2024-07 | −16.9% | −3.3% | −30.2% | 24.9 mo |

At twelve months in, 2016 was −5.7% and nearly over; 2018 was −32.1% with
another 27 months still to run. **There is no way to tell which one you are in
while you are in it.** Any rule of the form "I'll give it another year" is a
rule that cannot be applied.

---

## 5. What would actually invalidate the strategy

To be intellectually honest, this must be falsifiable. I would reconsider if:

- **Reconciliation fails persistently** — the book no longer matches its trade
  record, so no number can be trusted
- **Signal parity breaks** and cannot be explained — the live signal has
  diverged from the one that was validated
- **Realised slippage runs above ~3× the modelled 30 bps** on real fills, which
  would eat the edge measured in §11o P2
- **A drawdown exceeds −60%**, materially worse than the −53.1% the archive
  contains, suggesting the sample understated the tail
- **Ten years elapse with returns below NIFTY.** The backtest showed momentum
  beating the index in 100% of 5-year windows; a decade of failure falsifies it

None of these is "it went down".

---

## 6. What I am accepting going in

- The hypothesis **fails two of its own pre-registered gates**:
  `max_drawdown_max = 0.20` (actual −41.6%) and `oos_sharpe_min = 0.8` (actual
  0.53). It is carried on a judgement call, recorded rather than concealed.
- Assessed odds of success: **~25–30%**.
- One bear year in fourteen years of data. **The −53.1% is the benign-sample
  figure**; the true tail is unbounded by this evidence. US momentum lost ~80%
  in the 2009 recovery.
- Ten paper sessions prove plumbing, not edge. Distinguishing Sharpe 0.8 from
  Sharpe 0.0 needs **3–5 years** of forward data.

## 7. Success criterion

**Followed the process.** Not "made money".

Over any horizon short enough to feel, outcome is mostly noise. The only thing
within my control is whether I did what this document says. That is what gets
reviewed.

---

## 8. Review

This document is reviewed **annually**, and only when the portfolio is **not**
in a drawdown greater than 15%.

Rewriting the rules while frightened is precisely the failure this exists to
prevent — the same reason PROJECT.md §8 forbids rewriting a pre-registered gate
after seeing the result.

| Date | Change | In drawdown? |
|---|---|---|
| 2026-08-23 | Written, before any capital committed | n/a — paper only |

---

## Signature

I have read the numbers above. I understand that a three-year period of
trailing the index is the expected case, not the failure case, and that the
single most expensive thing I can do is sell during one.

Signed: ______________________  Date: ____________
