# Heartbeat flapping + daily staleness false alarms — 2026-08-27

Commissioned to explain a Telegram/healthchecks.io digest showing the
`algotrade-daily` check flipping down→up every single day, plus a `[WARN]
market_data: ... 1 trading day(s) behind` on every watchdog run since
deployment (2026-08-23). Both traced to real bugs, not infrastructure noise.

## Bug 1 — healthcheck.yml corrupts daily.yml's heartbeat

`algotrade/check_config.py`'s `check_healthchecks()` pings the shared
`HEALTHCHECKS_PING_URL`'s `/start` endpoint on every invocation, to prove the
URL is reachable. `check_config` (without `--no-network`) runs as the first
step of **both** `daily.yml` and `healthcheck.yml`.

`daily.yml` owns the heartbeat: it sends its own `/start`, then a `success`
ping with `sessions=/signals=/snapshots=` on completion. `healthcheck.yml`
has no such completion ping — it only ever touches the check via
`check_config`'s reachability probe. Every morning at 09:30 IST, that probe
put the shared check into "running" state with nothing to close the loop,
and healthchecks.io marked it down a couple of hours later — a false alarm
about `daily.yml`, produced by an unrelated workflow, every single day.

Confirmed against the healthchecks.io event log: `Started` (GET, from
`check_healthchecks`'s `python-requests` GET) at ~04:3x UTC (healthcheck.yml,
`0 4 * * *`), `up→down` ~2h later, then `Started`+`OK` (POST, with the
sessions/signals/snapshots body) at ~15:xx UTC when daily.yml actually ran
and closed it.

**Fix:** `check_healthchecks()` takes `skip_ping: bool`; `check_config.py`
gets a `--skip-healthchecks-ping` flag; `healthcheck.yml` passes it. The
watchdog still verifies Mongo, Telegram, schema, and storage — it just no
longer touches a heartbeat it doesn't own.

## Bug 2 — market_data staleness always WARNs before that day's ingestion

`jobs/watchdog.py::check_data_freshness` called
`service.missing_sessions(through=today)`. The watchdog runs at 09:30 IST;
`daily.yml` ingests that day's session at 20:00 IST. `missing_sessions`
treats every weekday up to and including `through` as required, so `today`
was *always* missing at 09:30 IST — a guaranteed WARN with `STALE_WARN_SESSIONS
= 1`, regardless of system health. `check_daily_job` in the same file already
excludes the latest date for exactly this reason ("today's run may
legitimately not have happened yet"); `check_data_freshness` never got the
same treatment.

**Fix:** check through `clock.previous_weekday(today)` instead of `today`.

## Verified correct (not a bug)

- The Aug 24 CRIT (`2 trading day(s) behind ... valuation BLOCKED`) and the
  "Watchdog itself failed" alert the same day: real, one-time — the system
  was deployed 2026-08-23 (Sunday), so Friday 2026-08-21's session was never
  ingested by anything. Self-healed the same day when `daily.yml` backfilled.
  Not related to either bug above.

## Changed

| file | change |
|---|---|
| `jobs/watchdog.py` | `check_data_freshness` checks through `clock.previous_weekday(today)`, not `today` |
| `algotrade/check_config.py` | `check_healthchecks(skip_ping=...)`; `--skip-healthchecks-ping` CLI flag |
| `.github/workflows/healthcheck.yml` | `check_config` step now passes `--skip-healthchecks-ping` |

No test files existed for either `jobs/watchdog.py` or `algotrade/check_config.py`
(both CLI entrypoints, consistent with the rest of `jobs/`). Verified with
`pytest tests/unit -q` (all pass, unaffected) and manual `--help` / compile
checks on the edited files.

Not yet pushed — next `daily.yml`/`healthcheck.yml` runs will show the fix
working once this lands on `main`.
