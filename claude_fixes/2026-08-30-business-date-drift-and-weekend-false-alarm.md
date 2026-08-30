# Business-date drift + weekend false alarm — 2026-08-30

Follow-up analysis of a second healthchecks.io/Telegram digest, three days
after the 08-27 fixes. Both of those held. Two new defects surfaced, and the
acceptance gate closed on evidence it should not have.

## First: the 08-27 fixes are confirmed working in production

- No `[WARN] market_data` on the 08-28 or 08-29 watchdog runs. The staleness
  check no longer fires structurally every morning.
- No `Started` GET pings at ~04:3x UTC after 08-26 in the healthchecks event
  log — `healthcheck.yml` has stopped touching a heartbeat it does not own.

## Root cause behind both new defects: GitHub scheduling delay exploded

Scheduled runs were firing ~35 min late through 08-26. From 08-27 they are
**9–12 hours** late:

| Workflow | Scheduled | 08-24 | 08-26 | 08-27 | 08-28 |
|---|---|---|---|---|---|
| `daily.yml` | 14:30 UTC | +39 m | +88 m | **+9 h 21 m** | **+9 h 06 m** |
| `healthcheck.yml` | 04:00 UTC | +38 m | +32 m | **+10 h 54 m** | **+11 h 49 m** |

Nothing in this repository causes that — it is GitHub free-tier scheduled
dispatch under load. But two things downstream assumed it would not happen.

## Bug 1 — a run delayed past IST midnight labels the wrong session

`daily.yml` resolved its business date with `TZ=Asia/Kolkata date +%F`, and
`jobs/ingest_session.py` defaulted to `clock.SystemClock().today()`. Both mean
"the wall-clock IST day", which silently rolls forward at IST midnight.

| Run fired (UTC) | = IST | Recorded `business_date` | Should have been |
|---|---|---|---|
| 08-27 23:51 | 08-28 05:21 | `2026-08-28` | `2026-08-27` |
| 08-28 23:37 | 08-29 05:06 | `2026-08-29` **(a Saturday)** | `2026-08-28` |

Verified directly against `job_runs`: there is **no** `ingest_session` success
for `2026-08-27` at all, and there **is** one for Saturday `2026-08-29`, a day
the exchange was shut and for which no session can ever exist.

**The data is correct.** Ingestion is gap-driven, so `--through 2026-08-29`
still fetched every missing weekday; `portfolio_snapshots` holds 08-27 and
08-28 with no Saturday row, and reconciliation passes. Only the bookkeeping
lied — which is exactly what the watchdog reads, producing the new
`[WARN] daily_job: 1 session without a success` on 08-28 and 08-29. Another
false alarm, from a different direction than the one fixed on 08-27.

**Fix:** `clock.session_date(now)` — before NSE's ~18:00 IST publish hour, or
on a weekend, the session a run is for is the previous trading day. Now the
single definition, used by both `ingest_session.py` and `daily.yml` (the shell
`date` call is gone). Seven tests pin it, two reproducing the exact incidents
above, plus a property test that it never returns a weekend.

The stale `job_run` rows are **left as they are**. They are an append-only
audit record of what was believed at the time; rewriting them to make a
warning go away is the opposite of the point. The 08-27 gap ages out of the
watchdog's 10-day window around 09-10.

## Bug 2 — the dead-man switch alerts every weekend (OPEN)

`HEALTHCHECKS_PING_URL`'s check is configured *period 1 day, grace 6 hours* →
alerts 26 h after the last success. `daily.yml` runs Mon–Fri, so Friday's
success to Monday's is **~72 h**. 26 < 72, so the check goes down every
weekend on a perfectly healthy system.

Confirmed: last success Fri 08-28 23:37 UTC → `up ➔ down` Sun 08-30 01:37 UTC,
exactly 26 h later. It will stay down until Monday's run.

Worse, both docs asserted the opposite. `health.py` claimed 26 h was
"deliberately longer than a weekend gap between Friday and Monday" — off by a
factor of ~3 — and `github-actions.md` claimed cron mode "would also alert on
weekends", when cron mode is exactly the fix, because it waits for the next
*scheduled* firing rather than counting elapsed hours.

**Fix (docs corrected; the setting itself is NOT yet changed):** switch the
check to cron mode, `30 14 * * 1-5`, UTC, grace **12 h** — 12 rather than 6 so
it also absorbs the 9–12 h delays above. This is a healthchecks.io UI change
and cannot be made from this repository.

