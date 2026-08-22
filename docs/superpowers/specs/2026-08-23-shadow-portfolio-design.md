# Shadow Portfolio v1 — Signal Parity + Paper P&L

**Status:** Approved for planning, 2026-08-23
**Context:** PROJECT.md §9 (Human-in-the-Loop Design) and §12 ("shadow-portfolio agreement" —
non-negotiable before real capital, §11o item 3c). This spec covers the subset of §9 that
blocks Phase 0d (₹25k live), not the full human-in-the-loop system.

## Purpose

The strategy is decided (50/50 Momentum/Low-Vol, §11o). Nothing about *edge* is still in
question — only whether the pipeline that will run unattended for months produces the same
answer a from-scratch backtest would, every single day, without a human checking by hand.

§9 names the two things forward simulation can actually validate (edge cannot be validated
this way — 3–5 years of data would be needed for statistical confidence, §9):

1. **Plumbing** — does the daily pipeline run without breaking?
2. **Signal parity** — does today's live signal match what the backtester computes for today?

This spec delivers exactly those two, plus a paper P&L trail, and nothing else.

## Non-goals (explicitly deferred to full §9 / Phase 0d)

- No UI, no accept/decline workflow — there is no human decision to log yet.
- No `live` portfolio_id, no real orders, no broker API integration — that is Phase 0d.
- No risk circuit breakers — meaningful only once real capital is at stake.
- No `variant_a` sensitivity portfolio or weekly regression-decay job — separate concern.
- No shared `portfolio_id`-keyed database — premature until `live` and `variant_a` actually
  need to coexist with `shadow_full`.

## Architecture

**No new signal-generation logic.** The nightly job re-invokes the exact functions already
used for research, on the panel extended through today:

```
universe.liquidity(panel) → universe.membership(...)
  → features.add(liq, "momentum", lookback=252, skip=21)
  → strategies.restrict_to_universe(elig, members)
  → buffering.buffered_long_only(elig, entry=30, exit_=60, cadence="monthly")
  → engine.run(weights=w, panel=panel, ..., trade_dates=engine.implied_trade_dates(w))
```

This is the same call sequence as `scripts/remeasure_finalists.py`. There is deliberately no
second implementation of the signal to drift out of sync with the first — "does live match
the backtester" is true by construction, not by a parity layer bolted on afterward.

What *is* new: re-running this **every night on a growing panel**, checking that history
doesn't retroactively change, and keeping a paper ledger. Three new pieces:

- `algotrade/shadow/parity.py` — diffs tonight's recomputed weight history against
  yesterday's persisted one.
- `algotrade/shadow/ledger.py` — builds the paper equity curves (`shadow_full`, `benchmark`).
- `algotrade/shadow/store.py` — read/write for `data/shadow/*`, mirroring the conventions
  in `algotrade/io/store.py` (manifest-per-write, partitioned where it matters).
- `scripts/shadow_run.py` — thin CLI orchestrating the above, following the existing
  pattern where scripts are wrappers around `algotrade/*` logic, not where logic lives.

## Data flow, per nightly run

1. **Determine the target session.** Today's date, IST. If it's a weekend, skip immediately
   — no fetch attempted.
2. **Fetch.** Reuse `algotrade.io.nse` (already handles NSE's CSV-that's-actually-XLSX
   quirk, retries, and caching-by-writing-to-disk) to pull the session's bhavcopy. If NSE
   has no file for the date (a trading holiday `nsefin`'s calendar can't be trusted to
   report, §10.9), that 404/empty-response **is** the holiday signal — record it as
   "skipped: no data" in `run_log.jsonl` and stop. No separate holiday calendar needed,
   consistent with §10.8's finding that data shape, not an external calendar, is what's
   trustworthy.
3. **Extend the curated panel and rebuild the adjusted panel** — same functions
   `build_panel.py` already calls, just invoked incrementally instead of once.
4. **Recompute weights for the full history** (inception → today), not just tonight's row.
   Cheap: existing diagnostic scripts already do this over 2011–2026 in seconds.
