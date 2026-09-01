# Failure injection fired — 2026-09-01

The deliberate failure armed on 08-30 fired on the 08-31 scheduled run. This
records what was verified, and is explicit about what is **not yet** proved.

## Status: DONE — `SELF-HEALING DEMONSTRATED`

| Half | State |
|---|---|
| A real run failed, recorded, alerted | **Verified** 08-31 |
| A later scheduled run repaired it unaided | **Verified** 09-01 |

The 09-01 run fired at 17:54 UTC — 3 h 24 m past its 14:30 schedule — and did
exactly what was predicted below, unprompted:

```
generate_signals:2026-08-31:1   failed    trigger='schedule'
generate_signals:2026-08-31:2   success   trigger='schedule'
```

Read from `job_runs` directly rather than taken from the checker, because the
whole lesson of this week is that the checker was the thing that was wrong.
Snapshot ₹2,00,820 and signal `momentum_v1:2026-08-31` both exist, so the gap
was filled rather than merely re-attempted, and reconciliation passes.

`acceptance.status`: **`PASS -- 13 sessions, reconciliation clean, self-healing
demonstrated`**. The verdict string is identical to the one it printed on
08-30 on fabricated evidence; what changed is what stands behind it.

One honest caveat, recorded in the gate doc rather than argued away: criterion
2 ("zero manual intervention") is **not** met on a strict reading — the window
contains three `workflow_dispatch` runs, none of which repaired a failure, and
`acceptance.status` does not check that criterion at all.

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

---

# Dashboard deployed — 2026-09-01

Live at <https://algotrade-six-chi.vercel.app>, read-only. Verified:
unauthenticated requests 401, an authenticated page renders in ~840 ms cold,
and NAV / day P&L / position count match the Telegram report exactly.

## What the first deployment cost, and why none of it was guessable

| Problem | Cause | Fix |
|---|---|---|
| Build **blocked** before it started | The repo had a *local* `user.email` of `akash@localhost`; Vercel refuses an invalid commit author | Local override set to the account's real address. Global stays the work identity, which is correct for work repos |
| Build used **Python 3.12** | No version specified; local and CI are both 3.13 | `.python-version`. 3.13 is supported, just not the default — checked the docs rather than assuming |
| Would have **404'd every path** | A catch-all rewrite `/(.*) -> /api/index.py`. The build warned that rewrites now route using the *rewritten* path, so FastAPI would have seen `/api/index.py` for every request | Rewrite removed. Vercel's FastAPI preset already routes everything to the app; the entrypoint is declared with `tool.vercel.entrypoint` |
| Would have installed **polars, pandas, duckdb** | Dependencies resolve from the root `requirements.txt`, which is the trading system's and threatens the 500 MB bundle limit | Four real dependencies declared in `pyproject.toml`; the root file excluded from the upload |
| `Internal Server Error` on **every** path | `DASHBOARD_PASSWORD` was under the enforced 12-character minimum | See below — the message was the real bug |

## The failure that hid itself

The 500 was correct: a short password should refuse to boot. What was wrong is
that it said **nothing**, on every path including `/healthz` — a route that
reads no settings, opens no connection and returns a constant.

Two compounding defects, both mine:

* **The exception handler rendered a template that reads the settings.** When
  the settings were what had failed, the handler raised too and the platform's
  bare message won. A misconfiguration was indistinguishable from a crash —
  the precise opposite of what `settings.py`'s fail-fast validation exists for.
* **Auth was an app-level dependency**, so it guarded `/healthz` as well, and
  it reads the settings. The one route whose job is to answer "is the function
  alive?" could not answer exactly when that mattered.

Diagnosis took a Vercel log export to find `ConfigError: DASHBOARD_PASSWORD
must be at least 12 characters` in a traceback. It should have taken opening
the page.

Reproduced locally by unsetting the configuration, and fixed:

```
/healthz   500  ->  200 {"status":"ok"}
/          "Internal Server Error"  ->  "MONGODB_DATABASE is not set"
```

The config message names a variable, never a value; a test asserts no
connection string can appear in it.

**Also found:** the dashboard test suite was not hermetic. Settings fall back
to `dashboard/.env` when a variable is absent, so a test that deliberately
unset one silently read the developer's real local file — the suite would pass
or fail depending on whether that file existed. `ENV_FILE` is neutralised for
every test now.

## Still open

