# Three mornings of an alert that named the wrong thing

**2026-09-08.** Telegram sent "Watchdog itself failed — the monitor is down.
Check Atlas connectivity and secrets" on 09-06, 09-07 and 09-08. Atlas answered
in 726 ms and both secrets authenticated on all three runs. The monitor was
working perfectly; it was the message that was wrong.

## What was actually broken

`check_config` exited 1 on a real finding:

```
[FAIL] mongodb.schema: 6 discrepancies vs schema.py; first: broker_sessions.ttl: index missing
         -> run: python -m jobs.init_db
```

All six belonged to the three collections the broker integration added:

| Collection | Missing |
|---|---|
| `broker_sessions` | `ttl` index, validator |
| `symbol_map` | `isin` index, `kite_symbol` index, validator |
| `submissions` | the collection itself |

`schema.py` declared them; `jobs.init_db` was never run against Atlas after
that commit. `broker_sessions` and `symbol_map` had been created implicitly by
their first insert — collection, no validator, no indexes. `submissions` was
never written to, so it did not exist at all.

One of those is functional, not cosmetic. The `ttl` index is what **deletes**
an expired access token; `expireAfterSeconds: 0` means "expire at the date in
this field". Without it, `sessions.live()` still refused to serve an expired
token — it checks `expires_at` — but the ciphertext sat in Atlas indefinitely
instead of being removed. The design intent is that an expired token is gone,
not merely filtered.

## The defect underneath it

`init_db` would have failed if run, and left the schema half-applied.
`symbol_map.isin` is declared unique among rows that have an ISIN, and the
live collection held a duplicate:

```
INE477B01010  ->  DTIL, DVL
```

Both rows were written the same day from the same Kite dump, so this was not a
stale row from a rename nobody noticed. Kite lists both as tradeable —
`DTIL-BE` (series BE) and `DVL` (series EQ) — sharing one ISIN.

`enrich()` merges ISINs keyed by **symbol** and never checked whether one ISIN
was claimed by two symbols. So **`schema.py` declared an invariant the
producing code did not enforce**, and the two had never met because `init_db`
had never run. The database was going to refuse what the code happily produced.

Checking that precondition before writing is what caught it. Applying the
schema first would have failed the unique index build partway through.

## Fixes

**1. `enrich()` withholds a contested ISIN** rather than guessing a winner. A
wrong ISIN is silent — it matches, just to the wrong instrument. Both rows
survive with their Kite mapping intact, and `demat.pair()` falls back to the
symbol, recording the weaker basis. The demat is the one thing that can settle
a contest: it reports *holdings*, so it names an instrument it actually holds.
`isin_conflicts()` reports what was withheld, and `build_symbol_map` logs it —
the identity question is real and must be resolved, not left invisible.

**2. Production data and schema.** Cleared the ISIN on DTIL and DVL to exactly
what the fixed code now produces, then ran `init_db`: 14 collections match
`schema.py`, `broker_sessions.ttl` live with `expireAfterSeconds=0`.

**3. The alert quotes the finding.** A check that *finds* something is the
watchdog working, and needs a different message from a watchdog that could not
run. The three steps now tee to one log; the handler quotes the `[FAIL]`/
`[CRIT]` lines and the `-> run: ...` remedy that follows them. It claims the
monitor is down only when there is no finding to quote. The speculative
"check Atlas connectivity and secrets" is gone — it named the two things that
were fine.

What should have arrived on Saturday:

```
🔴 Watchdog found a problem

configuration and connectivity reported:

• [FAIL] mongodb.schema: 6 discrepancies vs schema.py; first: broker_sessions.ttl: index missing
• -> run: python -m jobs.init_db

The monitor is working. This is what it found.
```

## A second bug, found by the fix

The dashboard suite went from 199 passing to 2 failing while this was in
progress, and the two failures were nothing to do with the change:
`ServerSelectionTimeoutError` against `localhost:27017`.

