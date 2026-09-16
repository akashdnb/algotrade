# Corporate actions on the live book

**2026-09-16.** TDPOWERSYS split 2:1 on 24-Aug-2026 and the book never noticed.

## What was wrong

`paper_momentum_v1_001` bought 4 TDPOWERSYS @ ₹1,507.50 on 23-Aug-2026 — one
session before the ex-date. NSE's feed carries the action:

    "Face Value Split (Sub-Division) - From Rs 2/- Per Share To Re 1/- Per Share"
    exDate 24-Aug-2026

The bhavcopy shows the discontinuity plainly: 23-Aug closed 1,534.80, 24-Aug
opened 775.00 — an implied ratio of 1.98 against a feed factor of exactly 2.

The book still says 4 shares at 1,507.50. The market says ~753. So the position
reports **−51.9%** when the true P&L is roughly flat, and every figure derived
from it is wrong: position value, NAV, drawdown, weight, and the momentum
signal's view of its own holding.

`shadow_momentum_v1_001` holds the same position and was wrong identically.

## Why it happened

The **research** path has complete corporate-action machinery — `core/actions.py`
parses factors, `core/adjust.py` back-adjusts on read, `core/reconcile.py`
corroborates feed actions against observed price moves. Backtests are correct.

The **live book** path had none of it. `executions → holdings → valuation` never
consulted the action feed, so a split was invisible to it. The two paths were
built at different times for different purposes and the gap between them was
never named.

This is a latent bug, not a one-off: with ~50–100 splits and bonuses a year in a
300-name universe and 30 names held, a monthly-rebalanced book will cross one of
these every few months.

## Why the obvious fixes don't work

**Editing the execution** breaks the append-only guarantee that makes
`executions` the authority. It also lies: nobody bought 8 shares at 753.75.

**Patching `holdings`** does not survive. `daily.yml` runs `value_portfolio
--rebuild-holdings` every session, which re-derives the book from executions —
so a hand-patched lot is silently reverted within 24 hours, and the reconcile
check would flag the patched book as disagreeing with its trade record.

Holdings are a *derived cache*. The fix has to change what they are derived
*from*.

## The design

A corporate action is a **second kind of event** in the same append-only stream
that executions already form. The book becomes a fold over both, in time order.

    executions ─┐
                ├─> derive() ─> holdings
    actions ────┘

`corporate_actions` is a new collection with the same contract as `executions`:
append-only, authoritative, replayed from scratch on every rebuild. A rebuild is
still a pure function of stored events, so it stays idempotent and `verify()`
still re-derives independently.

### Ordering: the ex-date boundary

An action with ex-date `D` sorts **before every execution on `D`**, because that
is exactly what the ex-date means:

* shares held at the close of `D-1` are entitled — they get scaled
* shares bought on `D` are bought at the already-adjusted price — they must not be

Ordering alone makes this correct: a lot acquired on the ex-date is appended
*after* the action fires, so it is never scaled. No date filter is needed inside
the fold, and the buy-on-ex-date case is handled by construction rather than by
a special case someone has to remember.

### What scales, and what doesn't

Per lot, for a share-count ratio `f`:

| Field | On a ratio of `f` |
|---|---|
| `quantity` | **multiply** by `f` |
| `price` | **divide** by `f` (via cost preservation — see below) |
| `charges` | **unchanged** — already paid, and a rupee is a rupee |
| `acquired_on` | **unchanged** — a split does not restart the holding period |
| `lot_id` | **unchanged** — same acquisition, more shares |
| `realised_pnl` | **unchanged** — already realised |

`acquired_on` is the one with teeth: scaling is not a new acquisition, so the
long-term capital-gains clock keeps running. Resetting it would silently convert
long-term gains into short-term ones at 20% instead of 12.5%.

### Fractional entitlements, and why the total comes first

Splits use integer ratios, so they never produce fractions. **Bonuses do.**

