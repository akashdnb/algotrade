# An unaffordable entrant no longer leaves its slot's cash idle

**Requested 2026-10-05**, after the 2026-09 rebalance dropped APARINDS (share
price Rs 17,648 against a Rs 6,732 equal-weight slot) and left that slot's
budget -- about 3.3% of the book -- sitting in cash until the next period.

## The change

`RebalanceService.decide()` now calls `_fill_unaffordable_slots()`
(`algotrade/services/rebalance.py`) after sizing: when `construction.build()`
drops a name because its share price exceeds the whole equal-weight position,
the next-ranked candidate **outside** the target -- the next name that did not
make the entry cut -- is swapped in for that one slot, and sizing is redone.
The book stays at exactly `entry` names; the swap changes nothing else about
it. Bounded by the size of the ranking, so it cannot loop longer than there
are candidates to try.

Scoped narrowly on purpose:

* **Only a fresh entrant** dropped for price is eligible. A HELD name the book
  sizes to zero is being exited on purpose (it fell past the rank buffer, or
  lost its price) -- that is `leaving`, not an affordability problem, and
  substituting for it would paper over why the position is actually closing.
* **Only the "exceeds the whole target position" reason.** A missing or
  non-positive price is a feed defect, not a sizing problem -- a substitute
  cannot fix bad data, and silently swapping it away would hide exactly the
  kind of thing `dropped` exists to surface.

Considered and rejected: letting the book grow to `entry + 1` instead (buy the
original 30, then spend whatever is left on one more name). Rejected because
this strategy's identity is equal-weight over a fixed count -- the backtest
validated 30 names at 1/30 each, and a 31st position is either an
unequal-weight outlier or forces re-sizing all 30 others down to 1/31, a much
bigger change than swapping one name for another. The swap leaves every other
position's size untouched.

## The frozen-strategy caveat

**This was applied directly to the running `v1` portfolios
(`paper_momentum_v1_001`, `shadow_momentum_v1_001`), not forked into a v2, at
the user's explicit choice** after being told the tradeoff: neither
`strategy_params_hash` (the YAML entry/exit/lookback parameters) nor
`pipeline_hash` (the universe filter -- series, exclusions, allowlist,
suffixes) covers sizing/construction mechanics, so `--verify-frozen` will
stay green across this change with no record that the strategy's behaviour
moved. That is exactly the class of problem `pipeline_hash` was added for
after `DEFAULT_EXCLUSIONS` was edited live on 2026-08-20 -- this is the same
shape of exception, made consciously rather than discovered after the fact.

Worth a third hash (construction/sizing behaviour) if this class of change
recurs, so the next one is at least visible rather than silent.

## Also fixed in this session, same root investigation

Both portfolios' 2026-09 rebalance fills had been recorded with the wrong
business date (`2026-10-02` instead of `2026-10-01`) by `_fill_date()`
stepping a weekend back to Friday without knowing Oct 2 is an NSE holiday
(Gandhi Jayanti). That desynced the cash ledger from the rebuilt holdings for
one snapshot (`cash` read Rs 556 while holdings were already the new book,
understating NAV by the full Rs 6,308 delta). Corrected by hand: 46 execution
records' `executed_on`/`executed_at`, 46 matching `cash_transactions.date`
entries, holdings rebuilt from the corrected history, Oct-1 snapshot
regenerated via `daily.yml`. All 60 snapshots (30 per portfolio) pass the
invariant checker clean afterward.
