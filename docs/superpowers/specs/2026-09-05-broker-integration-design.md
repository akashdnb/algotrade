# Broker integration — design

**Status:** **Steps 1–2 and 4–6 built** (§15). Step 3 is infrastructure and not begun;
Submission exists and runs against the stub only:
`brokers/kite.py`'s `place()` raises, and `brokers/wiring.py` refuses to pair a
non-`live_` portfolio with the real client. Step 3 — the static IP — is what
still gates real orders.
**Date:** 2026-09-05. Status last checked 2026-09-05.

Implementation notes:
[step 1](../../../claude_fixes/2026-09-05-broker-integration-step-1.md),
[step 2](../../../claude_fixes/2026-09-05-broker-integration-step-2.md),
[step 4](../../../claude_fixes/2026-09-06-broker-integration-step-4.md).
Where building contradicted the design, the section says so rather than
quietly reading as though it had always said the new thing — see §6's symbol
key.
**Supersedes:** nothing. Extends
[`2026-08-23-production-system-design.md`](../../architecture/2026-08-23-production-system-design.md).

A dashboard section that reads the real demat, reconciles it against the book,
and — one trade at a time, on an explicit click — places orders through
Zerodha's free Kite Connect Personal API.

---

## 1. Why this is worth building

Today a rebalance is executed by hand-typing quantity, price, charges and
`broker_order_id` into `gh workflow run record-execution.yml`, roughly sixty
times across the sell and buy legs. Every one is a chance to put a wrong number
into `executions` — the collection this system treats as authoritative and
never patches in place. Holdings, unit NAV, drawdown, the monthly returns and
the discretion/slippage split are all *rebuilt* from it.

Two consequences follow, and the second is the one that matters:

**Typed prices make the slippage number partly fictional.** `slippage_bps` is
derived from the fill price against the reference. When the fill price is typed
by a human, the measurement includes that human's typing.

**Nothing checks the book against reality.** Reconciliation asks whether the
book agrees with its trade record. `services/invariants.py` asks whether the
stored figures agree with each other. Neither asks whether either agrees with
the demat. A forgotten partial fill, an unrecorded corporate action, or a sell
that silently failed is invisible to both.

---

## 2. Scope

**In scope**

| | |
|---|---|
| Read | holdings, positions, funds/margins, orders, trades |
| Reconcile | broker holdings vs book holdings, reported as findings |
| Size | quantities from the execution-day open (§6) |
| Submit | one order per click, idempotent (§8) |
| Record | fills fetched from the broker, through the existing write path |
| Funds | an allocated-capital cap the system may never exceed (§10) |

**Explicitly out of scope**

- **No live portfolio.** `live_momentum_v1_001` is not created by this work.
  The live `BrokerClient` is built and tested; wiring a live portfolio to it is
  a separate, deliberate step gated by `docs/acceptance/10-session-gate.md` and
  `docs/deployment/production-checklist.md`.
- **No batch submit.** There is no "execute all" button, now or later.
- **No automated login.** Zerodha's stated position is that login is not to be
  automated, and the exchange requires a manual login once a day. A human
  authorises every session.
- **No change to the deployed paper or shadow books.** Neither
  `paper_momentum_v1_001` nor `shadow_momentum_v1_001` gains a broker code
  path. The stub runs against a separate portfolio in a separate database
  (§12).

---

## 3. Decisions already settled, and why

Recorded so they are not relitigated.

**Sizing follows the engine, not the calendar.** `backtest/engine.py` computes
weights at close(D) and fills at **open(D+1)** — `open.shift(-1)` in
`_fill_prices`. `domain/construction.py` is `build(targets: weight, prices) →
integer shares`: weight in, quantity out, priced at whatever it is handed. The
frozen artifact is the **weight**; the quantity has always been a function of
the execution price. Freezing quantities from close(D) would apply the wrong
price vector and make `build`'s price argument decorative.

**A delayed price feed is sufficient.** The engine needs the *open*, which is a
static historical number fifteen minutes after the bell — not a live tick. This
removes the need for a licensed real-time feed, a second demat account, and
Kite's paid data tier — which Zerodha's FAQ prices at **₹500/month per API
key**, or ₹6,000/year, not the ₹2,000 quoted in the launch announcement.

