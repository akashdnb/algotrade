# Runbook

Written for you in six months, having forgotten all of it.

> **Live since 2026-08-23.** `paper_momentum_v1_001` on Atlas
> `algotrade_paper`, driven by GitHub Actions. Nothing here needs doing daily
> except reading one Telegram message.

**Mental model.** Atlas holds the truth. GitHub Actions is a machine that wakes
up, asks *"what is missing?"*, fills it, and goes back to sleep. Nothing
remembers that yesterday failed — today just notices the gap.

---

## Daily (30 seconds)

Read the Telegram message. That is the whole routine.

```
Reconcile   PASS     <- the only line that must be true
Data        OK
Drawdown    -0.67%
```

**If `Reconcile FAIL`:** stop. The NAV above it means nothing. Go to
[disaster-recovery.md](disaster-recovery.md) §5.

**If no message arrived:** either the job did not run or Telegram failed.
healthchecks.io tells you which — it alerts only if the job itself stopped.

```bash
python -m algotrade.acceptance.status
```

---

## Weekly (2 minutes)

```bash
python -m algotrade.acceptance.status
python -m algotrade.acceptance.verify_recovery
```

Confirm the backup landed: GitHub → Releases → `backups`. A missing weekly
backup is not urgent today and is very urgent the day you need it.

```bash
gh release view backups --repo akashdnb/algotrade
```

---

## Monthly rebalance

You get a `REBALANCE REQUIRED` message once the month has closed — on the
**first trading session of the following month**, not the last one of the
month itself. A month cannot be known to be over while its latest session is
still the latest session there is.

> **This has never fired before 2026-09-02.** The check derived its period
> from the latest session, so it always asked about the month in progress and
> always answered "not due". The one rebalance this book has had was
> `triggered_by: manual`. Fixed; the first automatic firing will be the
> September rebalance, on the first October session.
> [`claude_fixes/2026-09-02-monthly-rebalance-never-fires.md`](../../claude_fixes/2026-09-02-monthly-rebalance-never-fires.md)
>
> **A month missed entirely is not gone back for.** The run catches up to the
> latest closed month and prints `MISSED 2026-09` rather than filing a later
> run under September's name. If you see that line, the gap is real and
> permanent — the book was never rebalanced for that month.

1. **Read it.** Sells first — T+1 settlement funds the buys.

   The alert has three lists and they mean different things:

   | | |
   |---|---|
   | **Exiting** | Close the position. The name is out of the book. |
   | **Entering** | A new position. |
   | **Adjusting** | **Size change only — the name stays.** Sell part, or buy more. |

   Never act on *Adjusting* as though it were *Exiting*. Until 2026-09-05 the
   alert derived "Exiting" from every sell instruction, so a trim appeared
   there: the rehearsal produced *ATHERENERG sell 1* while the target kept 4,
   and *SKYGOLD sell 1* while the target kept 8. Selling out of those would
   have closed positions the strategy wanted held. The quantity in the
   recommendation is always the authority — the lists are a summary.

2. **Place the orders at your broker.** Nothing here does that.
3. **Record each fill**, one dispatch per fill:

```bash
gh workflow run record-execution.yml \
  -f recommendation_id=paper_momentum_v1_001:2026-09:r1 \
  -f symbol=RELIANCE -f side=buy \
  -f quantity=300 -f price=2402.75 -f charges=49.05 \
  -f broker_order_id=251001000123456
```

Partial fills are normal — record each separately; they aggregate.

4. **Declining a trade needs a reason:**

```bash
python -m jobs.record_execution --set-id <id> \
  --fills '[{"seq": 5, "skip": "insufficient funds after T+1"}]'
```

The reasons are the dataset that measures whether your discretion helps or
hurts. §9 exists for this; skipping the reason discards the measurement.

5. **Confirm.** You get a ✅ per fill and reconciliation runs automatically.

> **Do not edit a recorded fill.** Executions are append-only. To correct one,
> record the opposite with a note. The history stays truthful about what was
> believed and when.

