# A holding priced at zero, and the ₹47,000 it was hiding

**2026-09-04.** Spotted on the dashboard: HFCL showing ₹0.00 and −100.0%.
One bug found, a second and larger one found while investigating it. The
second is fixed here; the first is not.

## Bug 1 — HFCL is not worthless, it changed series (VALUATION FIXED)

```
02092026   HFCL, EQ, 02-Sep-2026, ... 225.98, 226.27
03092026   HFCL, BE, 03-Sep-2026, ... 220.10, 220.42
04092026   HFCL, BE, 04-Sep-2026, ... 231.44, 231.44
```

NSE moved HFCL from `EQ` to the `BE` (trade-for-trade) series on 2026-09-03.
`bhavcopy.read_file(series=("EQ",))` keeps EQ only — a filter that exists
because DHFL appears eight times on 2018-01-01, once as EQ and seven times as
debt series. **`BE` is not a debt series. It is the same equity in a restricted
settlement segment**, and it is trading normally: ~4 lakh shares on 2026-09-04,
closing at ₹231.44.

So from 2026-09-03 HFCL has no row in the panel. Verified against a freshly
rebuilt panel through 2026-09-04:

```
HFCL last panel row: 2026-09-02
prices_as_of(2026-09-04, {"HFCL"}) -> {}   price_date=2026-09-04
```

`prices_as_of` carries forward by **date** (a weekend resolves to the previous
session) but not by **symbol**, so nothing rescues it. `value_positions` then
carries a missing price at zero, which is documented and deliberate —
"dropping it would understate the book and hide a suspension". The reasoning is
right; the trigger is not a suspension.

| | recorded | actual |
|---|---|---|
| HFCL value | ₹0 | ₹6,943.20 |
| NAV | ₹1,92,276 | ~₹1,99,219 |
| drawdown | **−4.40%** | ~−0.9% |

Written into the 2026-09-03 and 2026-09-04 snapshots of **both** paper and
shadow, so `max_drawdown`, the history chart and the drawdown panel are all
carrying a fake 3.5-point loss — the exact input the behavioural rules in
`INVESTMENT-POLICY.md` are read against.

### The valuation half, fixed

`prices_as_of` now carries forward **by symbol** as well as by date, bounded by
`MAX_STALE_SESSIONS = 30` (~6 weeks of trading), and **returns** which symbols
were carried and from when. Past the bound the symbol is left unpriced so it
surfaces, rather than holding a stale mark forever — which is how a genuinely
delisted stock would keep phantom value.

Staleness is returned rather than logged away because a carried price is a real
estimate but not a quote, and a caller that cannot tell the difference will
eventually report a suspended holding as though it were liquid. Both jobs print
a `STALE` line per affected symbol, every time.

Verified against the live book with a panel rebuilt through 2026-09-04:

```
as_of=2026-09-04  price_date=2026-09-04  priced=31/31
stale: {'HFCL': '2026-09-02'}
HFCL  -> 226.27
invested value: Rs 198,508.19   positions at zero: []
```

Note it carried **₹226.27**, HFCL's last `EQ` close, not the ₹231.44 it
actually closed at on 2026-09-04 in `BE`. That was the honest limit of the
carry-forward on its own: the panel cannot see BE at all, so ₹226.27 was the
most recent price the system was entitled to believe.

### The series half — closed 2026-09-05, and NOT by changing the panel

Whether `BE` belongs in the *panel* was measured over fifteen years and the
answer was no: t = −0.29, no material difference, and changing it would have
forked the strategy for nothing. See
[`2026-09-04-be-series-measured.md`](2026-09-04-be-series-measured.md).

But valuation and signal generation want different things from the same data.
`valuation.unfiltered_closes` reads the session's raw bhavcopy with
`series=("EQ", "BE")` for any symbol the panel could not price, leaving the
signal path untouched. HFCL: ₹226.27 → **₹231.44**, staleness clears, invested
value 198,508.19 → **198,663.29**.

**Latest session only.** Prices in the panel are back-adjusted, so today's
close carries a cumulative factor of 1 and equals the raw file; on any earlier
date a corporate action since would make the two disagree and the difference
would be booked as a price move. Debt series stay excluded — DHFL's seven NCD
lines on 2018-01-01 are why the filter exists, and `BE` is not one of them.