**The stub is injected, never branched on.** `if portfolio_id.startswith(...)`
would put untested routing logic on the money path, where one typo sends live
orders to the stub or rehearsal orders to the real broker.

**Discretion is measured before it is removed.** A review-and-submit screen
drives `paper − shadow` toward zero, because it removes the discretion the
shadow exists to measure. That is arguably the goal — PROJECT.md §11o finds
discretion destroys alpha — but it is chosen, not stumbled into, and it is
chosen *after* at least one hand-executed rebalance has produced a number.

---

## 4. Architecture

The dashboard already writes. `dashboard/app/recording.py` records fills by
calling `ExecutionService` → rebuild holdings → refresh cash → reconcile, in
that order, with no book-keeping logic of its own. This work follows the same
rule: **the broker module composes requests and interprets responses; it never
writes to the book directly.**

```
algotrade/
  brokers/
    base.py         BrokerClient protocol -- the only interface anything imports
    kite.py         live implementation
    stub.py         deterministic fake, with a fault switch
    tokens.py       encrypt / decrypt / TTL for the session token
  services/
    submission.py   intent-then-submit-then-confirm (pure, injected client)
    demat.py        broker holdings vs book holdings -> findings
dashboard/app/
  routes/broker.py  read-only: holdings, funds, reconciliation
  routes/execute.py submit -- registered only when EXECUTE_ENABLED
  templates/
```

`brokers/` is a new top-level package rather than a service because it is I/O
against a third party, the same reason `repositories/` is the only place
pymongo appears. `services/submission.py` and `services/demat.py` stay pure and
take a `BrokerClient`, so both are testable without a network.

**One codebase, two deployments.** The same dashboard is deployed to Vercel and
to the Oracle VM; a single `EXECUTE_ENABLED` flag decides whether
`routes/execute.py` is registered at all. `main.py` already builds its router
set at startup, so an unregistered route is *absent*, not merely guarded —
there is no handler to reach. Vercel runs with the flag off. A test asserts the
route set is empty when it is unset, because a feature flag that defaults to
"on" when misread is not a safety control.

### `BrokerClient` protocol

```python
class BrokerClient(Protocol):
    def holdings(self) -> list[BrokerHolding]: ...
    def margins(self) -> BrokerFunds: ...
    def orders(self, *, tag: str | None = None) -> list[BrokerOrder]: ...
    def trades(self, *, order_id: str) -> list[BrokerTrade]: ...
    def place(self, req: OrderRequest) -> str: ...   # returns broker order id
```

`place` is the only mutating method. A test asserts that nothing under
`algotrade/` outside `brokers/` and `services/submission.py` calls it — the
same shape as `tests/unit/test_domain_purity.py`, which fails the build if
`domain/` imports a database. A promise in a document is not enforcement.

### Where each capability runs — the static IP constraint

