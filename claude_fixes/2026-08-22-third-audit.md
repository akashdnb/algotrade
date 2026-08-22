# Third audit — 2026-08-22

Commissioned to verify one untrusted number (cost drag 2.9% → 7.2% after the
mark-to-market fix). The number was correct; the audit found the defect behind it.

## Headline

`buffered_long_only` / `portfolio.resample` restate `gross/len(held)` on every
session. Harmless while exposures were carried forward unchanged; once the
mark-to-market fix let positions drift, the restated target became a daily
instruction to trade back to equal weight.

- 2,393 of 2,577 sessions traded, against **126** monthly rebalance dates
- 86.6% of trades and 64.4% of cost on non-rebalance dates
- median trade ₹741 — the flat ₹15.34 DP charge is 300+ bps at that size
- frozen book: Sharpe 0.73 → **0.85**, CAGR 18.7% → **21.8%**, drag 4.70% → **2.15%**
- gross Sharpe 0.95 → 0.96 — the fix removes cost, not signal

## Changed

| file | change |
|---|---|
| `algotrade/backtest/engine.py` | `trade_dates` param; `implied_trade_dates()` helper |
| `algotrade/backtest/metrics.py` | `beats_benchmark` → Sharpe difference; finite-guard IR; calendar-span years; `pipeline_clean` in `gate_inputs` |
| `algotrade/backtest/lookahead.py` | `settle` 25 → 0; month-end default cutoffs (`_month_end_cutoffs`) |
| `algotrade/backtest/registry.py` | `pipeline_clean` gate; negative `configs_explored` corrections |
| `scripts/run_hypothesis.py` | register *after* pre-flight; persist bools; pass `trade_dates` |
| 6 sprint/diagnostic scripts | all 13 `engine.run` call sites pass `trade_dates` |
| `config/trials.jsonl` | appended −8 correction for the duplicated exploration record |
| `tests/` | +3 tests (re-equalisation, 1-session leak, unrun pipeline check) — 343 pass |

## Verified correct (tried to break, could not)

- mark-to-market multiplier is period-exact (buy-and-hold reproduces the analytic answer)
- `capital_at_open` is pre-return and assigned before use (1.7e−10 residual)
- `vol_scalar`'s `shift(2)` is tight — neither leaky nor over-conservative

## Not fixed (recorded, low impact)

- `min_trade_value=500` is 7.5% of a ₹2L/30-name position — should scale with position size
- a symbol missing for one session is fully liquidated and re-bought (65 of 76,956 rows, 0.08%)
- `Metrics` has no `cost_bps_of_traded_value`, so a 2x-wrong bps assumption stays invisible

Doc: PROJECT.md §11j. Commit f306ed1.