The entitlement is computed by the registrar on the **whole demat holding**, not
per tax lot, and applying the ratio lot-by-lot gets a different answer:

    lots of 2 and 2, bonus 1:3 (ratio 4/3)
      per lot: floor(2 × 4/3) = 2, twice  -> 4 shares   WRONG
      on total: floor(4 × 4/3) = 5        -> 5 shares   right

So the new total is computed first, then distributed across lots by
**largest-remainder**, ties broken oldest-lot-first. Deterministic, and it
reproduces on every rebuild.

Each lot's **price is then derived from its preserved cost**, not from dividing
by the ratio:

    new_price = (old_price × old_quantity) / new_quantity

That keeps every lot's cost basis exact no matter how the remainder landed,
which dividing by `f` would not. Prices carry 4 decimals rather than paise
because a post-split per-share cost is genuinely a repeating decimal sometimes
(1,507.50 ÷ 7), and rounding to paise there drifts the basis past the ₹0.01
tolerance `verify()` enforces.

Cash-in-lieu for the dropped fraction is **not** modelled, and says so in the
finding rather than being silently absorbed.

### What is covered, and what is deliberately refused

Anything expressible as a share-count multiplier is handled generally — this was
never going to be a TDPOWERSYS patch:

| Action | Handled | Ratio |
|---|---|---|
| Face-value split | ✅ | `old_fv / new_fv` |
| Bonus issue | ✅ | `1 + new/held` |
| Bonus **and** split on one ex-date | ✅ | the two multiply |
| Dividend | ❌ refused | no share-count change; belongs in the cash ledger |
| Rights issue | ❌ refused | factor needs the subscription price *and* whether you subscribed |
| Demerger | ❌ refused | you receive a different security; no ratio exists |
| Buyback | ❌ refused | a tender, so a human decision |
| Consolidation / reverse split | ❌ refused | ratio < 1; `core/actions.py` already refuses to parse these |

Sibling rows on one ex-date multiply, which is the BAJFINANCE lesson from
§10.6 — a bonus 4:1 and a split 2:1 announced separately on 2025-06-16 compose
to 10.0, and keeping only the first produced a phantom −89.9% session.

Refused actions are **surfaced, never guessed at**. That matches how the rest of
this system treats ambiguity, and the reconciliation check below means a refusal
cannot be quietly forgotten.

#### Why demergers and reverse splits stay manual

Both were reconsidered on the evidence rather than on principle. Counting the
2015–2026 feed against the liquid-300 of each year:

| | market-wide | hitting an in-universe name |
|---|---|---|
| demerger | ~11/yr | **~3.1/yr** — RELIANCE, ITC, HINDUNILVR, SIEMENS, VEDL, NMDC |
| reverse split | ~1.2/yr | **0 in 11 years** |

**Reverse splits: not worth building.** Zero in-universe events in eleven years,
and that is structural rather than luck — momentum ranks by trailing return, and
a company consolidating its shares is the opposite of a momentum leader. The
cost is real (a lot can round to zero shares while still holding cost, so it
needs a disposal model) and the parsing is treacherous: MONNETISPA 2018-08-29
reads *3.03x in the wrong direction* if you take only its first clause.

**Demergers: genuinely unautomatable, not merely unimplemented.** ~3/yr in
universe on exactly the large liquid names a momentum book holds, so a 30-of-300
book meets one roughly every three years — it will happen. But the number needed
to do it correctly is the cost apportionment between parent and child, set by
the scheme under s.49(2C)/(2D), published by the company weeks later and **never
in NSE's feed**. The child security may not be in the panel at all. Splitting
the basis on a guess is the plausible-wrong-number this system exists to refuse.

So the useful thing to build was not the adjustment — it was a way to *clear*
the refusal, below.

#### A refusal has to be clearable

The first cut of this dropped unsupported events instead of recording them
(`if candidate.ratio is None: continue`). A demerger on a held name was
therefore never written, never `seen`, and re-reported on **every** run: a
permanent reconciliation failure no human could ever clear.

That is worse than not checking. An unclearable error is one you learn to scroll
past, and the check it would have desensitised you to is the one that catches
splits — the whole point of this change. The runbook already states the
principle for `verify_feed`: *"red that means nothing is red you learn to
ignore."*

