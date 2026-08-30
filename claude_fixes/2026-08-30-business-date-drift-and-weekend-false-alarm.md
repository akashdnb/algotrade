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

## Changed

| file | change |
|---|---|
| `algotrade/platform/clock.py` | `PUBLISH_HOUR_IST`, `session_date(now)` |
| `jobs/ingest_session.py` | defaults `--through` to `session_date`, not `today()` |
| `.github/workflows/daily.yml` | business date from `clock.session_date`; shell `date` removed |
| `algotrade/platform/health.py` | docstring: corrected the false weekend claim, specifies cron mode + 12 h grace |
| `docs/deployment/github-actions.md` | §4 healthchecks table corrected, with why the old advice was backwards |
| `docs/acceptance/10-session-gate.md` | records that the gate passed on simulated self-healing; table of the four monitoring defects |
| `tests/unit/test_platform_clock.py` | +7 tests (both incidents, publish-hour boundary, weekend, Monday dawn, property) |

`pytest tests/unit` — 476 pass.

## Still open

1. **healthchecks.io check → cron mode, 12 h grace.** UI change. Until then, a
   false "down" every Saturday.
2. **A real failure injection** for criterion 5, per `10-session-gate.md`.
3. **Consider moving the crons off `:00`/`:30`** (e.g. `37 14 * * 1-5`).
   Scheduled dispatch queues hardest on the hour and half-hour; an odd minute
   is the standard mitigation. Unproven here — worth trying, not a certainty.