Route modules do `from app.db import get_db`, which binds their own name, so
patching `app.db.get_db` alone leaves an already-imported route calling the
real connector. The `client` fixture enumerated **eight** modules;
`app/routes/` had grown to **eleven**. `/rebalances`, `/audit` and `/history`
were reaching a live mongod, and `client_disabled` patched none of them.

They passed only because a local mongod was listening — left over from the
rehearsal work. The suite was one `brew services stop` from failing for a
reason unrelated to any change, which is the same accident the `ENV_FILE`
override in that conftest already exists to prevent.

- `_bind_db()` walks `app/routes/` instead of listing it, so it cannot drift.
- Test `MONGODB_URI` is now `mongodb://240.0.0.1:27017/` — reserved and
  unroutable. An escaping route must fail on every machine, not connect on the
  developer's and fail in CI.
- A guard test asserts the walk covers the package.

Suite time fell from 12.71s to 1.97s: nothing waits on a Mongo timeout now.
Mutation-tested by re-excluding `rebalances` — the guard names it.

## Verified

- `check_config`: `OK -- 2 warning(s), no errors`
- All three healthcheck steps exit 0, no findings in the log
- Both alert branches rendered from the code as it ships, using Saturday's log
- 1,366 root tests (41 skipped), 200 dashboard tests

---

## Addendum: the same drift took out Monday's session, and both alarms

Asked why the book was two sessions behind. Same single cause.

`daily.yml` step 6 is **Validate configuration** — `check_config`, the same
command the watchdog runs first. On Monday 2026-09-07 it exited 1 on the schema
drift, so every later step was skipped: `Resolve business date`,
`Ingest missing sessions`, `Generate signals`, `Value portfolios`.

Timeline — the collections were declared on **2026-09-05**, a Saturday:

| Date | | What happened |
|---|---|---|
| Fri 09-04 | daily | succeeded — schema declared the next day |
| Sat/Sun | — | no daily run |
| Sat–Tue | healthcheck ×3 | failed at step 1, "monitor is down" |
| Mon 09-07 | daily ×2 | failed at step 6; **no ingest** |
| Tue 09-08 | daily | not yet due (17:03 UTC) |

So: Monday missing, Tuesday not yet run, book at Friday. Two sessions.

**Neither alarm reported it, for two different reasons.**

*The daily alert crashed.* `Alert on failure` interpolated
`${{ steps.bd.outputs.date }}` into Python source. That step had been skipped,
so it evaluated to `date.fromisoformat("")` → `ValueError`, and
`continue-on-error: true` swallowed it. **The run that broke the ingest sent no
notification at all.** An alert must not depend on a step that only runs when
there is nothing to alert about.

*The watchdog never got that far.* Its freshness check is step 3; it died in
step 1 on the same drift. Today's run, once fixed, reported immediately:

```
[WARN] market_data: last session 2026-09-04, 1 trading day(s) behind
[WARN] daily_job:   1 session without a success (last: 2026-09-04)
```

Those two warnings were waiting behind the schema failure for three days. The
drift broke the job, then broke both independent paths that existed to tell me
the job had broken — and the only message that did arrive named Atlas and the
secrets, which were fine.

### Fixes

- **`Resolve business date` now runs before `Validate configuration`.** The
  alert's input is established before anything that can fail.
- **The date arrives via `env:`, not interpolation**, and falls back to
  `clock.session_date` if empty. Both paths verified by extracting the block
  from the shipped YAML and running it.
- **A guard test** asserts no step guarded by `failure()`/`always()`
  interpolates `steps.*.outputs.*` into inline Python. Mutation-tested by
  reintroducing Monday's exact line; it names the file and the expression.

### Node 20 deprecation

All 17 action references bumped: `checkout@v4→v5`, `setup-python@v5→v7`,
`cache@v4→v6`, `upload-artifact@v4→v7`. All four target `node24`; the runner is
2.337.0, above the 2.327.1 minimum each requires.