### Rehearsing it first

Before a rebalance you have not run before — a new strategy, a changed
workflow, the first one after a long gap — copy the book into a scratch
database and walk the whole cycle there:

```bash
python scripts/seed_scratch_db.py --into algotrade_rehearsal
export MONGODB_DATABASE=algotrade_rehearsal
```

Then run the real jobs against the copy. `validation` treats a non-default
database as a **warning** hinted "intentional for a scratch database", so
nothing else has to change. The script refuses the live database by name and
does not copy `job_runs`, `audit_logs` or `notifications` — a rehearsal must
not be able to produce something that later reads as a record of the real book.

This is not ceremony. The first rehearsal, on 2026-09-05, found four defects in
a cycle that had never run: the alert calling a trim an *Exiting* instruction,
`holds` never persisted, fills dated to the signal session instead of the day
they happened, and a stale reference price for a name that had moved to `BE`.
Three weeks before it mattered.

Drop it afterwards:

```bash
mongosh --eval 'db.getSiblingDB("algotrade_rehearsal").dropDatabase()'
```

### If the rebalance refuses: `CANNOT PRICE n HELD NAME(S)`

The job stops rather than emitting an instruction with a ₹0.00 reference
price. Almost always a **series change**, not a delisting: NSE moves a name
between `EQ` and `BE` (trade-for-trade) under its surveillance framework, and
the panel keeps `EQ` only, so the name vanishes while still trading normally.

Check the bhavcopy before assuming the worst:

```bash
grep "^HFCL," data/sessions/2026/sec_bhavdata_full_04092026.csv
# HFCL, BE, 04-Sep-2026, ... 231.44   <- trading fine, just not EQ
```

Valuation handles this case itself since 2026-09-05 — it reads the raw
bhavcopy including `BE` — so a name reaching this message is genuinely absent
from the exchange file, not merely reclassified. See
[`claude_fixes/2026-09-04-zero-priced-holdings.md`](../../claude_fixes/2026-09-04-zero-priced-holdings.md).

### If the rebalance refuses: `DATA PIPELINE MISMATCH`

Something changed about **what the ranking can see** — the series filter, the
ETF denylist (`universe.DEFAULT_EXCLUSIONS`), the equity allowlist, or the
fund-name suffixes. None of those live in the frozen strategy YAML, so until
this gate existed a change to any of them altered every signal while
`FROZEN STRATEGY` still reported OK.

This is not a bug to work around. Treat it exactly like a parameter change:
**fork a v2 rather than letting it alter this book.** If the edit was genuinely
meant to apply here — a new ETF that should never have been rankable — that is
still a new strategy, and the honest route is a new portfolio on the corrected
pipeline with the old one left running until it earns the switch.

[`claude_fixes/2026-09-05-data-pipeline-gate.md`](../../claude_fixes/2026-09-05-data-pipeline-gate.md)

### The shadow book fills itself

`shadow_momentum_v1_001` gets the same recommendation and auto-fills it at the
reference price inside the rebalance workflow, before you have touched
anything. You get **no alert for it** and there is nothing to do.

That is the point: it is the version of your book where every instruction was
followed, so what you did instead is measurable. **Never record a fill against
it by hand** — not even to fix something. See
[shadow-portfolio.md](shadow-portfolio.md).

---

## The broker pages

Two read-only pages, added 2026-09-05. **Neither can place an order**, and not
only because no code does: Zerodha requires a registered static IP for order
placement and the Vercel deployment has none. See the
[design](../superpowers/specs/2026-09-05-broker-integration-design.md).

**Only one of them is on Vercel.** `/broker` is; `/review` is gated behind
`REVIEW_ENABLED` and that variable is not set there, so the route returns 404
and its module is never even imported. Two reasons, both measured:

- pricing thirty symbols takes **~27s** against the function's 30s limit, and
  a rebalance month prices forty to fifty;