5. **Parity check** (`shadow/parity.py`): diff tonight's weight history against the
   persisted `weights_log` from the previous run, **for every date strictly before the new
   session** (the new tail is expected to be new — nothing to diff it against). A
   `(date, symbol, weight)` row is a discrepancy if it appears in one run and not the other,
   or its weight differs beyond a small float tolerance. Any discrepancy on an
   already-decided date is either a deliberate code fix (must be identified and its reason
   logged) or a silent regression — the exact failure class the third and fourth audits
   found repeatedly in the research code. **This is what "signal parity" concretely means
   here**: not live-vs-backtest (they're the same function), but tonight-vs-last-night, which
   catches the code from silently changing its own past answers.
6. **Persist tonight's weights as the new baseline regardless of step 5's outcome.**
   Otherwise a deliberate, explained fix would re-trigger the same "discrepancy" every night
   forever. The discrepancy record itself is permanent (append-only `discrepancies.jsonl`,
   never overwritten) — the running baseline just moves forward. This is a deliberate
   trade-off, not an oversight: it means a silent regression could compound for more than
   one night if the alert is missed, which is why step 7 needs to be loud, not just logged.
7. **Alert on any discrepancy or fetch/build failure.** Silent failure defeats the entire
   point of this system — the historical pattern (§11q, three prior audits) is that these
   bugs are found by someone reading output and noticing it looks wrong, which won't happen
   if nothing surfaces the run's outcome. Mechanism: **open question, see below.**
8. **Build the paper ledger** (`shadow/ledger.py`): `engine.run` on the recomputed weight
   history → `shadow_full`'s equity curve (every recommendation taken blindly — no
   discretion layer exists yet to diverge from it). Separately, pull
   `indices.benchmark_series` over the same window → `benchmark`'s curve. Persist both to
   `data/shadow/equity.parquet`.

## Storage layout

```
data/shadow/
  weights_log.parquet       # (date, symbol, weight) — full history, overwritten each run
  weights_log_manifest.json # built_at, source commit hash, session count
  equity.parquet            # (date, shadow_full_nav, benchmark_nav, gross_return, net_return)
  discrepancies.jsonl       # append-only, one record per discrepancy ever found
  run_log.jsonl             # append-only, one record per nightly run (status, session, notes)
```

No SQLite/DuckDB. This mirrors the existing `io/store.py` pattern (Parquet + JSON manifest)
rather than introducing a new persistence technology for one job. A shared `portfolio_id`
schema (§9) is deferred until `live` and `variant_a` exist and actually need to share a
table with `shadow_full` — building it now would be speculative.

Each `run_log.jsonl` and `discrepancies.jsonl` record includes `git rev-parse HEAD` at run
time, so a discrepancy can be correlated to the commit that caused it later.

## Scheduling

`launchd`, not `cron` — handles a sleeping laptop better (runs on next wake rather than
silently skipping the fire time). One `LaunchAgent` plist,
`~/Library/LaunchAgents/com.akashranjan.algotrade.shadow.plist`, with a
`StartCalendarInterval` in the evening (exact hour TBD against when NSE's bhavcopy is
actually published — needs one week of manual observation before picking a time; starting
guess 20:00 IST with the script's own retry covering earlier misses).

The script is idempotent: if `data/curated`'s last session is already today's, the fetch
step is a no-op and the run just re-verifies. This means a missed firing (laptop off, no
network) self-heals on the next run — it catches up on however many sessions are missing,
the same multi-day capability `backfill_sessions.py` already has, rather than needing a
separate watchdog process.

## Error handling summary

| Condition | Handling |
|---|---|
| Weekend | Skip before attempting fetch |
| NSE has no file for the date (holiday) | Record "skipped: no data", not an error |
| Fetch fails (network, NSE 403/500) | Reuse `nse.py`'s existing retry/backoff; if still failing, log failure, alert, leave curated panel untouched — next run catches up |
| Data fetched but fails integrity checks | Reuse `algotrade.core.integrity`; treat as a failed run, do not extend the panel, alert |
| Parity discrepancy found | Log permanently, alert, **still advance the baseline** (see step 6 above — explicit trade-off) |
| Job doesn't fire at all for >1 session | Self-heals next run via multi-day catch-up; no separate watchdog needed |

## Open question for you before planning

**Alerting mechanism for step 7.** Your global CLAUDE.md references a `slack_push
"<summary>"` command used at the end of Claude Code sessions. I don't know if that's meant
for arbitrary script-triggered alerts (versus specifically session summaries), or what its
actual interface is. Options:
- Reuse `slack_push` if it's a generally-available shell command — cheapest if it fits.
- A separate, purpose-built notification (e.g. a small script hitting a Slack webhook
  directly) — decouples this from whatever `slack_push` is scoped for.
- Start with just a loud local signal (e.g. a `STATUS: FAIL` file plus macOS
  `terminal-notifier`/`osascript` notification) and add Slack later once the plumbing is
  proven for a week or two.

## Testing plan

- `shadow/parity.py`: unit tests with hand-built weight-history fixtures — added row,
  removed row, changed weight, unchanged history (no false positive), tolerance boundary.
- `shadow/ledger.py`: thin wrapper over already-tested `engine.run`/`indices.benchmark_series`
  — test that it assembles the two curves correctly over a small synthetic panel.
- `scripts/shadow_run.py`: integration-style test simulating 3 consecutive nightly runs over
  a tiny synthetic multi-day panel, asserting: idempotency (re-running the same day is a
  no-op), discrepancy detection when a fixture is mutated between runs, and correct
  skip-and-resume across a simulated weekend gap.
