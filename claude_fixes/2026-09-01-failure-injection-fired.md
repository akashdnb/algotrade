# Failure injection fired — 2026-09-01

The deliberate failure armed on 08-30 fired on the 08-31 scheduled run. This
records what was verified, and is explicit about what is **not yet** proved.

## Status: half done

| Half | State |
|---|---|
| A real run failed, recorded, alerted | **Verified** |
| A later scheduled run repaired it unaided | **Pending** — expected on the 09-01 run |

`verify_recovery` currently reports `FAILURES NOT RECOVERED`. That is the
correct reading of a genuine failure that has not yet been repaired, and it is
the first time this command has said anything true about a real event.

## What happened

Run [33434995161](https://github.com/akashdnb/algotrade/actions/runs/33434995161),
`event: schedule` — not a manual dispatch. Scheduled 14:30 UTC, fired 20:14
UTC (5 h 44 m late, consistent with the delays since 08-27).

It failed at exactly the intended step, and nowhere else:

```
success  Ingest missing sessions      <- data still arrived
success  Commit new sessions
failure  Generate signals             <- the injected fault
success  Heartbeat failure            <- /fail ping fired
success  Alert on failure             <- Telegram alert fired
```

The armed warning behaved as designed, so the run was never ambiguous:

```
[WARN] ALGOTRADE_FAULT_INJECT: ARMED for generate_signals -- this run is MEANT to fail
  2026-08-31  FAILED: fault injection armed for generate_signals
0 signal(s) stored, 1 failed
```

## The evidence that was previously impossible

```
generate_signals:2026-08-31:1   status=failed   trigger='schedule'
    error: fault injection armed for generate_signals (ALGOTRADE_FAULT_INJECT)
ingest_session:2026-08-31:1     status=success  trigger='schedule'
```

That `failed` row is the whole point. Before 08-30, `generate_signals` wrote
to the ledger **only when nothing had failed**, so this row could not have
existed no matter what went wrong. The `trigger` field did not exist either.

No snapshot for 2026-08-31: the gap is real and currently unfilled, which is
what makes the pending recovery a genuine test rather than a formality.

## Incidental: `session_date` verified in production

The run fired at 20:14 UTC — **past IST midnight** — and recorded
`ingest_session:2026-08-31`, not `2026-09-01`. Under the pre-08-30 code this
is precisely the case that mislabelled the business date. The fix is confirmed
against the exact condition that produced the original bug, in production,
rather than only in tests.

## Disarming

Deleted 09-01 10:35 UTC, ~4 hours before that day's scheduled run:

```
gh variable delete ALGOTRADE_FAULT_INJECT --env paper
```

Left armed, the 09-01 run would have failed too, and a second consecutive
failure is exactly what a real recurring fault looks like — the confusion the
loud `ARMED` warning exists to prevent.

## What the 09-01 run should do

Unattended, with nothing dispatched by hand:

1. ingest 09-01
2. `generate_signals --backfill 5` finds 08-31 still has no stored signal,
   claims **attempt 2** for it, and succeeds with `trigger='schedule'`
3. `value_portfolio --backfill 5` writes the missing 08-31 snapshot
4. `verify_recovery` → all four conditions met for 2026-08-31

If instead it reports `REPAIRED, BUT NOT UNAIDED`, something dispatched the
repair by hand and the exercise has to be redone. If it still reports
`FAILURES NOT RECOVERED`, the backfill window did not reach back far enough
and that is a real finding about the recovery mechanism, not about the test.

**Nothing is being touched until then.** Intervening would invalidate the one
property being measured.