- the page reads `config/costs.yaml` and the strategy spec, which
  `.vercelignore` deliberately keeps off the deploy — the dashboard reads from
  Atlas, not from disk.

Set `REVIEW_ENABLED=1` wherever the process is long-lived and the repository
is present. Until then, `./dev.sh` locally gives you the page.

### `/broker` — what the account actually holds

Shows the demat, your funds, and — **for a live portfolio only** — whether the
book agrees with reality. Paper and shadow are not compared: they hold
simulated positions against a real account, so every line would differ,
correctly, and none of it would mean anything.

Positions the demat holds and the book does not are listed separately, and are
**not a defect**. This system manages the momentum sleeve; the same account
holds the NIFTYBEES half and whatever else you own.

**Log in each morning you need it.** Kite invalidates every access token at
06:00 IST by regulation, so "Not connected" is the ordinary state, not a
fault. The button hands you to Zerodha's own page — this system never sees
your password or your TOTP.

If it says **Session refused** rather than Not connected, the token was killed
early. A master logout from Kite's web terminal does that, and no expiry
margin can predict it. The remedy is the same: log in again.

### `/review` — the basket, sized at the open

**Not on Vercel** (see above). Locally: `cd dashboard && REVIEW_ENABLED=1
./dev.sh`.

Assembles the pending recommendation into what would actually be traded:
membership frozen from the recommendation, quantities recomputed from
**today's opening prices**, sells before buys.

Quantities differ from the recommendation on purpose. The recommendation sized
at the previous close; the backtest fills at the *open*, so the open is the
faithful number. Changed sizes are flagged rather than hidden.

**A target that could not be priced blocks the basket, and no instructions are
shown at all.** Not the instructions at zero: sizing against a partial price
vector empties the target book, every holding falls out of it, and the plan
becomes a sale of the whole portfolio at zero. On a Saturday that is exactly
what it rendered before the refusal was added.

**"Nothing to execute" is the normal state between rebalances.** A set that is
superseded or completed is not work, however many of its items still read as
pending — a superseded set's items stay pending forever, because nothing ever
marks them executed.

### `/execute` — placing the frozen basket

**Not on Vercel** either, and it will not be: order placement needs a static IP
registered with Zerodha, which serverless egress has none of. Gated separately
from `/review` by `EXECUTE_ENABLED`, because reading a basket and sending it
are different intentions.

Three buttons, three different risks:

| | |
|---|---|
| **Freeze** (on `/review`) | writes revision `r(n+1)`, status `APPROVED`. Records that THESE numbers are the ones to act on |
| **Place** (per row) | irreversible. One instruction at a time, never a batch |
| **Record what filled** | reads, and is safe to press repeatedly |

**Sells before buys, and "sent" is not "settled".** A buy is not offered until
every sell has actually filled or been rejected — an order the broker merely
accepted has produced no money yet. A rehearsal on 2026-09-06 placed a buy its
funding sale had not made, which is what that distinction now prevents.

**Pressing Place twice is safe.** Each instruction writes its intent before the
broker is called, so a double click, a refresh, or a retry after a crash all
resolve to the same single order.

**If a row reads `intent`**, we may have sent it and not recorded it. Press
*Record what filled* — the recovery asks the broker by tag. Do not press Place
again; it will ask first anyway, but the button implies otherwise.

### The allocated cap

`portfolios.allocated_capital` is a ceiling sizing may not exceed. Absent means
uncapped, which is the current state.

**Changing it is a contribution or a withdrawal, never a field edit.** Unit NAV
is contribution-neutral: raise the number without a matching ledger entry and
the book gains money the unit count does not reflect, so unit NAV jumps and
reads as performance nobody earned. The nightly run checks the cap against the
`opening_balance`, `contribution` and `withdrawal` entries that explain it and
fails if they disagree.

Buys, sells, charges and dividends move money *within* the book and do not
change the allocation.

### Correcting a charge

Recorded charges are rate-card estimates. The contract note arrives later, and
STT and stamp duty post at end of day, so the estimate under-reports.

