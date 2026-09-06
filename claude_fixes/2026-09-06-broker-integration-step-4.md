# Placing an order, survivably — and a design choice I got wrong

**2026-09-06.** Step 4 of the
[broker integration](../docs/superpowers/specs/2026-09-05-broker-integration-design.md):
submission, the client wiring guard, the freeze, `/execute`, settlement, and
the rehearsal book.

929 unit tests, 195 dashboard tests. **Nothing here can reach a real exchange:**
`brokers/kite.py`'s `place()` still raises, and `brokers/wiring.py` routes
every non-`live_` portfolio to the stub as a construction-time refusal.

## The sequence, and why it is that order

    write intent  ->  submit  ->  confirm

Placing an order is the first irreversible write in this system. Everything
else rebuilds — holdings from executions, cash from the ledger, a bad snapshot
from a repair job — and an order that reached the exchange cannot be undone by
re-running anything.

**Never submit-then-write.** A crash in between leaves an order nobody knows
about; the next attempt places it again and the book carries twice the
position with no record of why.

Writing the intent first inverts the failure: a crash leaves a row saying *"we
may have submitted this — go and ask"*, and every order carries a deterministic
tag, so asking is a lookup. An unanswerable question becomes a query.

Three refusals worth keeping:

- **a broker that will not answer `orders` raises**, rather than returning "not
  found". *"We could not ask"* and *"it is not there"* lead to opposite
  actions, and treating the first as the second is how one order becomes two;
- **an empty order id leaves the row at `intent`** — nothing to reconcile
  against, so the next attempt must ask rather than assume;
- **a rejection is an outcome, recorded, never an exception.** Only *we do not
  know what happened* stops a human.

## The design choice I got wrong

I recommended storing the approved basket in its own collection, `option A`.
Akash chose it on that advice. It cannot record fills.

`ExecutionService.record` requires a **recommendation item seq** and refuses
one it cannot find. The resized instructions do not exist there: a trim or a
top-up has no counterpart among that period's entries and exits. On the
2026-09-04 rehearsal **all four sells were trims**, so this was the common case
rather than an edge — a basket could be approved and then never recorded.

My objection to using a revision — *"it means the strategy changed its mind"* —
was simply wrong. `r1 → r2 → r3` happened for 2026-08 with no strategy change.
A revision is a re-issue of the instruction set, and `fill_convention:
"next_open"` on the model says it always expected instructions to be expressed
at the execution open.

So freezing now writes revision `r(n+1)` with status `APPROVED`, superseding
the set it was priced from. The signal session, holds, params hash and code
commit carry forward — **the decision** — while only the pricing changes.
`accepted_baskets` was deleted; two records of one decision is how they drift.

## What the rehearsal found in its first run

**A buy was placed while its funding sell was still merely `submitted`.**

The gate released buys on `CLOSED`, which includes `submitted`. But submitted
means the broker *accepted* the order, not that it filled — and until it fills
the proceeds do not exist. Two sets now, and the distinction is the fix:

| | |
|---|---|
| `CLOSED` | do not resend — includes `submitted` |
| `SETTLED` | the money is known — excludes it |

A partial fill still releases the buys: it is terminal, the money that arrived
is what there is, and waiting for the rest would wait forever.

This is exactly what a rehearsal is for, and it is why the fault switch needed
a real book rather than a unit test.

## Freeze, then execute, then settle — three buttons, three risks

**Freeze** re-derives the basket server-side, never from the form: a client
that could name its own quantities could name any quantity. But re-deriving
reopens a gap — the feed moves — so the page carries a digest of what it drew
and a mismatch is refused rather than frozen. Neither the client dictating
values nor the server silently substituting them.

**Execute** is one button per instruction and never a batch. Every sizing
defect this system has had would have become a money defect behind an "execute
all", and a per-trade click stops you after the first rejection rather than the
thirtieth. The gate is re-evaluated in the POST, never trusted from the form:
the page was drawn before any sell was placed.

**Settle** reads and is safe to repeat — an order already recorded is a no-op
because a repeated `broker_order_id` is one the book has seen. Only terminal
orders are recorded; an open order has no final quantity or price, and
executions are never patched in place.

The price recorded is **quantity-weighted across an order's trades**. First or
last would misstate the cost basis, which tax and every P&L figure are computed
from.

## The wiring guard

Two failures, both silent, both indistinguishable from success:

- a **live** book given the stub simulates orders while the book records them
  as real — you would believe you were trading and not be;
- a **rehearsal** given the real client places real orders during what everyone
  involved believes is a dry run.

So it is a construction-time refusal, not a review convention. `client_for`
refuses a live book without credentials rather than falling back to the stub,
which would be the worst available outcome.

## Not built

**Charges reconciliation** (§9). Recorded charges are the rate-card estimate;
the contract note arrives later and STT and stamp duty post at end of day. The
correction must be a separate ledger entry, never an edit to an execution.

**The allocated-capital cap** (§10), and the invariant that makes a field edit
fail the nightly check.

**A live portfolio.** `brokers/kite.py`'s `place()` raises, there is no path to
an access token for a live book, and the static IP does not exist yet.