## Bug 2 — every sell instruction carried a ₹0.00 price (FIXED)

Found while checking whether HFCL would be sold. It would, and so would six
others, all at zero:

```
sell    4 ASTRAMICRO @ Rs 0.00      sell   96 PAISALO    @ Rs 0.00
sell    8 GRANULES   @ Rs 0.00      sell    2 SANSERA    @ Rs 0.00
sell   30 HFCL       @ Rs 0.00      sell    8 SHILPAMED  @ Rs 0.00
                                    sell    1 THANGAMAYL @ Rs 0.00
```

Nothing to do with HFCL's series. The signal set stores only the top `exit`
ranks — **60 of ~2,600 traded symbols** — and the job defaulted any held name
outside that list to zero:

```python
for h in holdings:
    prices.setdefault(h.symbol, money.ZERO)
```

with the comment "a held name absent from today's ranking is priced at zero,
which correctly makes it worth nothing to redeploy and forces a sell". But the
sell is forced by **rank buffering**, not by the price. Falling out of the top
60 is *why* a name is sold; it says nothing about what it is worth. All seven
had good prices in the panel.

### It was not cosmetic

The zeroes dropped the sale proceeds out of investable capital. Same dry run,
before and after:

| | before | after |
|---|---|---|
| investable | ₹1,50,397 | **₹1,97,472** |
| deployed | ₹1,40,195 | ₹1,90,814 |
| dropped as unaffordable | NETWEB, APARINDS | APARINDS |

**₹47,075 of a ₹2,00,000 book — nearly a quarter — was invisible to the
sizing.** The under-sized target position also made NETWEB look unaffordable at
₹5,193 against a ₹5,013 slot, so a name that should have been bought was
dropped. This is not a display bug.

### Why it had never bitten, and why it was about to

Every execution in the book so far is a **buy** — 30 of them, no sells. The
path had never run.

It was about to become dangerous: the shadow portfolio added on 2026-09-02
auto-fills at `reference_price` with no human in between. At the September
rebalance it would have booked seven sales at ₹0 and realised a total loss on
each. Before the shadow, someone would have read "sell 96 PAISALO @ ₹0.00" and
stopped. **My own change turned a visible absurdity into an automated one.**

### The fix

`valuation.merge_prices(ranked, held, panel)` — ranked names keep the close the
ranking used, so selection and sizing cannot disagree across a panel rebuild;
held names outside the ranking are priced from the panel; and anything that
still cannot be priced is **returned for the caller to refuse on**, never
zeroed. A zero coming back from the panel counts as unpriced too, so the same
instruction cannot re-enter through the fallback.

`generate_recommendations` loads the panel only when some held name is outside
the ranking, and exits 1 rather than emitting a zero-priced instruction.

Nine tests. Note the consequence: **bug 1 now blocks the rebalance instead of
corrupting it.** With the current panel the September run refuses with
`CANNOT PRICE 1 HELD NAME(S): HFCL`. That is the right failure — loud, and
before any money moves — but it means bug 1 has to be settled before the
September rebalance can run.


## Postscript: the same fallback belongs in the recommendation too

**2026-09-05.** The BE fallback went into `value_portfolio` only. The
rehearsal showed why that was half a fix: a reference price is not just a
display number.

* It is what **slippage is measured against**. Selling HFCL at its real
  ₹231.44 against a carried ₹226.27 reference records **+228 bps of
  favourable slippage that never happened** — into `realised_slippage_bps`,
  which is §11o's evidence item (a), *are real fills inside the modelled 30 bps
  round trip?*
* It is what the **shadow auto-fills at**. The shadow would have booked ₹226.27
  — a price nobody could get — while the traded book booked ₹231.44, and the
  difference would have landed in the discretion column. A control group that
  fills at prices which did not exist is not a control group.

Measured before and after, on the same instruction:

```
r2  HFCL sell ref 226.27  -> selling at 231.44 records +228 bps slippage
r3  HFCL sell ref 231.44  -> selling at 231.44 records    +0 bps slippage
```

Cheap, because `generate_recommendations` already loads the panel and already
receives `stale` from `prices_as_of`, and `as_of` is the panel's own latest
session (`load(through=as_of)`) — so the back-adjustment guard that limits the
fallback to the newest close holds by construction rather than by a check.