**The correction is a new `adjustment` ledger entry, never an edit to the
execution.** Executions are the trade and tax record and are never patched in
place — and editing one would destroy the ability to say what was believed
when the trade was made.

### Rehearsing it

```bash
python -m jobs.create_rehearsal          # paper_momentum_v1_002 in algotrade_rehearsal
```

Refuses any database but the scratch ones. `brokers/wiring` routes every
non-`live_` portfolio to the stub as a construction-time refusal, so this book
cannot reach an exchange even if pointed at one — and the stub's fault switch
produces the rejections a happy path never will.

**The dashboard cannot be pointed at it, by design.** Two independent guards
keep rehearsal data and the dashboard apart, in both directions:

| Guard | Refuses |
|---|---|
| `dashboard/app/settings.py` — `ALLOWED_DATABASES = ("algotrade_paper", "algotrade_prod")` | `MONGODB_DATABASE=algotrade_rehearsal` → `ConfigError: unexpected database` |
| `jobs/create_rehearsal.py` — `ALLOWED_DATABASES = {algotrade_rehearsal, algotrade_scratch}` | creating the stub book inside `algotrade_paper` |

So the rehearsal runs through the **service layer and the jobs**, not the
pages. Driving it from the UI means deliberately widening one of those two
lists, and they are not equivalent: adding `algotrade_rehearsal` to the
dashboard's list keeps production data clean and is local-only in practice,
whereas putting a scratch book in `algotrade_paper` puts it in front of the
daily report, the Telegram summary and the invariant checks, which all iterate
portfolios. Prefer the first if you ever need it.

`/review` and `/execute` would need **no broker login** either way —
`wiring.client_for` hands any non-`live_` book a `StubBroker()` and ignores
credentials. `/broker` does need one, and tokens are stored per database.

**Two further limits, if you do widen it.**

`/broker` reads the **real** Zerodha account whichever book is selected. That
is deliberate: a page that mirrored the selected book into a fake demat would
produce a reconciliation that always agrees. On a paper book you get the real
demat plus the non-comparable notice, not phantom findings.

**Confirm cannot work through the dashboard.** Both the submit and confirm
paths call `wiring.client_for(...)` with `stub=None`, so every HTTP request
builds a *fresh* `StubBroker()`. Submitting is real — the row is written to
`submissions` and the sell-before-buy gate, tag idempotency and frozen digest
are all genuinely exercised — but `settlement.settle` then asks the broker
about orders a brand-new stub has never heard of, so you get "the broker did
not answer" and never a fill, a charge or a ledger entry. That is why the
2026-09-06 rehearsal went through the service layer, and why
`paper_momentum_v1_002` still has zero holdings and zero executions. Giving
the stub back its order book from the recorded `submissions` rows would close
it — writing the row *before* calling `place()` exists precisely so we can go
and ask afterwards.

### Checking the price feed

Opening prices come from a free public feed, which is defensible only while it
demonstrably matches the exchange:

**It runs every day**, in `daily.yml` after ingestion, so the evidence
accumulates without anyone remembering. By hand:

```bash
python -m jobs.verify_feed                 # latest archived session
python -m jobs.verify_feed --on 2026-09-04
```

**A failure there does not fail the daily run**, on purpose. A feed
disagreement says nothing about ingestion, valuation or reconciliation — all
bhavcopy-based and unaffected — so failing the run would let a third-party
website break this pipeline, and red that means nothing is red you learn to
ignore. It becomes blocking once orders are placed, when a disagreement means
a real order may have been sized wrongly.

Run it after bhavcopy lands. On 2026-09-04 all 30 book symbols agreed within
**1 bps**; five sessions checked, none disagreed. Exit 1 means a symbol moved
beyond tolerance — usually a mapping problem, since a wrong mapping shows up
as a *different company's* price rather than a plausible one.

### Splits and bonuses on a held name

Runs automatically in `daily.yml`, **before** valuation. By hand:

```bash
python -m jobs.apply_corporate_actions --all-active --dry-run   # report only
python -m jobs.apply_corporate_actions --all-active
python -m jobs.value_portfolio --all-active --rebuild-holdings  # fold it in
```

Recording an action does not itself move a share. It appends to
`corporate_actions`, and the book is a fold over that collection *and*
`executions` — which is the only design that survives, because
`--rebuild-holdings` re-derives the book nightly and would revert any edit made
directly to `holdings`.

**Exit 1 means something was refused, not that the job broke.** The book is
untouched and as correct as it was before; a human has to decide. Two reasons:

*Not corroborated.* The feed's factor and the ex-date price move disagree by
more than 15%. One of them is wrong. Check the subject against the bhavcopy:

```bash
grep SYMBOL data/sessions/2026/sec_bhavdata_full_DDMMYYYY.csv   # prev_close, open
```

*Not a share-count change.* Rights, demergers, buybacks and consolidations are
refused by name. None is expressible as a ratio — a rights factor depends on
the subscription price *and* on whether you subscribed — so each needs manual
handling and none may be guessed at.

Both kinds are recorded with their reason, so the reconciliation check can tell
a declined action from an unnoticed one.

**`[FAIL] corporate_actions: n feed action(s) ... neither applied nor refused`**
means the discovery job has not run since a split hit a held name. Run it. This
is the only reconciliation check whose authority is *outside* our own data, and
it exists because the other four cannot see this class of fault at all: when
TDPOWERSYS split 2:1 on 2026-08-24 the book kept 4 shares at ₹1,507.50 against a
market near ₹753 — a phantom **−51.9%** — through three weeks of clean
reconciliations, because the book agreed with its executions and its figures
agreed with each other the whole time.

**Do not "fix" a pre-split snapshot that looks halved.** The valuation panel is
back-adjusted and the book carries post-split quantities, so the factor cancels:
8 × 767.40 is the same money as 4 × 1,534.80. Switching valuation to raw closes
looks like the obvious repair and doubles historical NAV instead. See
`claude_fixes/2026-09-16-corporate-actions-on-the-book.md`.

### Rebuilding symbol identity

```bash
python -m jobs.build_symbol_map --dry-run   # report changes, write nothing
python -m jobs.build_symbol_map
```

Needs a live broker session. Every identity change is reported and never
applied silently: a symbol whose ISIN or tradingsymbol moved is either a
corporate action worth knowing about or a bug worth catching, and the two look
identical until a human reads them.

**If two symbols claim one ISIN, neither keeps it.** `schema.py` declares
`symbol_map.isin` unique among rows that have one, so a contested ISIN is an
unresolved identity question rather than a value to pick between — and a wrong
ISIN is silent, matching to the wrong instrument rather than failing. Both rows
survive with their Kite mapping intact and `services/demat.pair()` falls back
to the symbol, recording the weaker basis. The job logs what it withheld:

```
INE477B01010 is claimed by DTIL, DVL; ISIN withheld from all of them,
matching falls back to the symbol
```

The one thing that settles a contest is the **demat**: it reports holdings, so
it names an instrument actually held rather than guessing between two
listings. Until then, expect the pair to show as symbol-matched.

Live as of 2026-09-08: 2,050 of 3,243 rows carry an ISIN, and DTIL/DVL carry
none by design.

---

## Checking health

```bash
python -m algotrade.check_config       # connectivity + config
python -m jobs.value_portfolio --reconcile-only
cat data/runtime/system_health.json
```

| Score | Meaning |
|---|---|
| **GREEN** ≥90 | Act normally |
| **YELLOW** 60–89 | Look before you trade |
| **RED** <60 | The numbers cannot be trusted |

### Two different checks run every night

They answer different questions and a book can pass one while failing the other.

| | Asks | Fails when |
|---|---|---|
| **reconciliation** | Does the book agree with its **trade record**? | Holdings, cash or rollups drift from the executions |
| **invariants** | Do the stored **figures** agree with each other? | `total_return` disagrees with unit NAV, `drawdown` with the peak, `day_return` with the prior session, or monthly returns do not compound to the total |

