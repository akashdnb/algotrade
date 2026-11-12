# Swing-strategy sweep — results

**Question:** does any well-known swing strategy actually work on Indian equities?
**Verdict: none of the 8 passes both repo gates.** The closest is #7 momentum
pullback: it passes the drawdown gate (max DD -15.3% <= 20%) but still loses
money (CAGR -3.7%, OOS Sharpe -0.51 < 0.8).

## Setup (identical to research/swing-ema-crossover, for comparability)
- Universe: repo liquid-300 (top 300 by trailing 20-session median turnover,
  >= Rs 5 cr/day, coverage >= 0.80, monthly rebalance, EQ only, ex-ETF).
- Data: NSE bhavdata data/sessions, 20 Sep 2024 - 11 Nov 2026 (526 sessions,
  1,188,809 rows, 3,095 EQ symbols). Closes UNADJUSTED for splits/bonus;
  overnight gap > 20% treated as a corporate-action break.
- Capital Rs 2,00,000, delivery charges + mid-tier slippage from
  config/costs.yaml, fill at next open after the close signal, equal weight.
- In-sample Oct 2024-Dec 2025 | out-of-sample Jan-Nov 2026.
- Gates: max drawdown <= 20%, OOS Sharpe >= 0.8. Benchmark CAGR +12.0%.

## Ranked results (by net CAGR)

| # | Strategy | Trades | CAGR net / gross | Sharpe | Max DD | OOS CAGR / Sharpe | Win | >=5% | Median trade | Cost drag |
|---|----------|--------|------------------|--------|--------|-------------------|-----|------|--------------|-----------|
| 7 | Momentum pullback | 397 | -3.7% / +10.2% | -0.62 | -15.3% | -6.3% / -0.51 | 37.6% | 24.9% | -2.8% | 13.9% |
| 5 | Dual momentum breakout | 1129 | -9.8% / +5.1% | -0.85 | -27.3% | -19.3% / -1.01 | 30.7% | 15.6% | -2.5% | 14.8% |
| 8 | Bollinger squeeze breakout | 81 | -12.1% / -2.8% | -0.73 | -31.7% | -22.6% / -1.21 | 29.6% | 11.1% | -3.6% | 9.3% |
| 4 | RSI + Bollinger mean reversion | 850 | -16.6% / +5.3% | -1.05 | -39.5% | -20.8% / -1.32 | 37.5% | 24.8% | -2.7% | 21.9% |
| 2 | Range breakout | 660 | -24.6% / +1.1% | -1.92 | -52.9% | -29.1% / -2.03 | 45.1% | 9.6% | -2.6% | 25.7% |
| 6 | Donchian 20-day breakout | 1503 | -30.8% / -10.0% | -1.71 | -53.9% | -45.2% / -2.29 | 33.8% | 19.4% | -3.4% | 20.8% |
| 3 | Pullback with RSI turn-up | 2533 | -36.6% / +4.5% | -1.88 | -65.0% | -35.3% / -2.00 | 41.1% | 17.4% | -2.4% | 41.1% |
| 1 | EMA pullback trend-follow | 5030 | -53.0% / +10.5% | -2.78 | -79.6% | -66.1% / -3.15 | 25.1% | 9.8% | -1.4% | 63.5% |
| - | Benchmark: 20/50 EMA crossover (earlier, not re-run) | 1066 / 1080 | -24.4% / -12.3% (A/B) | -1.55 / -0.94 | -44.5% / -32.4% | -36.7% / -11.8% | 32.3% / 35.1% | 15.2% / 19.2% | -1.9% / -1.7% | 27.0% / 28.4% |

## Rules tested (exact; stops/targets are my assumptions, documented in sweep.py)
1. EMA pullback: 20EMA>50EMA, low within 2% above 20EMA, bullish close; stop 2% below 5d swing low; exit close<20EMA.
2. Range breakout: 15d range <=8% wide, close above prior 15d high, volume>=1.5x 20d avg; stop at range midpoint; target = range height projected up.
3. RSI pullback: uptrend, pullback to 20EMA/50EMA, RSI turns up from 40-50; stop below 3d low; target prior 10d high.
4. RSI+BB reversion: prev close below lower BB (20,2) with RSI<30; enter on close back inside + RSI turning up; stop below 5d low; target middle BB.
5. Dual momentum: close>EMA50, RSI crosses above 60, AND (vol>1.5x SMA20 OR ROC10>4%), NIFTYBEES close>SMA200 (proxy for NIFTY50>SMA200); exit close<EMA50 or RSI<50.
6. Donchian 20: close > highest high of prior 20 sessions, volume>=1.5x avg; exit close < lowest low of prior 10 sessions.
7. Momentum pullback: 12-1 momentum rank <=30 in liquid-300, pullback 2-8% off 5d high, RSI 40-60, bullish reversal close; max hold 15 sessions, stop below pullback low.
8. BB squeeze: BB width narrowest in 20 sessions, close above upper BB, volume>=1.5x; exit close<middle BB, stop at entry lower BB.

## Why they lose
- Costs dominate the frequent ones: #1 churns 5,030 trades in 526 sessions
  (cost drag 63.5%/yr), #3 2,533 trades (41.1%).
- #2's target caps gains: range <=8% means the best trade was only +7.6%.
- #6 is the only one with a negative gross CAGR (-10.0%): breakouts in this
  window were mostly false, and the 10-day exit is slow.
- #7 is the only one with a positive mean trade (+1.06%) and a passable
  drawdown, but its median trade is still -2.8% and it loses out-of-sample.
  Note it only starts trading once 252 sessions of history exist per symbol,
  so its effective window is shorter than the others.
- #8 has only 81 trades: the squeeze filter is very strict, and it still
  loses out-of-sample (-22.6% CAGR).

## Caveats
- Only ~2 years of data; every strategy loses out-of-sample, so none of this
  is a 2012+ validated edge.
- Unadjusted closes; gaps >20% treated as corporate-action breaks.
- NIFTY regime in #5 uses NIFTYBEES as a proxy for the NIFTY50 index.
- No strategy was re-tuned or optimised after seeing results; these are the
  rules as written before the run.