## The acceptance gate closed on hollow evidence

`acceptance.status` now reads `11 / 10` sessions and prints
**READY FOR PAPER CAPITAL**. Ten criteria are genuinely met. Criterion 5 is
not: `verify_recovery` still reports the same 2026-08-23 injected ledger entry
for `value_portfolio 2026-08-19`, from before deployment. The note added to
`10-session-gate.md` on 08-27 — "do the real injection before session 10, or
the gate closes on hollow evidence" — was not acted on, and it has.

The verdict logic is behaving correctly; its input is a simulation. Recorded
prominently in `docs/acceptance/10-session-gate.md` rather than quietly fixed,
because a gate that can be passed without noticing is worth less than no gate.

## Bug 3 — paper workflows not given the secrets their own config check demands

Reported separately the same morning: `🔴 BACKUP FAILED`, the first-ever
scheduled `weekly-backup.yml` run (03:00 UTC Sunday, fired 08:51 — another
5 h 51 m delay).

It did not fail in the backup. It failed in its **first step**:

```
[FAIL] HEALTHCHECKS_PING_URL: not set
FAIL -- 1 error(s), 0 warning(s)
```

`weekly-backup.yml` runs `python -m algotrade.check_config --no-network`, and
`validation._check_healthchecks` treats a missing `HEALTHCHECKS_PING_URL` as a
hard **error** whenever the environment is `paper` or `prod` — but the
workflow's `env:` block never passed that secret through. Nothing about the
backup was broken; it was never reached.

Audited all five workflows for the same shape:

| Workflow | Runs `check_config` | Passes `HEALTHCHECKS_PING_URL` |
|---|---|---|
| `daily.yml` | yes | yes |
| `healthcheck.yml` | yes | yes |
| `rebalance.yml` | yes | yes |
| `weekly-backup.yml` | yes | **no — failed today** |
| `record-execution.yml` | yes | **no — latent** |

`record-execution.yml` is `workflow_dispatch`-only and had therefore never
run. It would have failed the **first time a real fill was recorded** — the
single worst moment for a config error, with a filled order at the broker and
no way to get it into the book.

**Fix:** supply the secret in both, rather than relax the check. The dead-man
switch is an *environment-level* requirement ("this deployment has one"), not
a per-job one, which is why the three workflows that never ping it still
correctly demand it. `--no-network` means neither workflow pings anything, so
there is no risk of repeating the 08-27 heartbeat-collision bug.

**Guard:** `tests/unit/test_workflow_secrets.py`. Static, over the YAML,
because the failure mode is a workflow nobody has run — nothing dynamic can
catch it. Its companion test derives the required set by *running* the
validator against an empty paper configuration rather than regexing the
source, so a newly-required setting fails in CI rather than at 03:00 on a
Sunday. It immediately earned itself by catching `DEFAULT_PORTFOLIO_ID`
missing from the hand-written list. Verified to fail when the original bug is
reintroduced.

**Confirmed fixed:** re-ran `weekly-backup.yml` — success. All 11 collections,
187 documents, 34 KB, *verified against the live database*, uploaded to the
permanent `backups` release
(`algotrade-backup-algotrade_paper-20260830.json.gz`). Gate criterion 9 is now
genuinely met.

## Changed

| file | change |
|---|---|
| `algotrade/platform/clock.py` | `PUBLISH_HOUR_IST`, `session_date(now)` |
| `.github/workflows/weekly-backup.yml` | passes `HEALTHCHECKS_PING_URL` |
| `.github/workflows/record-execution.yml` | passes `HEALTHCHECKS_PING_URL` (latent failure) |
| `tests/unit/test_workflow_secrets.py` | **new** — static guard, +7 tests |
| `jobs/ingest_session.py` | defaults `--through` to `session_date`, not `today()` |
| `.github/workflows/daily.yml` | business date from `clock.session_date`; shell `date` removed |
| `algotrade/platform/health.py` | docstring: corrected the false weekend claim, specifies cron mode + 12 h grace |
| `docs/deployment/github-actions.md` | §4 healthchecks table corrected, with why the old advice was backwards |
| `docs/acceptance/10-session-gate.md` | records that the gate passed on simulated self-healing; table of the four monitoring defects |
| `tests/unit/test_platform_clock.py` | +7 tests (both incidents, publish-hour boundary, weekend, Monday dawn, property) |