A snapshot can reconcile perfectly and still carry a drawdown computed against
the wrong peak — that is precisely what happened on 2026-09-04, and it took
someone reading a page to notice.

Output looks like:

```
reconciliation:
  [OK  ] holdings: match the execution history
  ...
invariants: 12 snapshot(s) cross-checked
```

**If an invariant fires, do not re-run the job and hope.** A re-run recomputes
the same figures from the same inputs and will usually write the same wrong
number back. Read the finding — it names the date, what is stored, and what the
other field implies — then decide whether the *stored row* is wrong (repair it)
or the *calculation* is wrong (fix the code, then repair). `scripts/repair_snapshots.py`
does the first, dry-run by default.

[`claude_fixes/2026-09-05-invariant-checks.md`](../../claude_fixes/2026-09-05-invariant-checks.md)

### Schema drift, and why it is not just cosmetic

```
[FAIL] mongodb.schema: 6 discrepancies vs schema.py; first: broker_sessions.ttl: index missing
         -> run: python -m jobs.init_db
```

Declaring a collection in `schema.py` does not create it. Mongo creates one
implicitly on first insert — **without the validator and without the
indexes** — so a book keeps working while the constraints it is supposed to be
under quietly do not exist. Run `python -m jobs.init_db` after any change to
`schema.py`; it is idempotent and safe on every deploy.

`check_config` names only the **first** discrepancy. The remedy is the same
whichever it names, but to see all of them:

```bash
python -m jobs.init_db --verify    # lists every one, writes nothing
python -m jobs.init_db --dry-run   # what applying would do
```

**Check the preconditions before applying to a populated collection.** A
`unique` index build fails if the existing rows already contain a duplicate,
and it fails *partway*, leaving the schema half-applied. On 2026-09-08
`symbol_map.isin` could not build because DTIL and DVL both carried
`INE477B01010`:

```python
db.symbol_map.aggregate([
    {"$match": {"isin": {"$type": "string", "$gt": ""}}},
    {"$group": {"_id": "$isin", "n": {"$sum": 1}, "syms": {"$push": "$_id"}}},
    {"$match": {"n": {"$gt": 1}}}])
```

A validator does not retro-validate, so installing one cannot reject data
already stored — but it governs every future write to those rows. Confirm they
would pass first: `db[coll].count_documents({"$nor": [{"$jsonSchema": v}]})`.

The deeper lesson from that incident: the schema declared an invariant the
**producing code did not enforce**, and the two had never met because
`init_db` had never run. A constraint in `schema.py` needs a matching refusal
in the code that writes the rows.

[`claude_fixes/2026-09-08-watchdog-schema-drift.md`](../../claude_fixes/2026-09-08-watchdog-schema-drift.md)

### When the watchdog itself reports

Two distinct messages, and the difference matters:

| Telegram says | Means | Do |
|---|---|---|
| 🔴 **Watchdog found a problem** | A check ran and found something. The monitor is **working**; the message quotes the `[FAIL]`/`[CRIT]` line and the `-> run:` remedy | Act on the quoted finding |
| 🔴 **Watchdog itself failed** | A step failed *without producing a finding* — it could not run at all | Open the run; nothing was measured |

Until 2026-09-08 both said "Watchdog itself failed — the monitor is down,
check Atlas connectivity and secrets", which sent three mornings of alerts
naming the two things that were fine while never naming the schema drift that
was wrong. **If you ever see that wording again it is a regression.**

The steps run in order and stop at the first failure, so a failure in
`check_config` means the freshness and job-success checks **never ran**. Do not
read a passing watchdog out of a run that died in step 1 — that is how a
missing session went unreported for a day on 2026-09-07.

### Alerts that are not faults

Three false alarms were diagnosed and fixed in the first week. If you see one
of these shapes again, it is a regression, not a new incident:

| Symptom | Was |
|---|---|
| `[WARN] market_data: 1 trading day behind`, every morning | The watchdog runs 09:30 IST; that day's ingest is at 20:00 IST. It now measures against the last *completed* trading day |
| healthchecks.io down every Saturday | Simple mode alerts 26 h after the last success; Friday→Monday is ~72 h. Now cron mode |
| healthchecks.io down every morning, up each evening | `healthcheck.yml` pinged `/start` on a check it does not own. Only `daily.yml` owns the heartbeat; everything else passes `--skip-healthchecks-ping` |
| `[WARN] daily_job: 1 session without a success`, with a *weekend* date | A run delayed past IST midnight claimed the next calendar day. Fixed by `clock.session_date`, but the mislabelled rows from 08-27/28 stay (the ledger is append-only) and age out of the 10-day window around **2026-09-10** |

A genuinely failing job is distinguishable from all of the above by a `failed`
row in `job_runs` and a `🔴 Job failed` Telegram message. A warning with
neither is a measurement artefact.

**But the same wording can be real.** On 2026-09-08 the watchdog reported
exactly the first and last shapes above —

```
[WARN] market_data: last session 2026-09-04, 1 trading day(s) behind
[WARN] daily_job:   1 session without a success (last: 2026-09-04)
```

— and both were true: Monday's ingest had genuinely never run. The artefact
version recurs **every** morning and names the *current* day; the real version
names a **specific stale date** that does not move. Check the date before
filing it under the table above. The mislabelled 08-27/28 rows still age out
of the 10-day window around **2026-09-10**, so until then that row can mask a
real one.

### Forcing the daily run

Three ways, in increasing order of "counts as a human intervening":

```bash
# 1. The backup trigger. Reports source=manual, which is honest -- Vercel
#    stamps only its own cron, so this does NOT count as self-healing.
curl -s -H "Authorization: Bearer $CRON_SECRET" \
  "https://<job-search>.vercel.app/api/index?action=trigger-algotrade"

# 2. GitHub, same effect, recorded as workflow_dispatch
gh workflow run daily.yml

# 3. Locally, bypassing CI entirely
python -m jobs.ingest_session && python -m jobs.generate_signals --rebuild-panel
```

Running it when the day is already done is safe and does nothing — proven
2026-09-01 — so if in doubt, run it.

**The gap is the unit of work, not "today".** One run backfills every missing
session up to `clock.session_date`, so catching up after a failed day needs no
`--through` and no per-day loop. On 2026-09-08 a single run took the book from
2026-09-04 to 2026-09-07 and revalued the five sessions behind it.

It targets the last **completed** session, so a run before that day's bhavcopy
is published correctly stops one day short. `data freshness: 1 session(s)
behind` at 16:00 IST is right, not a failure.

**If it fails on the archive:**

```
::error::Archive has 0 files, expected ~4,000
```

`data/archive` is restored from the `market-archive-v1` cache and downloaded
from the release of the same name on a miss. The download used to be gated on
`cache-hit`, which answers "was there an entry" and not "is the archive
usable" — so the 192-byte empty cache written on 2026-08-23 hit cleanly, the
download was skipped, and the assertion failed the run. It is now gated on
counting the CSVs, so an empty or partial hit re-downloads. If it recurs, look
at the cache before the release:

```bash
gh cache list | grep market-archive     # a few hundred MB, not a few hundred bytes
gh release view market-archive-v1       # archive.tar.zst, ~247 MB
gh cache delete <id>                    # deleting an empty one loses nothing
```

The assertion is the real gate and the download deliberately keeps
`continue-on-error`: a failed download is survivable, a **truncated panel** is
not, and until 2026-09-04 the two were indistinguishable.

### Deliberate failure injection

For the acceptance gate's self-healing criterion only. Arm, let one
**scheduled** run fail, then **disarm before the next one**:

