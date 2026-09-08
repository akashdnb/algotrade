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