`checkout` stops at **v5**, not v7, deliberately. v6 changed credential
persistence ("persist creds to a separate file"), and `daily.yml` pushes
`data/sessions` back to the repo using exactly that mechanism. v5 is already
node24, so it clears the deprecation without touching the push path.
`setup-python@v7` removes the `pip-install` input, which is unused here.

### Left alone, reported

`record-execution.yml` interpolates `${{ inputs.symbol }}`, `inputs.price`,
`inputs.note` and others straight into Python string literals and shell
arguments. A value containing a quote executes as code, in a job holding
`MONGODB_URI` and the bot token. Not today's bug and not a crash — a separate
injection finding in the money path, needing ~20 arguments moved to `env:` and
a real dispatch to verify. Raised rather than bundled.

`weekly-backup.yml` interpolates `steps.backup.outputs.*` under `if: success()`,
so the step has always run and the values are our own filenames. Same pattern,
no exposure.

---

## Addendum 2: catching the sessions up surfaced two more things

Triggering the catch-up did not work first time.

### The archive cache was a 192-byte lie

`Assert the archive actually restored` failed: **0 files, expected ~4,000**.

The `market-archive-v1` cache written on **2026-08-23** is 192 bytes — an empty
directory marker — and it *hits*. So `Download archive on cache miss`, gated on
`cache-hit != 'true'`, was skipped, and the assertion refused to build a
truncated panel. Correct refusal; useless gate.

Already written up in
[`2026-09-04-archive-release-missing.md`](2026-09-04-archive-release-missing.md),
which published the 247 MB release and added the assertion — but never deleted
the stale cache. It stayed hidden because that day's daily run happened *before*
the assertion was added, and Monday's died at config validation before reaching
it. **Today's run is the first ever to execute that step.**

`cache-hit` answers "was there an entry", not "is the archive usable". The
download is now gated on counting the CSVs, so an empty or partial hit
re-downloads instead of failing the run; an unset count reads as 0 and
downloads, which is the safe direction. Stale cache deleted so the post-job
save could write a real one — it did, 267.68 MiB, so runs stop re-downloading.

One self-inflicted round trip in between: `find data/archive` exits 1 when the
path does not exist and `-o pipefail` turned that into a failed step, so the
count step failed on exactly the empty-archive case it was added for. `mkdir -p`
first.

### The restated NAV is the HFCL fix landing

The catch-up ran with the default `backfill: 5`, so 2026-09-01 through 09-07
were revalued. The Sep-04 snapshot moved:

```
reported on 09-04:  NAV 1,92,276      (inception -3.86%)
stored now:         NAV 1,99,064.19   (inception -0.47%)
difference:            6,788.19
HFCL value:            6,788.10   <- 30 @ 226.27
```

The gap **is** HFCL, to the paisa. Sep-04 was valued with HFCL at ₹0 — the
`HFCL` / `HFCL-BE` mapping bug — so NAV was understated by exactly one
position's market value. Today's revaluation prices it correctly. No position
in any snapshot is valued at zero now.

So the restatement is the earlier fix propagating into stored history, not a
regression. Worth stating plainly because a 3.4% NAV move on a settled date
looks alarming until you know which of the two numbers was wrong.

### Verified

```
last stored session: 2026-09-07     missing through 2026-09-07: 0
latest snapshot:     2026-09-07     NAV 201,499.28
latest signal:       2026-09-07
PASS  last signal       2026-09-07
PASS  data freshness    signal 2026-09-07, 1 session(s) behind
archive:             4,081 session files
cache saved:         market-archive-v1, 267.68 MiB
```

"1 session behind" is correct at 16:00 IST: Tuesday's bhavcopy is not published
until after close, so `clock.session_date` targets Monday. Tonight's scheduled
run takes Tuesday.

Every step of `daily.yml` green, on `checkout@v5` and `setup-python@v7`.