A **registered static IP is required for order placement**, and it is in force
now. (Zerodha's FAQ dates it 2025-04-01; commentary on SEBI's retail algo
framework dates the wider mandate 2026-04-01. The two may be Zerodha's own
requirement and the regulator's — either way both dates have passed.) Up to two
IPs (primary and secondary), account-wide across all apps, **changeable only
once per week**. Non-order endpoints — order book, positions, holdings, funds,
WebSocket — remain IP-unrestricted.

**This applies regardless of order frequency.** Orders from an unregistered IP
are rejected outright, whether or not you are below the algo threshold (§16).

This splits the deployment, and it is not negotiable:

| Capability | Where | Why |
|---|---|---|
| Holdings, funds, orders, trades, demat reconciliation | **Vercel** | IP-unrestricted |
| OAuth redirect and token exchange | **Vercel** | needs a public registered redirect URL |
| `place()` | **NOT Vercel** | serverless has no static egress IP |

Serverless functions draw from rotating pools, so a Vercel-hosted submit call
fails against a registered-IP allowlist. **GitHub Actions does not solve this
either** — hosted runners are equally dynamic. Neither of the two places this
system already runs can place an order.

### The chosen host: Oracle Cloud Always Free

A single `VM.Standard.E2.1.Micro` instance in the Mumbai or Hyderabad home
region, at **₹0/month**, running the submit service and the review-and-execute
UI. Three conditions, each of which is a failure if skipped:

**Convert the account to Pay As You Go.** Oracle reclaims Always Free instances
that are idle — 95th-percentile CPU under 20% across a 7-day window. This box
works on two days a month and sleeps the other twenty-eight, which is exactly
the reclamation profile. **PAYG accounts are exempt and remain free while usage
stays inside the Always Free limits.** Set a budget alert at ₹1 so an
accidental over-limit provision cannot bill silently. Do not fake load with a
keepalive cron; it works until it doesn't, and it fails quietly.

**Take a reserved public IP, never ephemeral.** An ephemeral IP is bound to the
instance lifecycle and can change on a stop/start. With Kite's whitelist capped
at one change per week, an IP that moves on rebalance morning costs the month.

Free Tier tenancies get **1–2 reserved public IPv4 addresses at ₹0**. Oracle's
Always Free page does not spell this out — it says only "one VNIC with one
public IP address" without distinguishing reserved from ephemeral — but the
allotment is consistently reported as free and is a **tenancy-level quota, not
a regional offering**, so the home region being Mumbai or Hyderabad does not
affect it. Confirm on the cost estimate when reserving; the downside if wrong
is a few dollars a month, not a redesign.

**Take AMD, not ARM.** Ampere A1 returns "out of capacity" constantly in
popular regions. The workload is roughly sixty HTTPS requests a month; 1 OCPU
is already excessive.

Home region is fixed at signup and Always Free resources exist only there.

### Exposure

| | Host | Reachable from |
|---|---|---|
| Read-only dashboard — NAV, history, holdings, funds, reconciliation | Vercel | public, HTTP Basic, **no order capability** |
| Review-and-execute | Oracle VM | **Tailscale only** — not publicly routable |

Putting the execute UI on the tailnet means the dangerous surface is never
internet-facing, which is strictly stronger than adding a second factor to a
public page. It also makes §14's `place()` purity test physically true as well
as enforced in code: **the Vercel deployment has no registered IP, so even a
defect that reached `place()` would be rejected by Zerodha.**

### Reaching the VM

**The registered IP is an *egress* requirement, not an ingress one.** Zerodha
checks the source address of API calls arriving at *their* servers. Nothing
needs to reach the VM from the internet, and nothing should.

- **Inbound: closed entirely.** OCI security list and host firewall drop
  everything. Even SSH goes over the tailnet, so port 22 is not exposed.
- **Outbound: the reserved public IP.** An instance with a public IP assigned
  uses it for egress via 1:1 NAT, and that is the address registered with Kite.
  The IP must be *assigned* — it is not a listening endpoint.
- **Access: Tailscale MagicDNS**, plus `tailscale serve` to get HTTPS on a
  `*.ts.net` name with a real certificate, no port number and no browser
  warning. Reachable from laptop or phone, unreachable from anywhere else.

**The OAuth redirect goes to Vercel, not the VM.** Zerodha redirects the
*browser* to a registered public URL; the token is then encrypted into
`broker_sessions`, and the VM reads it from Mongo like any other consumer. This
is what lets the VM keep zero public ingress — pointing the redirect at a
`ts.net` hostname would work only from the tailnet and invites Zerodha to
reject a non-public host.

The resulting rebalance-morning flow:

1. Vercel dashboard → **Login with Zerodha** → authenticate on Zerodha's page →
   redirected back to Vercel → encrypted token lands in `broker_sessions`.
2. Open the tailnet URL for the execute UI. It picks the token up from Mongo.
3. Click orders one at a time. Each call leaves the VM from the registered IP.
4. **Drop token** when finished, which invalidates it at Kite as well (§5).

Register the **secondary IP as a real fallback**, not a duplicate of the first.

**Why a free-tier box is defensible for something this consequential.** If the
VM is unavailable on rebalance morning, the fallback is to execute through
Kite's web terminal and record the fills by hand — which is the current process
in full. The failure mode is a degradation to the status quo, not a loss. That
justifies ₹0 over ~₹5,000/year for a managed VPS, which on a ₹2L book is 2.5%
of capital and not a rounding error at this size.

---

## 5. Authentication and the session token

Kite's OAuth flow, unmodified: redirect to
`kite.zerodha.com/connect/login?api_key=…`, the user authenticates **on
Zerodha's own page** with their own 2FA, the registered redirect URL receives a
`request_token`, and we exchange it with a SHA-256 checksum of
`api_key + request_token + api_secret` for an `access_token`.

**We never see the password or the TOTP seed.** No credential field is built.

The token is **encrypted at rest, not hashed** — hashing is one-way and the
plaintext is required for the `Authorization: token api_key:access_token`
header. AES-GCM with a key from `BROKER_TOKEN_KEY`, ciphertext stored in a new
`broker_sessions` collection. Both deployments hold the key — reads need the
token as much as writes do — so it is set in the Vercel environment *and* on
the VM. The asymmetry that matters is the registered IP, not the key.

| | |
|---|---|
| Expiry | `expires_at` = next 06:00 IST — Kite's own regulatory expiry, so the record cannot disagree with reality |
| TTL index | Mongo TTL on `expires_at`; the row disappears when the token dies |
| Stale | any call with no live row returns "authorise again", never a silent failure |
| Logout | "drop token" calls Kite's invalidate endpoint *and* deletes the row — deleting only locally leaves the token live on their side |

`Settings` already carries `broker_api_key`, `broker_api_secret`,
`broker_access_token` and `broker_refresh_token`. **`broker_token_key` is the
only new setting** — allocated capital is portfolio state, not configuration,
and lives on the portfolio document (§10).

**Rate limits.** Order placement is capped at **10 orders/sec per trading
account** — per account, not per app — and **rejected orders count toward the
limit** too, so a retry storm against a failing order is self-defeating.
Exceeding it returns HTTP 429. General endpoints are 3 req/sec. The client is
capped at 1 req/sec throughout, an order of magnitude under the tighter limit,
and one human click per order makes the ceiling unreachable in practice.

---

## 6. Sizing

**Frozen weights, quantities computed once, affordability live.**

1. At rebalance generation, targets are stored as **weights** (unchanged).
2. On execution morning, ≥15 minutes after the open, fetch each symbol's
   **opening price** from the delayed feed.
3. Call `construction.build(targets=weights, prices=opens)` once. These
   quantities are fixed for the rebalance and shown as a fixed basket before
   any click.
4. After each fill, recheck **affordability only** — cash moves during the
   session and sell proceeds may be partly withheld. A buy that no longer fits
   is **deferred to day 2 at its original quantity** (§7), not reduced. It is
   skipped outright only if it still does not fit on day 2, and the reason is
   recorded; `record_execution.py` already models skips.

   Deferring rather than clamping matters: a downward clamp *is* re-sizing, and
   would reintroduce exactly the drift this rule exists to prevent. Note that
   `construction.build` applies its own "never exceed the cash available" rule
   at step 3, against the capital known then — the affordability recheck is a
   second, later question and must not be allowed to alter step 3's output.

**Quantities are never recomputed.** Recomputing them mid-session would run a
different construction from the frozen one, and would also mean the number on
screen for trade #28 is not the number reviewed at the start — defeating the
purpose of a per-trade button.

**Price feed.** Yahoo Finance (`.NS`) — no account, no key, ~15 minutes
delayed, which is irrelevant because the open is static. **A feed price must
never enter stored state**: it sizes the order and is then discarded. Sizing
and valuation both remain on committed bhavcopy.

**Feed verification.** The next session's bhavcopy carries the official open.
A job compares every price sized on against it and raises a finding beyond a
threshold — the same shape as the holiday-duplicate check. The price we traded
on becomes checkable after the fact.

### Symbol mapping

Three namespaces are in play at once: bhavcopy's NSE symbol, the feed's
`SYMBOL.NS`, and Kite's `tradingsymbol` plus `instrument_token`. They drift on
series changes, renames, and the feed lagging a rename by weeks. **The danger
is that a wrong mapping is silent** — no error, just *a* price, for the wrong
instrument, which then sizes a real order.

- ~~**Anchor on ISIN.**~~ **Revised during step 1 — the key is the NSE
  symbol.** ISIN is the better identity, but it cannot always be obtained:
  `sec_bhavdata_full_*` (the current session format) carries none, nor does
  Kite's instruments dump; only the retired `cm*bhav` archive, the
  corporate-action feed and Kite's holdings do. Nothing on the recommendation
  path populates `isin` either, so book holdings carry `""` and ISIN-only
  matching would call every position unmatchable. **A key that cannot always
  be computed is not a key.** ISIN is an optional field under a unique
  *partial* index, matching prefers it and falls back to symbol, and every
  pairing records which basis it used so the weaker one stays visible.
- **The approved basket is a recommendation REVISION, not its own
  collection.** Revised 2026-09-06, having built it the other way first:
  `ExecutionService.record` requires a recommendation item seq and refuses one
  it cannot find, and the resized instructions do not exist there -- a trim has
  no counterpart among that period's entries and exits. A basket stored
  elsewhere could be approved and then never recorded. The original objection,
  that a revision implies the strategy changed its mind, was wrong: `r1 -> r2
  -> r3` happened for 2026-08 with no strategy change, and `fill_convention:
  "next_open"` says the model always expected instructions to be re-expressed
  at the execution open.
- **The map is a stored artifact, not logic that runs at trade time.** Built
  in step 1 from Kite's instruments dump, enriched with ISINs from the
  corporate-action feed and from Kite's own holdings — the broker outranks the
  feed, since the broker is what we reconcile against.
- **Refreshed on a schedule and diffed.** A symbol whose mapping changed is a
  finding, never a silent update.
- **Fail closed.** An unmapped or ambiguous symbol blocks that trade and
  reports it. Never guess, never fall back to a string match.

Two independent catches result: a bad mapping is caught up front by the ISIN
join, and after the fact by feed verification, since the wrong instrument's
price will not agree with the right instrument's bhavcopy open. This system has
already paid for the EQ→BE lesson once.

---

## 7. The two-day execution protocol

Sells fund buys, and settlement means the money is not all there at once.
Sources disagree on whether Zerodha releases 80% or 100% of CNC sell proceeds
same-day. **The percentage is never encoded** — the system reads `margins` and
spends what is actually available. If it is 100%, day 2 is empty and the
protocol collapses to a single day at no cost.

```
Day 1  sell every sell instruction        MARKET
       read margins
       buy what the available cash covers  LIMIT at open + band
Day 2  re-read margins
       buy the remainder                   LIMIT at that day's open
       unfilled or rejected -> retry next session
```

**Order types.** Sells are MARKET: they fund everything downstream, an
unfilled sell cascades into the buy leg, and a momentum exit is not
price-sensitive. Buys are LIMIT at the open plus a band from `costs.yaml`'s
slippage model, keeping execution near the price they were sized on. The
retry-next-session rule makes a no-fill cheap.

**A MARKET order must carry a market-protection value.** Kite rejects orders
with a protection value of 0 as unprotected. The sell leg therefore sets an
explicit percentage rather than relying on a default — and the value is a
config constant, not a literal, because it is a real cap on how bad a fill the
system will accept on an illiquid or surveillance-series name.

**Order slicing is capped at 10 slices.** Not a constraint at ₹6,667 per
position, but it bounds what a freeze-quantity rejection can be worked around
with, so it is recorded rather than discovered later.

**Market protection is a config value, not a literal**, starting at Zerodha's
3% default. It is a genuine risk parameter — the cap on how bad a fill the
system will accept — and belongs somewhere visible and tunable.

**The first real submission is all-LIMIT with a generous band**, both legs.
The first live run's job is to prove the path, not to optimise fills: a LIMIT
that misses costs a retry, a MARKET on never-run code costs a price that cannot
be undone. The MARKET sell leg switches on afterwards.

**Order type is recorded on every execution.** No per-leg configuration beyond
the above — more options mean more untested combinations — but recording it
means the choice can be measured after a few months rather than defended.

**A deviation to record, not to hide.** The engine buys the entire basket at
open(D+1). Day-2 buys land at open(D+2) — one extra session of drift on part of
the book. This is not introduced by this design; it is a pre-existing gap
between a backtest that assumes instant rotation and a market that settles T+1,
and nobody had written it down. Every execution records which day of the
protocol it belongs to, so the cost becomes measurable instead of assumed.

---

## 8. Submission idempotency

The first irreversible write in this system. Everything else rebuilds: holdings
from executions, cash from the ledger, a bad snapshot from a repair job. A
placed order cannot be undone by re-running anything.

**Order: write intent → submit → confirm.** Never submit-then-write.

1. Write a `submissions` row with a deterministic `tag` derived from
   `set_id:item_seq` (Kite's `tag` field, 20 chars) and `state: "intent"`.
2. If a row already exists in `intent`, **do not submit** — query
   `orders(tag=…)` first. The tag is an unambiguous join key; matching on
   `(symbol, side, quantity, timestamp)` is not.
3. Submit. Store the returned `broker_order_id`, set `state: "submitted"`.
4. Poll to terminal state. On `COMPLETE`, fetch trades and hand them to the
   existing recording path.

A crash between any two steps leaves a row that says "unknown, go and ask",
which is recoverable. Submit-then-write leaves an order nobody knows about.

**States:** `intent → submitted → filled | partial | rejected | cancelled`.
`partial` and `rejected` are not errors; they are outcomes the sizing loop and
the retry rule already expect.

---

## 9. Charges

Kite's trades API returns price and quantity. Actual charges arrive on the
contract note, not in real time, and STT and stamp duty post at end of day.

- **At fill time:** record the `costs.yaml` estimate. It is already verified
  line-by-line against Zerodha's published card (`tests/test_costs.py`).
- **Next day:** a reconciliation job compares against the contract note or the
  end-of-day funds difference.
- **A correction is a new cash-ledger adjustment, never an edit to the
  execution.** Executions are immutable; the same discipline that backfilled a
  late audit row rather than rewriting history.

Per-trade the funds-difference method works — `charges = |Δfunds| − trade
value` — and the one-order-at-a-time design is what makes it possible. It
under-reports intraday because of the EOD postings, which is exactly what the
next-day pass exists to catch.

---

## 10. Allocated capital

A cap, held on the portfolio, that sizing may never exceed **regardless of what
the broker balance actually is**. Enforced against the sleeve's own ledger
cash, not the broker's total — the broker balance answers a different question
(§11).

This is the strongest risk control in the design — stronger than the per-trade
buttons — because it bounds the damage from *any* sizing defect to a known
number, whatever the defect turns out to be. The zero-priced-holdings bug made
₹47,075 invisible to sizing; a cap bounds that class of error without needing to
anticipate it.

Two conditions:

- **Enforced in the sizing domain, not the UI.** A UI-only cap is decoration.
- **A change to the cap is a cash-ledger contribution, not a field edit.** Unit
  NAV is contribution-neutral by construction — contributions buy units at the
  prevailing price. Editing the number without a ledger entry silently
  misstates every return figure downstream, the same class as the pre-inception
  snapshots deleted as fabricated on 2026-09-05.

The cap is also what finally makes PROJECT.md §11o structural: "the system
manages the momentum half and never trades the NIFTYBEES half" is currently a
convention, and becomes an enforced limit.

**The buffer fund sits outside the cap**, or it gets spent.

### Make the wrong path impossible, not merely discouraged

Add a check to `services/invariants.py`: **allocated capital must reconcile
against the sum of ledger contributions and withdrawals.** A field edit made
without a ledger entry then fails the nightly invariant run instead of quietly
inflating unit NAV.

This belongs there specifically. The invariant module was built after four
defects of exactly this shape — a number derived from where the data happened
to start rather than from where the thing being measured began — and it found
two more on its first production run. An allocated-capital edit that skips the
ledger is the same failure wearing different clothes.

**The broker balance check is advisory, not blocking.** Warn when the broker
holds less than the allocated amount; do not refuse to trade. It answers "do
you actually have the money you claim to have allocated", which is worth
knowing and is not the same as the cap. A broker API hiccup must not be able to
stop a rebalance.

---

## 11. Demat reconciliation

`services/demat.py` compares broker holdings against book holdings and emits
`Finding` objects, reusing the shape `services/invariants.py` established.

**Policy: the broker is the observation, the book is the record.** A mismatch
is a finding a human resolves through the existing recording path. **Nothing
auto-corrects.** A broker glitch, a symbol-mapping error, or a series change
must never silently rewrite executions.

The section offers manual resolution options; it does not choose between them.

---

## 12. Where the stub lives

**Not shadow.** Shadow's only job is being the zero-discretion, zero-slippage
control that makes `paper − shadow` interpretable as the cost of discretion.
Give it broker plumbing and it acquires its own execution noise, and the
baseline stops meaning anything.

The stub runs against a portfolio in the **`algotrade_rehearsal` scratch
database** — the same instrument that found four defects in never-run code on
2026-09-05.

Note the constraint: `schema.py` restricts portfolio ids to
`^(paper|live|shadow)_[a-z][a-z0-9]*_v\d+_\d{3}$`. A "rehearsal" prefix would
require widening that pattern, which is not worth it. Use
`paper_momentum_v1_002` **inside the scratch database**, where it cannot
collide with production.

**The stub carries a fault switch** — "reject the next order with reason X" —
covering margin shortfall, price band, freeze quantity and ASM/GSM
restrictions. Per-trade submission makes a real rejection *cheap to discover*;
it does not make the rejection-handling code ever have run. HFCL is currently
in the BE surveillance series, so this is not hypothetical.

The stub fills at the reference price with zero slippage and never simulates
partial fills beyond what the fault switch is asked for.

---

## 13. Data model changes

| Collection | Change |
|---|---|
| `broker_sessions` | **New.** `_id`, `ciphertext`, `expires_at` (TTL index), `created_at` |
| `submissions` | **New.** `_id` = `set_id:item_seq`, `tag`, `state`, `broker_order_id`, `day` (1 or 2), timestamps |
| `symbol_map` | **New.** `_id` = NSE symbol; `isin` (optional, unique partial index), `feed_ticker`, `kite_tradingsymbol`, `kite_instrument_token`, `series`, `updated_on` |
| `executions` | `mode` enum gains `"broker"`; new optional `submission_tag`, `protocol_day`, `order_type` |
| `portfolios` | new `allocated_capital` (money); `execution_mode` enum gains `"broker_assisted"` |

Both enum widenings are genuine new legitimate values, unlike the `audit_logs`
`actor` enum, which was left closed on purpose because widening it would have
made the field meaningless.

---

## 14. Testing

- Unit tests for `submission.py` and `demat.py` against the stub — no network.
- **A purity test** that `place()` is unreachable from anything but
  `brokers/` and `services/submission.py`.
- **A wiring test** that a live portfolio cannot be constructed with the stub,
  and a rehearsal portfolio cannot be constructed with the live client.
- A crash-injection test per step of §8: intent-written-not-submitted,
  submitted-not-confirmed, confirmed-not-recorded.
- Feed-verification test: a sized price disagreeing with next-day bhavcopy
  raises a finding.
- **Symbol-map tests:** an unmapped symbol blocks the trade rather than
  guessing; a changed mapping on refresh raises a finding.
- **An invariant test** that allocated capital not matching the ledger fails
  the nightly check — the regression for the field-edit failure mode.
- A full rehearsal against the stub, end to end, both protocol days.

---

## 15. Sequencing

1. ✅ **Built 2026-09-05.** `BrokerClient` protocol, stub, token storage,
   **symbol map**, read-only section (holdings, funds, demat reconciliation).
   No `place()` anywhere. Vercel only. OAuth login, callback and logout.
   `symbol_map` populated: 3,243 mappings, all 30 book positions resolving.
2. ✅ **Built 2026-09-05.** Sizing from the open (`RebalanceService.resize`),
   the delayed feed, `jobs/verify_feed.py`, and the review screen. Feed opens
   agreed with NSE's official opens on 30/30 symbols across five sessions —
   within 1 bps on 2026-09-04.
3. **Infrastructure, in parallel and independent of the code:** Oracle account
   with Mumbai/Hyderabad as home region → convert to PAYG → budget alert at ₹1
   → `E2.1.Micro` → **reserved** public IP → Tailscale → register primary and a
   genuinely distinct secondary IP at `developers.kite.trade`.
4. ✅ **Built 2026-09-06**, against the stub, locally rather than on the VM --
   nothing about the sequence needs a static IP until it points at the real
   broker. Submission with intent-then-submit-then-confirm, the wiring guard,
   freeze-as-revision, `/execute`, settlement, and the rehearsal book.
   **The README rewrite (§16) still lands with the first real order**, not
   with this.
5. ✅ **Built 2026-09-06.** Charges reconciliation -- `services/charges.py`.
   A correction is a new `adjustment` ledger entry, never an edit to an
   execution, and matched on `broker_order_id` rather than symbol.
6. ✅ **Built 2026-09-06.** Allocated-capital cap, enforced in
   `RebalanceService.size` rather than a screen, plus
   `invariants.check_allocated_capital` so a field edit fails the nightly run.
7. **Only then**, and as a separate decision: a live portfolio.

Steps 3 and 4 are the boundary: everything up to here reads, sizes and
displays. Nothing yet writes to a broker, and step 3 is entirely outside the
repository.

**September's rebalance is executed by hand**, as it would have been anyway.
That produces one clean measurement of what discretion costs while there is
still discretion to measure, and it keeps untested code off the highest-stakes
day of the year.

---

## 16. Open items

- ~~SEBI's retail algo framework.~~ **Closed — no registration, no algo ID, no
  enquiry needed.** Zerodha's overview of NSE's circular is explicit that an
  algo order is one placed through an automated strategy, and **"not individual
  orders manually placed by humans one at a time, which don't require
  registration or algo IDs."** This design is exempt on two independent
  grounds: it is manual placement, and even read as an algo it runs four orders
  of magnitude below the **10 OPS threshold** (which applies per API key, per
  segment, per exchange). Individual retail users register only above that line;
  exchanges assign generic algo IDs to unregistered strategies, so there is
  nothing to obtain.

  **The static IP is the only obligation that survives**, and it is
  unconditional — it applies to all API order placement regardless of
  frequency, so being under the threshold does not exempt §4's registration.
- ~~Reserved public IPs inside the Always Free allotment.~~ **Closed.** 1–2 per
  tenancy at ₹0, quota is tenancy-level rather than regional. Not stated on
  Oracle's Always Free page, so glance at the cost estimate when reserving;
  worst case is a few dollars a month. Kite registration is self-service at
  `developers.kite.trade` → profile → **IP Whitelist**.
- `live_momentum_v1_001` does not exist; its creation trigger is the acceptance
  gate, not this document.
- **Do not display broker data publicly.** Zerodha states that displaying or
  redistributing Kite Connect API data on external platforms violates exchange
  data-vending policies. Holdings and funds are account data on a private
  single-user dashboard, which is not redistribution — but this is a second
  reason never to take the paid market-data tier and render quotes on a hosted
  page. Sized-on prices come from the delayed public feed (§6), which carries
  no such restriction.
- ~~Dashboard OTP.~~ **Closed by §4.** The execute UI is Tailscale-only, so the
  shared HTTP Basic password now guards nothing that can move money. A second
  factor on the public read-only dashboard would be defence for a page whose
  worst outcome is disclosure of positions — worth having eventually, no longer
  load-bearing.
- README's "**No code here places an order**" is **still true** and stays so
  until `brokers/kite.py`'s `place()` stops raising. It becomes false with the
  first real order, not with step 4, and must be rewritten in that commit. The replacement is decided now so it
  is not re-argued later:

  > The system recommends; a human executes. **No order is placed without an
  > explicit human click** — there is no batch submit and no unattended order
  > path, and that is a design decision, not a missing feature.

  ~~INVESTMENT-POLICY.md may lean on the manual step as a circuit-breaker.~~
  **Checked: it does not.** Its rules concern not liquidating during drawdown
  and not pausing contributions; Rule 3's release valve is pausing
  contributions, not execution friction. Nothing there depends on fills being
  typed by hand. If INVESTMENT-POLICY.md leans on the manual step as a
  circuit-breaker, removing it needs a deliberate replacement, not a silent
  deletion.
- Symbol mapping between the price feed and NSE symbols is a known bug
  surface — the EQ/BE series change has already cost this system once.
