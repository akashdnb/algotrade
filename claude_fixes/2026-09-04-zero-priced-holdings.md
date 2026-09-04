# A holding priced at zero, and the ₹47,000 it was hiding

**2026-09-04.** Spotted on the dashboard: HFCL showing ₹0.00 and −100.0%.
One bug found, a second and larger one found while investigating it. The
second is fixed here; the first is not.

## Bug 1 — HFCL is not worthless, it changed series (NOT FIXED)

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

Two separable questions, deliberately not answered here: whether valuation
should carry the last close forward per symbol under a staleness bound rather
than assume zero, and whether `BE` belongs in the panel at all — the latter
changes historical signals, which is the "code silently changes its own past
answers" class the parity checks exist for.

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