```bash
gh variable set    ALGOTRADE_FAULT_INJECT --body generate_signals --env paper
gh variable delete ALGOTRADE_FAULT_INJECT --env paper
gh variable list   --env paper            # confirm it is gone
```

`check_config` prints `ARMED ... this run is MEANT to fail` on every run while
it is set, and it is refused outright in `prod`. Left armed, every run fails
and it looks exactly like a real recurring fault.

Do **not** inject by breaking `MONGODB_URI`: that fails the config check before
any job starts, and the job ledger lives in the database you disconnected, so
nothing is recorded and the exercise proves nothing.

---

## Restoring a backup

Never restore straight into the live database.

```bash
gh release download backups --pattern 'algotrade-backup-*-20261004.json.gz'
python -m jobs.backup --inspect algotrade-backup-algotrade_paper-20261004.json.gz
python -m jobs.backup --restore <file> --into algotrade_scratch

ENVIRONMENT=paper MONGODB_DATABASE=algotrade_scratch \
  python -m jobs.value_portfolio --reconcile-only     # must PASS

python -m jobs.backup --restore <file> --into algotrade_paper --force
python -m jobs.value_portfolio --rebuild-holdings --backfill 30
```

---

## Replacing secrets

GitHub → Settings → Environments → `paper`.

| Secret | Rotate when |
|---|---|
| `MONGODB_URI` | Atlas password changed, or suspected exposure |
| `TELEGRAM_BOT_TOKEN` | BotFather `/revoke`, or exposure |
| `HEALTHCHECKS_PING_URL` | New check created |

After any rotation:

```bash
gh workflow run daily.yml        # must go green
```

**If you rotate the Atlas password**, update `.env` too, or your local
commands break while CI keeps working — a confusing half-hour.

---

## Validating portfolio state by hand

```bash
python - <<'PY'
from algotrade.config import settings
from algotrade.repositories import client
from algotrade.repositories.cash import CashRepository
from algotrade.repositories.holdings import HoldingRepository
from algotrade.repositories.snapshots import SnapshotRepository
s = settings.load(); db = client.connect(s.mongodb_uri, database=s.mongodb_database)
pid = s.default_portfolio_id
snap = SnapshotRepository(db).latest(pid)
print("NAV      ", snap.nav)
print("cash     ", CashRepository(db).balance(pid), "(ledger)")
print("holdings ", len(HoldingRepository(db).book(pid)))
print("unit NAV ", snap.unit_nav, "x", snap.units, "=", snap.unit_nav * snap.units)
PY
```

`unit_nav × units` must equal NAV. If it does not, the reconciler will already
have said so.

---

## When something is wrong

| Symptom | First command |
|---|---|
| No Telegram for 2 days | `python -m algotrade.acceptance.status` |
| `Reconcile FAIL` | `python -m jobs.value_portfolio --reconcile-only` |
| Stale data | `python -m jobs.ingest_session --check` |
| Rebalance never arrived | `python -m jobs.generate_recommendations --check-only` |
| Everything silent | Check GitHub Actions is enabled; the repo may have gone inactive |

Full procedures: [disaster-recovery.md](disaster-recovery.md).

---

## What this system will never do

Deliberate, not missing:

- **Place an order.** You trade; you report back. Enforced mechanically, not
  promised: `tests/unit/test_brokers_boundary.py` fails the build if anything
  submits an order, and its allow-list is empty. *(The
  [broker design](../superpowers/specs/2026-09-05-broker-integration-design.md)
  step 4 would change this to "no order without an explicit human click" —
  one click per trade, no batch submit. Nothing of it is built, and this line
  stays true until it is.)*
- **Sell because a price fell.** There are no stop losses; that is not the
  validated strategy.
- **Trade between monthly rebalances.** The rank buffer exists so drift is
  held, not corrected.
- **Tell you to sell during a drawdown.** §11p priced quitting during an
  underperformance streak at 27–40% of terminal wealth. The daily message
  shows drawdown *and* the historical worst streak (1,203 days) so a bad
  stretch reads as normal rather than as breakage.