1. `/healthz` is still behind auth on the live deployment: it is running
   `6f0c934`, before the fix. Any push to `main` picks it up.
2. Writes are off (`DASHBOARD_ALLOW_WRITES` unset), which is where they should
   stay until the dashboard has been used for a while.
3. The dashboard password has been through a terminal transcript and should be
   rotated.


---

# Backup trigger for the daily job — 2026-09-01

## Why

GitHub's `schedule:` is best-effort and has been badly late all week: +9h21,
+9h06, +5h44, +3h24 on `daily.yml`; up to +11h49 on `healthcheck.yml`. A
20:00 IST report arriving at 05:00 IST is not useful, and the delay already
caused a correctness bug — runs crossing IST midnight claimed the wrong
business date until `clock.session_date` was written.

Vercel documents Hobby cron precision as **±59 minutes**, against GitHub's
unbounded delay. The `linkedin-job-search` service is already deployed and
already holds a GitHub token, and had solved this exact problem for its own
workflow, for the same stated reason.

## What

`daily.yml` now also accepts `repository_dispatch: types: [run-daily]`, fired
at 16:30 UTC — two hours after its own 14:30 schedule — from that service.
**`schedule:` stays primary**; this is a second chance.

| Side | Change |
|---|---|
| `linkedin-job-search/api/index.py` | `DISPATCH_TARGETS` table replacing one hardcoded repo; `?action=trigger-algotrade`; origin stamping |
| `linkedin-job-search/vercel.json` | second cron, `30 16 * * 1-5` |
| `algotrade/.github/workflows/daily.yml` | `repository_dispatch` trigger; passes `client_payload.source` as `ALGOTRADE_TRIGGER_SOURCE` |
| `algotrade` settings/jobs/repositories | `github_trigger_source` recorded as `trigger_source` on every `job_run` |
| `verify_recovery`, `acceptance/status` | a `repository_dispatch` counts as unaided **only** with the cron stamp |

## The subtlety worth recording

`verify_recovery` counts a repair as self-healing only when no human was
involved — it approximated that as `trigger == "schedule"`. A backup-triggered
repair arrives as `repository_dispatch`, which is **indistinguishable** from a
human curling the same endpoint.

So the dispatcher stamps its origin: `vercel-cron` only when Vercel's own cron
headers are present (`x-vercel-cron-schedule`, a `vercel-cron/` user agent),
`manual` otherwise. Only that stamp counts. A human calling the endpoint still
reads as aided; a cron stamp cannot rescue a `workflow_dispatch`.

The criterion became **more accurate, not looser** — but the change was made
*after* the gate closed on the stricter reading, deliberately. Loosening a
pre-registered criterion while its result is pending is the failure §8 warns
about, whatever the justification.

## Verified live, not asserted

A manual dispatch at 19:29 UTC, 95 minutes after that day's scheduled run had
already completed:

```
{"status": "dispatched", "repo": "algotrade", "event": "run-daily", "source": "manual"}
```

The run went green end to end and changed nothing:

```
Business date (session this run is for): 2026-09-01
nothing to do -- already current
signals already stored for 2026-08-26..2026-09-01; nothing to do
```

Afterwards — one snapshot, one signal, three `job_runs` rows, all from the
scheduled run, none from the dispatch; executions still 30. This also confirmed
the existing GitHub token can reach the `algotrade` repo, which was the one
assumption that could not be checked from the code.

## A wrong prediction, corrected

I stated that both triggers firing would send the **daily Telegram report
twice**, on the reasoning that dedupe was per-run. It was reported that no
second message arrived, and checking showed the claim was simply wrong:

```
INFO  notification daily_summary already sent for 2026-09-01
```

Dedupe is **persisted**, not per-run. `NotificationService.send` claims a
`notifications` document keyed `daily_summary:{portfolio}:{date}` *before* the
network call, precisely so a re-run cannot post a second summary. One row
exists for 09-01, status `sent`, despite two runs.

So duplicate runs are safer than claimed: **four** independent guards, not
three — the ingestion gap query, the stored-signal check,
`JobAlreadySucceeded` on the unique `(job_name, business_date)` index, and the
notification dedupe. Recorded because a confidently-wrong claim in a runbook
is worse than no claim: it teaches you to expect a symptom that would, if it
ever appeared, be a real fault.

## Untested

Vercel's cron actually firing on schedule. First real test: the next weekday
at ~16:30 UTC. The dispatch path itself is proven; only the timer is not.
