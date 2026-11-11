# Swing strategy backtest — 20/50 DEMA crossover (Hindi reel)

Branch: `research/swing-ema-crossover` — research only, never merged, no orders placed.
Script: `swing_ema_backtest.py` (repo engine `algotrade/backtest/engine.py`, real delivery rate card + mid-tier slippage from `config/costs.yaml`).

## The claim (from the completed transcript)

- Daily chart, any stock. Plot **50 EMA and 20 EMA (exponential, not simple)**.
- **Enter** when the 20 EMA crosses the 50 EMA from below.
- **Hold** until the 20 EMA tilts down.
- Optional: also watch RSI — **exit if daily RSI is 75 or above**.
- Claimed: **minimum gain 5–6% per trade**, without tension/stress.

Transcript gaps at 54.6–61.9s and 64.6–79.1s; some words garbled. No universe, stop-loss, position sizing or holding period was stated.

## Rules as tested (assumptions NOT in the video)

- Universe: repo liquid-300 — top 300 by trailing 20-session median turnover, ≥ Rs 5 crore/day, coverage ≥ 0.80, monthly rebalance, EQ series only, 103 ETF/fund-like symbols excluded. The video says "any stock"; an undefined universe cannot be backtested.
- "Tilts down" = EMA20 falls vs the previous session (first down-tick).
- RSI = Wilder 14 on closes. Variant A: exit on tilt-down only. Variant B: exit on tilt-down OR RSI ≥ 75.
- Fill: signal on close of D, trade at open of D+1 (repo engine convention, no look-ahead). Equal weight across open positions, cash when flat. Capital Rs 2,00,000, min trade Rs 500.
- Data: NSE bhavdata `data/sessions`, 20 Sep 2024 – 11 Nov 2026 (526 sessions, 3,095 EQ symbols; 1,188,809 rows). Closes are **unadjusted** for splits/bonus (adjusted panel needs `data/curated` + `data/raw/actions`, not in git). Overnight gaps > 20% are treated as corporate-action breaks: indicators restart, open trades closed at last clean close and flagged (4 in A, 3 in B — excluded from headline trade stats).
- In-sample: Oct 2024 – Dec 2025. Out-of-sample: Jan 2026 – Nov 2026.

## Headline results (Rs 2,00,000 capital)

| | A: tilt-down exit | B: + RSI ≥ 75 exit |
|---|---|---|
| Trades (closed) | 1,066 | 1,080 |
| Total return | −44.5% | −24.1% |
| CAGR (net / gross) | −24.4% / +2.6% | −12.3% / +16.1% |
| Sharpe | −1.55 | −0.94 |
| Max drawdown | −44.5% | −32.4% |
| Longest drawdown | 467 of 526 sessions | 467 of 526 sessions |
| Cost drag (annualised) | 27.0% | 28.4% |
| Benchmark CAGR (NIFTY) | +12.0% | +12.0% |
| In-sample CAGR / Sharpe | −15.1% / −1.11 | −12.6% / −0.95 |
| Out-of-sample CAGR / Sharpe | −36.7% / −2.08 | −11.8% / −0.91 |
| Win rate (trades) | 32.3% | 35.1% |
| Trades gaining ≥ 5% | **15.2%** | **19.2%** |
| Trades gaining ≥ 6% | 13.9% | 18.3% |
| Mean / median trade | −0.08% / −1.9% | +0.18% / −1.7% |
| Best / worst trade | +70.8% / −68.0% | +46.3% / −68.0% |
| Median / mean hold | 10 / 14.7 sessions | 8 / 10.9 sessions |

## Verdict on the claim

**The 5–6% minimum-gain claim does not hold.** Only 15–19% of trades gained ≥ 5%, the median trade *lost* ~2%, and one trade in three won at all. Both variants lost money net of real costs in-sample *and* out-of-sample, and both fail the repo's own gates (max drawdown ≤ 20%: −44.5% / −32.4%; OOS Sharpe ≥ 0.8: −2.08 / −0.91). The RSI ≥ 75 exit helps (shorter holds, less bad) but does not rescue the strategy: at best it is roughly flat before costs.

## Why it loses — costs are half the story, not the whole story

- The signal fires ~2× per session and holds ~10–15 sessions, so ~30 positions run concurrently. On Rs 2L that is ~Rs 6,700 per position; flat DP charges and delivery costs on such small tickets are punishing (27–28% annualised drag).
- Sensitivity at **Rs 20,00,000 capital** (same trades, 10× larger tickets): A: CAGR −13.9%, Sharpe −1.03, max DD −27.0%, cost drag 14.7%, gross CAGR +0.8%. B: CAGR −0.5%, Sharpe −0.35, max DD −17.9%, cost drag 16.3%, gross CAGR +15.8%. Even with the cost drag roughly halved, variant A still loses double digits and variant B is flat — the gross edge itself is too thin. The conclusion does not depend on the Rs 2L position size.
- The exit is the weak link: "tilt down" exits after the move has already turned, and the 20/50 cross fires late into trends, so the median trade gives back its gain before the exit triggers.

## Caveats

- Only ~2 years of data were in git (Oct 2024 – Nov 2026), not the 2012+ window of the repo's momentum audit. The window includes a strong benchmark (+12% CAGR), so part of the underperformance is the strategy being long-only in names that lagged — but the per-trade stats (median −2%) do not depend on the benchmark.
- Unadjusted closes: split/bonus gaps are handled by the > 20% break rule, but smaller corporate actions can still distort individual EMAs slightly.
- No stop-loss was in the video, so none was tested. Adding one would be a different strategy, not this claim.
