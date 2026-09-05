# Sizing at the open, on a free price feed — and the evidence for trusting it

**2026-09-05.** Step 2 of the
[broker integration](../docs/superpowers/specs/2026-09-05-broker-integration-design.md):
the delayed price feed, execution-day sizing, the review screen, and the job
that keeps checking whether the feed deserves to be believed.

826 unit tests, 178 dashboard tests. `pytest -q` from the repository root now
runs 1,217 — see the last section, it did not before.

## The claim this rests on, and the number behind it

Sizing runs on Yahoo's free feed rather than a licensed real-time subscription.
The argument is that **`backtest.engine` fills at `open(D+1)`**, and an opening
price is a *static historical number* fifteen minutes after the bell — so a
delayed feed loses nothing.

That argument is only worth as much as the agreement it predicts.
`jobs/verify_feed.py`, over the live book:

| Session | |
|---|---|
| 2026-09-04 | 30/30 agree within **1 bps** |
| 2026-09-03 | 30/30 within 5 bps |
| 2026-09-02 | 30/30 within 5 bps |
| 2026-09-01 | 30/30 within 5 bps |
| 2026-08-27 | 30/30 within 5 bps |

The job runs every session afterwards, so this stays a claim under test rather
than an assurance written down once and quietly outlived.

Symbols the feed could not price, and symbols bhavcopy did not carry, are
**reported but never merged into the pass** — counting them as agreement would
let the check report success for exactly the names it could not examine.

## Frozen weights, not frozen quantities

The design's option B, now structural. `RebalanceService.resize` takes
membership **as given** and recomputes only quantities. Passing `target` in
rather than deriving it from signals is what makes "the signal is frozen, only
the prices move" a property of the code instead of a rule someone remembers.

The `size()` helper — gross, reserve, weights, rounding — is shared by
`decide()` (at the signal close) and `resize()` (at the execution open). Two
implementations would eventually disagree and both would look authoritative,
which is the failure found one commit earlier in the dashboard's private
matcher. A test asserts they agree on quantities, investable, reserve and
deployed given the same prices.

The case that justifies B: **a name that gaps up buys fewer shares for the same
weight**, where frozen quantities would have spent more rupees chasing the gap.

## What the review screen refuses to do

**It does not drop an unpriced target.** `construction.build` would size the 29
names it has prices for and record the thirtieth as dropped — correct for a
pure function, and a silent change from a 30-stock book to a 29-stock one for
anyone reading a screen. An unpriced target blocks the basket. A name dropped
for falling below the minimum trade value does *not* block: that is a decision,
not a failure to price, and the difference is the whole point.

**It does not hide the drift.** The recommendation sized at close(D); this
sizes at the open. Four instructions changed size on the 2026-09-04 rehearsal,
and each shows its delta beside the approved figure.

**It does not recompute membership.** That would silently run a different
strategy than the one approved, against a different session's ranking.

## Three defects found by pointing it at reality

**The disclaimer was inside the wrong branch.** "This page places no orders"
only rendered when there *was* a recommendation — so the empty page was silent
in exactly the state where someone might go looking for a button.

**`today` was never in the template context**, so every render claimed to be a
rehearsal. Jinja resolves an undefined name to Undefined and compares it
happily; nothing failed, the page was just wrong.

**The feed's lookback was five days.** Verifying 2026-08-27 returned "no open"
for all thirty symbols, which reads exactly like a broken feed. Correctness
never depended on the range being narrow — `parse_open` matches the row's own
date — so the comment justifying `5d` was reasoning that had stopped being true
when date-matching went in. Now `1mo`.

None of the three would have been caught by the service tests. All three
appeared within a minute of loading the page.

## `pytest -q` did not work, and README documented it

The repository has **two directories named `tests`**: `tests/` and
`dashboard/tests/`. Collecting from the root reached both, `tests.unit` resolved
against whichever landed on `sys.path` first, and seven modules importing
`tests.unit.fakes` failed with `No module named 'tests.unit'`.

So `pytest tests/unit` worked while the plain `pytest` in README's testing
section did not, and had not for some time. `pytest.ini` now scopes collection
to `tests` and excludes `dashboard`, matching how the two suites are actually
run. 1,217 pass, 41 skip.

Pre-existing, not introduced here — but README claimed a command that failed,
which is the kind of stale claim worth fixing on sight.

## One deploy break caught before it happened

`pyproject.toml` declares the dashboard's Vercel dependencies, deliberately
separate from the trading system's `requirements.txt`. The dashboard now
reaches `algotrade.brokers.tokens` on every `/broker` render, so `cryptography`
is an **import-time** dependency of the deploy. Without it the function fails
to start — it does not degrade.

## Still not built

Submission. `tests/unit/test_brokers_boundary.py`'s allow-list remains empty,
and nothing in this repository can place an order.