`pytest tests/unit` — 483 pass.

## Still open

1. **healthchecks.io check → cron mode, 12 h grace.** UI change. Until then, a
   false "down" every Saturday.
2. **A real failure injection** for criterion 5, per `10-session-gate.md`.
3. **Consider moving the crons off `:00`/`:30`** (e.g. `37 14 * * 1-5`).
   Scheduled dispatch queues hardest on the hour and half-hour; an odd minute
   is the standard mitigation. Unproven here — worth trying, not a certainty.

---

## Bug 4 — criterion 5 measured something the system cannot produce

Asked to break `MONGODB_URI` so a scheduled run would fail and criterion 5
could finally be earned. Checked first whether that could produce the
evidence. It cannot, and the investigation found the criterion itself broken.

### Why breaking the Mongo URI can never work

Reproduced locally with a corrupted URI: `check_config` reports
`[FAIL] mongodb: bad auth` and exits 1. That is `daily.yml`'s **first step**,
before `ingest_session` runs. And `job_runs` — the ledger `verify_recovery`
reads — lives in the database just disconnected, so **no `failed` row can be
written**. The result is a real workflow failure, a real Telegram alert, a
real `/fail` ping, and zero evidence.

### Three defects behind the false PASS

1. **The failures were mostly unrecordable.** Only `ingest_session` wrote a
   `failed` row, and only for a fetch failure. `generate_signals` claimed the
   ledger *only when `failures == 0`*. `value_portfolio` declared `JOB_NAME`
   from the start and **never wrote to the ledger at all**.

2. **Same-date attempts never happen naturally.** `analyse()` requires a
   failed *and* a succeeded attempt for one `business_date`, but each daily
   run claims a *new* date — real self-healing fills yesterday's gap under
   today's date. Proof from the live ledger: `2026-08-21` has a snapshot (its
   data was backfilled unaided on the 24th after the deployment gap) and
   **zero `job_runs`**. The system had already self-healed for real, and the
   checker could not see it.

3. **The only qualifying pair was fabricated.** `value_portfolio:2026-08-19:1`
   (failed) and `:2` (success) were the sole failed/succeeded pair — for a job
   that writes no rows, so they could only have been hand-injected. Every
   other row in the ledger is `attempt=1`; nothing live has ever produced an
   `attempt=2`.

Separately, the docstring claimed to verify "a later run succeeded **with no
manual job in between**" while `analyse()` checked no such thing — so a repair
driven by pressing *Run workflow* satisfied it.

### Fixes

| Area | Change |
|---|---|
| `jobs/generate_signals.py` | claims per business date *before* the work; records `failed` on `SignalUnavailable` |
| `jobs/value_portfolio.py` | writes to the ledger at all, per date, failing loudly |
| `repositories/jobs.py` | `claim(trigger=...)` stamps the GitHub event |
| `settings.py` | `github_event_name`, `fault_inject` |
| `verify_recovery.py` | excludes `synthetic`; requires `trigger == "schedule"`; requires gap-filled **and** unaided on the *same* recovery, not `any()` across different ones |
| `acceptance/status.py` | its own laxer duplicate of this logic brought in line |
| Atlas | the two 2026-08-19 rows marked `synthetic` with a note, not deleted |

Because `--backfill` revisits earlier dates, per-date claiming is what finally
makes a natural same-date `attempt=2` possible. That was the missing link: the
verifier was not merely strict, it was asking for something the jobs were
incapable of writing.

### The injector

Nothing else produces a real failure on demand — the Mongo route records
nothing, and deleting session files is repaired silently by an ingest that
never fails. So `ALGOTRADE_FAULT_INJECT=generate_signals` raises inside the
loop, *after* the ledger row exists, so the failure travels the genuine path:
recorded, alerted, heartbeat-pinged, and left for the next scheduled backfill.
Refused outright in prod (tested), and warns `this run is MEANT to fail` on
every run while armed, because an injector left on looks exactly like a real
recurring fault.

Verified locally under `--dry-run`: fires, writes nothing.

### Result

`acceptance.status` moved from **`READY FOR PAPER CAPITAL`** to
**`WARN -- session count reached but self-healing has not been demonstrated`**,
and `verify_recovery` from `SELF-HEALING DEMONSTRATED` to
`NOT YET DEMONSTRATED`. Both are now true.

+13 tests (9 for the verifier, 4 for the injector's safety). 496 pass.