Fixed by storing unsupported events with a ratio of **1** — which is the truth
about the parent's share count, a demerger leaves it untouched — and splitting
the check in two:

| | severity | means | cleared by |
|---|---|---|---|
| `corporate_actions` | **error** | the system has not looked at this yet | running the job |
| `corporate_actions.standing` | **warning** | it looked; a human must finish | `--acknowledge` |

Acknowledging records only that someone took responsibility, and when. The
system cannot verify a demerger was reconciled correctly, so that is the only
honest claim available to it.

### Corroboration before anything is written

A parsed factor is not trusted on its own. Before an action is applied, the
ex-date bhavcopy row must show a price move that agrees with it:

    implied_ratio = prev_close / open      (1534.80 / 775.00 = 1.98)
    deviation from feed factor 2.0         = 1.0%,  within tolerance

This reuses the `matched` bucket logic from `core/reconcile.py` that the
backtest already depends on. A mis-parsed subject cannot corrupt the book unless
the price happened to move by the same wrong ratio on the same day.

Uncorroborated actions are reported and not applied — the deliberate asymmetry
already documented in §10.6's "conservative by default" table.

### Reconciliation closes the loop

A fourth check joins the existing three: **every feed action on a held symbol
must have been applied, or explicitly refused.** Without it, this whole
mechanism fails silently the first time the discovery job doesn't run — which is
precisely how the original bug survived.

## The near-miss: raw prices are NOT the other half of this fix

Mid-fix it looked like there was a second bug. The 2026-08-21 snapshot valued
TDPOWERSYS at 767.40 while the bhavcopy for that day plainly says 1,534.80 —
the panel is back-adjusted, so every session before an ex-date is divided by the
factor. Valuing a holding at half the price the market printed reads like an
obvious defect, and the fix looks equally obvious: use the raw close.

**That is wrong, and it silently doubles historical NAV.**

`--backfill N` marks the **current** book at a past date's prices
(`valuation.valuable_dates` says so explicitly). A current quantity already
contains every split since that date, and back-adjustment divides the price by
that same factor. They cancel:

    current_qty x adjusted(D)  ==  qty_at(D) x raw(D)
    8           x 767.40       ==  4         x 1534.80      == 6,139.20

So the back-adjusted panel was right all along, and it is right *because* the
book is now split-aware. The corporate-action fix alone makes the whole series
correct; reaching for the raw close on top of it counts the split twice.

It was caught by checking the restated snapshots rather than the reasoning: the
2026-08-20 row showed 8 shares at 1,507.50 — a quantity the book did not hold on
a date whose price already assumed it did. `test_services_valuation.py` now
asserts the cancellation directly, because the mistake is more attractive than
the correct behaviour and would otherwise be re-made.

## Blast radius

Re-deriving the book changes every snapshot from 2026-08-20 on, because the
position was being valued at half its true worth throughout. The restatement is
stated explicitly, for the same reason the HFCL one was on 09-08: a NAV that
moves on a settled date reads as a regression until you know which of the two
numbers was wrong.

    date         NAV before      NAV after     TDPOWERSYS
    2026-08-21   198,769.37    201,838.97      4 x 767.40  ->  8 x 767.40
    2026-09-15   195,997.69    198,899.69*     4 x 725.50  ->  8 x 725.50

    * pending -- see below

**The restatement is only 14 of 18 sessions so far.** Local `data/sessions`
stops at 2026-09-08, so 09-09, 09-10, 09-11 and 09-15 could not be re-valued
here; they still carry 4 shares. They share the same database, so the fix
completes on the next CI daily run, which has the full archive.

`--backfill 5` covers exactly those four sessions and chains the peak from the
restated 09-08, so the ordinary scheduled run closes it. Until it does, the
invariant check reports `[peak] 2026-09-09: peak 102.4154 fell from 103.8427` —
that is the restatement boundary, not a new fault, and it clears itself.

If it has not cleared, run the daily workflow manually with a larger backfill:

    gh workflow run daily.yml -f backfill=20
