# Reading the demat, and refusing to write to it

**2026-09-05.** Step 1 of the Kite Connect integration
([design](../docs/superpowers/specs/2026-09-05-broker-integration-design.md)):
the broker interface, a stub, encrypted session storage, symbol identity,
demat reconciliation, and a read-only dashboard section. **Nothing here can
place an order.**

Branch `broker-integration`, eight commits. 691 unit tests and 147 dashboard
tests pass, of which 102 are new.

## Why this is worth having before submission exists

Reconciliation asks whether the book agrees with its trade record. Invariants
ask whether the stored figures agree with each other. Both are arithmetic over
our own data, so **a fill that was never recorded is invisible to both** — the
book is perfectly consistent with the executions it knows about. Only the
broker can say that thirty shares left the account.

That gap is what `services/demat.py` closes, and it needed no order placement
to close it.

## The decisions worth keeping

**Encrypted, not hashed.** The plan said "store the access token hashed". That
would have stored something unusable: Kite's `Authorization` header needs the
plaintext back. Hashing is for values you only *verify*; encryption is for
values you *use*. AES-GCM rather than CBC, so a corrupted blob fails with an
error naming the cause instead of a 403 from the broker on rebalance morning.

**The TTL index is a cleaner, not a clock.** Mongo's TTL monitor runs about
once a minute, so a row can outlive its `expires_at`. Every read checks expiry
itself. Trusting the index would occasionally hand a dead token to Kite.

**Identity is the ISIN.** bhavcopy says `HFCL`, the feed says `HFCL.NS`, Kite
says `HFCL` until the scrip moves to BE. Matching on symbol would have reported
that series move as one holding both *missing* and *unexpected* — two findings
for a corporate action that moved nothing. A check that cries wolf is a check
people learn to ignore. `resolve()` fails closed: unknown or ambiguous returns
`None` and the caller must refuse to trade, because a string-match fallback
returns a price for the wrong instrument, silently, and sizes a real order.

**Prices decode through `str`, never `float`.** `Decimal(2402.75)` from a JSON
float carries binary representation error into a value that ends up in a
permanent execution record.

**Order ids are stringified.** Kite has sent both forms; a numeric id would not
compare equal to the string on a submission row, and the idempotency check
would silently miss.

## Two barriers on order placement, and they fail differently

`tests/unit/test_brokers_boundary.py` walks the AST of every module under
`algotrade/` and `dashboard/app/` and fails the build if anything calls
`place` or `place_order` outside an allow-list — the same shape as
`test_domain_purity.py`. **The allow-list is empty, and that is the current
truth.** Nothing in this repository can place an order today.

Separately, the Vercel deployment has no IP registered with Zerodha, and
Zerodha rejects orders from unregistered IPs regardless of frequency. So even a
defect that reached `place()` would be refused at the broker.

The list also carries a test that its entries exist — which immediately caught
two files I had listed before writing them — and a test that the AST check can
actually detect a violation, since a guard that cannot fail is not a guard.

## What the page will not say

An unreachable broker and an empty demat render identically in a table and mean
opposite things: *you hold nothing* versus *we do not know*. The view carries
`error` separately and the template says so outright.

`View.agrees` requires rows. Vacuous truth would render as "checked, all good"
on the one page whose entire purpose is saying whether the book matches
reality.

## Not built, and deliberately

- **The OAuth login and callback.** There is nowhere to *get* a session yet;
  storage and decryption work, but the redirect flow that fills them is not
  written. The page reports "not connected", which is honest.
- **The symbol-map builder.** The collection, repository, `diff` and
  fail-closed `resolve` exist; the job that populates it from Kite's published
  instruments dump does not.
- **Everything from §15 step 2 onward** — sizing from the open, feed
  verification, submission, charges, the allocated-capital cap.

## One dependency added

`cryptography`, for AES-GCM. The standard library has no AES, and the
alternative was writing one.
