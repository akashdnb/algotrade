# Algo Trading System — Project Plan

**Status:** Scoping complete, pre-implementation
**Created:** 2026-08-21
**Owner:** Akash Ranjan
**Time budget:** 10–15 hrs/week

---

## 1. Goal & Framing

Build a systematic swing-trading system for Indian equities, human-in-the-loop first,
automated later.

**Initial capital:** ₹2L. Will scale if validated.

**This is not an income project at ₹2L.** 12 hrs/week ≈ 600 hrs/year; even 25% on ₹2L
is ₹50k. The deliverable is a **validated edge + trustworthy infrastructure** — an
option on capital. A validated Sharpe-1.0 system is worth ~₹2L/yr at ₹10L deployed
and ~₹10L/yr at ₹50L. The code and the edge are the asset; ₹2L is the test harness.

### Return target

| Metric | Target |
|---|---|
| Net annual return | 10–18% unlevered → 20–25% levered (2–2.5×) |
| Volatility | < 12% (ideally 7–9%) |
| Sharpe | 0.7–1.0 single strategy; 1.0–1.2 multi-strategy |
| Max drawdown | < 20% |
| Construction | Market-neutral or low-beta |

**Optimise for LOW VOLATILITY, not high return.** A 10%-return / 6%-vol strategy is
strictly better than 16%-return / 20%-vol, because the first can be levered into the
second and the second cannot be de-levered into the first. Most retail researchers
chase return and end up with something unleverageable.

### The benchmark is NIFTYBEES, not zero

NIFTY buy-and-hold ≈ 12% CAGR, zero work, zero bugs. If the system nets 14% with 20%
drawdowns it has **negative value**. Build the NIFTYBEES comparison into the
backtester as the default from day one.

What justifies the project anyway: buy-and-hold cannot give *uncorrelated* returns.
13% at 9% vol beats 12% at 20% vol with 40% peak drawdowns, and keeps earning in flat
or falling markets.

---

## 2. Arena Decision

**Chosen: Indian equity cash, swing horizon (2–10 day holds), cross-sectional,
~300 liquid NSE names.**

### Why swing over intraday

| | Intraday | Swing |
|---|---|---|
| Cost vs move captured | 10.6bps for ~1% move → ~10.6% drag | 29.9bps for ~4% move → **~7.5% drag** *(≈1.4× better, see §3 correction — NOT the 3–6× originally claimed)* |
| Data needed | Minute/tick, messy, GBs | **Daily bars — free, clean, tiny** |
| Research iteration | Slow | **~100× faster** |
| Crowding | Maximum — where the algos live | Meaningfully less |
| Documented edges | Sparse, execution-dominated | **Decades of cross-market literature** |
| Compatible with a day job | No | **Yes — run at 4pm, order at 9:15am** |

### Why cross-sectional equity over index

**Breadth.** ~300 names × 250 days = **~75,000 stock-days/year** vs **~250
index-days/year**. That is a ~300× difference in statistical power to distinguish edge
from luck. Decisive for a researcher with no prior hypothesis.

Also: cross-sectional strategies average over many semi-independent bets and degrade
gracefully. Time-series index timing is one bet on one regime and flips sign.

### Explicitly out of scope for Phase 0

- **Index intraday** — NIFTY/BANKNIFTY are not tradeable instruments. Requires F&O
  (no experience) or index ETFs (poor cost/range ratio, zero breadth).
- **Options** — no experience; adds IV surface, greeks, skew, theta to an unsolved
  research problem. Historical chain data expensive/unavailable.
- **Short option selling** — fat left tail. This is where ₹2L accounts die.
- **Microcaps (Lever 3)** — deferred, see §3.

---

## 3. The Cost Wall (design input, not an afterthought)

Costs are the deciding variable. They get a first-class, versioned component.

### Reference figures (verify current rates — they changed in 2024)

### Zerodha rate card (fetched 2026-08-21 from zerodha.com/charges) → `config/costs.yaml`

| Charge | Equity **DELIVERY** | Equity **INTRADAY** |
|---|---|---|
| Brokerage | **₹0 (zero)** | 0.03% or ₹20/order, lower of |
| STT | **0.1% on BUY *and* SELL** | 0.025% on sell only |
| Txn charges | NSE 0.00307% / BSE 0.00375% | same |
| SEBI | ₹10 per crore | same |
| Stamp duty | 0.015% (₹1500/cr) buy side | 0.003% (₹300/cr) buy side |
| GST | 18% on (brokerage + SEBI + txn) | same |
| **DP charges** | **₹15.34 per scrip on SELL** | — (no demat movement) |
| AMC | free yr 1, then ₹100–300/yr + GST | same |

### Worked round trip on a ₹20,000 position (₹2L / 10 positions)

| Line item | Delivery | Intraday |
|---|---|---|
| Brokerage | ₹0.00 | ₹12.00 |
| STT | **₹40.00** | ₹5.00 |
| Txn charges | ₹1.23 | ₹1.23 |
| SEBI | ₹0.04 | ₹0.04 |
| Stamp | ₹3.00 | ₹0.60 |
| GST | ₹0.23 | ₹2.39 |
| **DP** | **₹15.34** | ₹0.00 |
| **TOTAL** | **₹59.84 → 29.9 bps** | **₹21.26 → 10.6 bps** |

> **⚠️ CORRECTION to an earlier assumption.** The "8–15 bps for equity cash" figure used
> earlier in scoping was the **intraday** number. **Delivery — which is what a 2–10 day
> swing hold is — costs ~30 bps round trip, roughly 2.8× intraday.** Driver: STT is 0.1%
> on *both* sides for delivery (₹40 of the ₹60) versus 0.025% sell-only for intraday, plus
> ₹15.34 DP.
>
> Silver lining: **brokerage on delivery is zero**, so the ₹20/order assumed earlier
> doesn't apply.

### DP charges impose a floor on position size

₹15.34 is **flat per scrip per sell**, so it scales inversely with position size:

| Position size | DP cost |
|---|---|
| ₹10,000 | **15.3 bps** |
| ₹20,000 | 7.7 bps |
| ₹33,000 | 4.6 bps |
| ₹50,000 | 3.1 bps |
| ₹1,00,000 | 1.5 bps |

**Design consequence at ₹2L: target ~6–10 positions, not 15–20.** Twenty positions of
₹10k pays 15.3 bps in DP alone per rotation versus 6.1 bps for eight of ₹25k — and the
marginal diversification benefit of 8→20 names almost certainly doesn't justify 9 bps per
rotation. This also reinforces the low-turnover thesis: DP is a **fixed cost per position
per round trip**, so fewer, larger, longer-held positions win twice.

### Other instruments (approximate, verify before use)

| Instrument | Round-trip all-in |
|---|---|
| Equity cash **delivery** | **~30 bps** (computed above) |
| Equity cash intraday | ~10.6 bps (computed above) |
| Index futures | ~3–5 bps |
| NIFTY weekly ATM option | **~1.5–2% of premium** |
| Microcap equity | **1–4% (spread-dominated)** |

**Hard filter to encode in the backtester on day one:** any strategy whose backtested
gross expectancy doesn't clear full modelled costs by a wide margin is dead. Kill it in
seconds rather than after a month of tuning.

**Low turnover is the structural retail advantage.** Cost is ~fixed per trade, so
holding 10 days beats holding 2 days on the same signal even with worse entry timing.
Retail wins by trading *less*; institutions can't follow into slow, small,
capacity-constrained corners.

### Leverage — the honest path to >20%

`levered return = rf + L × (excess return)`. A 13% / 9%-vol strategy at 2× ≈ **19.5%
at 18% vol** — more return and less volatility than NIFTY. Same Sharpe.

**But cheap leverage is capital-gated:**

| Source | Leverage | Real cost | Viable at ₹2L? |
|---|---|---|---|
| MIS intraday | 3–5× | Free | No — forces intraday |
| MTF (delivery) | ~4× | 9–15% interest | No — interest cancels edge |
| Single stock futures | ~5× | 5–9% carry | No — ₹15L+ min contract |
| Index futures overlay | ~7× | 5–8% carry | No — kills diversification |

**>20% is a capital problem, not a skill or code problem.** Unlocks ~₹10–15L.
Consequence: **vol targeting is architecture, not a feature** (see §5).

### Lever 3 (microcap / illiquidity premium) — deferred, then measured

Agreed to use for a portion of funds, but **not at the start.**

Traps: severe survivorship bias (delistings — free data drops them, backtest looks
spectacular and is fiction), 0.5–2% spreads, no volume at the close so fills are
imaginary, market impact at ₹50k in a ₹50L/day name, circuit limits locking you in,
ASM/GSM moving a name to trade-for-trade overnight, operator pump-and-dumps.

**Approach: make liquidity a research dimension, not an upfront choice.** Run the same
signal across liquidity deciles.
- Edge rises monotonically as liquidity falls → likely real illiquidity premium (then
  rule out data artifacts)
- Random across deciles → noise dressed as a strategy

Start in liquid mid/large caps where backtester assumptions are ~true, get a reference
point, then extend down. Cap at ~20–30% of capital once *measured*, never at the start.

---

## 4. Candidate Edges to Research

Swing-horizon effects with published cross-market evidence, including on NSE:

| Effect | Robustness | Cost-friendly | Notes |
|---|---|---|---|
| Cross-sectional momentum (12-1m) | Very high | Good (low turnover) | Crash risk (2009, 2020) |
| Post-earnings drift (PEAD) | Very high | Good | Proxy surprise via price/volume reaction — **needs no fundamental data** |
| Low volatility / low beta | High | Excellent | More factor tilt than alpha |
| Quality / profitability | High | Excellent | Very low turnover |
| Short-term reversal (1w–1m) | Very high statistically | **Poor — turnover kills it** | Marginal after Indian costs |
| **Delivery-percentage signals** | Untested | Good | India-specific (no US analogue). Low delivery % = speculative churn, high = genuine accumulation. Available in bhavcopy. Found via §10.3 eod2 audit |
| Index reconstitution flow | Structural | Good | Announced in advance, index funds *mechanically obligated*. Published calendar |
| Demerger index-fund forced selling | Structural | Good | Passive funds must dump the spun-off entity |

**Assume you capture ~half of whatever any paper claims.** Momentum's Sharpe roughly
halved post-publication in most markets.

### Event-driven / retail-quota niches (parallel track, capital-advantaged)

Indian regulation **reserves a quota for "small shareholders" — holdings up to ₹2 lakh**
— in tender-offer buybacks and open offers, with much higher acceptance ratios than the
general category. **₹2L is the qualifying condition, not a handicap. Funds are
structurally excluded.**

- Buyback tender arbitrage — 3–8% per event over weeks
- Open offers / delisting offers
- Rights entitlement (RE) arbitrage — REs often trade below theoretical value

Caveats: lumpy, ~10–25 events/year, capital idle between, returns compressed as they
became known. Cannot produce a smooth equity curve alone. Verify current SEBI buyback
rules — the routes have been reshaped.

---

## 5. Architecture

**Governing principle: the strategy is a pure function, and the same code runs in
backtest, paper, and live.** Only the data source and the execution adapter swap.

```
  Data Layer ──> Universe Service ──> Feature Layer ──> Strategy
                                                            │
                                                            v
                                              Portfolio Construction / Risk
                                                            │
                                    ┌───────────────────────┴───────┐
                                    v                               v
                          Backtest Engine                    Execution / OMS
                        (+ Cost & Fill Model)              (+ Broker Adapter)
                                    │                               │
                                    v                               v
                          Evaluation Harness  <──diff──>  Parity Checker
```

### Component contracts

| Component | Contract | Why the boundary is here |
|---|---|---|
| Data Layer | Raw immutable Parquet + adjusted read-time views | Never mutate raw. Corporate actions are *events*; adjustment on read, so a bad adjustment is a bug not permanent corruption |
| Universe Service | `date → tradeable names + liquidity tier` | **Where survivorship bias lives or dies.** Membership is a time series, not a list. Must include delisted names |
| Feature Layer | `(panel, t) → features`, vectorised | Pure. Vectorised over all stocks at once = research speed |
| Strategy | `(features, t) → target weights` | Pure. No I/O. Knows nothing of brokers, cash, capital, or current positions. Emits **weights, not orders** |
| Portfolio / Risk | `weights + capital → sized positions` | Vol targeting, position/sector caps, liquidity caps, beta neutralisation, **turnover damping** |
| Cost & Fill Model | Separate, versioned, testable | Costs decide everything — they get a component, not a hardcoded 0.1% |
| Evaluation Harness | Walk-forward, pre-registration, trial counting | The component that decides whether the project succeeds |

### Three decisions worth stating explicitly

**1. Vol targeting is architecture.** Portfolio construction targets a *volatility*
(~8%), not a position size. This is what makes the strategy leverageable later —
exposure dials up/down without touching strategy logic. Strategies built without it are
usually unleverageable and you find out two years in.

**2. Look-ahead detection must be mechanical, not disciplinary.** You *will* introduce
look-ahead bias. Build a harness that computes features on truncated data and asserts
output for overlapping dates is byte-identical. If a feature peeks at the future,
truncation changes its value and the test fails.

Same idea for the engine: build a fast vectorised path *and* a slow event-driven path,
and assert they produce identical results. When they diverge, one has a bug — found in
seconds instead of in live trading.

**3. Pre-registration with a trial counter.** Before running any config, commit the
hypothesis, parameters, and pass/fail gates to git. Count trials in an append-only log.
"Best of 400 configs" needs a multiple-testing haircut (deflated Sharpe) before you
believe it. Omitting this is probably the largest source of fake edges in existence.

**4. Turnover damping lives in `portfolio.py`, not in the strategy.** The strategy emits
*desired* weights; the portfolio layer applies no-trade bands / hysteresis against
current positions. All "how do I get from here to there" logic in one place; strategy
stays a pure opinion about the market and works unchanged at ₹2L or ₹20L.

---

## 6. Tech Stack — deliberately boring

- **Python throughout.** Polars/pandas + numpy, **DuckDB** for queries, **Parquet** for
  market data, **SQLite → Postgres** for portfolio state.
- **Candidate third-party libs** (evaluate, don't assume): `eod2` for bhavcopy ingestion +
  split/bonus adjustment, `nsefin` for corporate actions. Audit both against the §6 tests
  before trusting output — see §10.3.
- **No C++.** 300 names × 10 years daily ≈ 750k rows. Python is ~1000× fast enough.
  This is a temptation to resist — it would cost months and buy nothing.
- **No Kafka, Redis, microservices, or Docker orchestration.** One repo, one process,
  cron/launchd, files on disk. Infra sprawl is the biggest procrastination trap for
  engineers building this, and it feels productive the whole time.
- **Streamlit** for the UI when the time comes (~300 lines, an afternoon). No frontend,
  no API layer, no auth, bound to localhost. Alternative: Telegram bot with inline
  accept/decline.
- **No VPS, no broker API keys until Phase 3.** Phases 1–2 are manual research on the
  laptop.

**Prediction: this stays a monolith for years.** At two trades a day there is never a
technical reason to split into services. Catching yourself adding a message queue means
procrastination wearing an architecture costume.

### Storage: two datasets, opposite access patterns, separate stores

**Decided against MongoDB.** OHLCV is the most structured data that exists — fixed
schema forever. Mongo gives no schema enforcement, ~10× worse compression, and poor
columnar scan performance, which is exactly the access pattern needed.

| | Market data | Portfolio state |
|---|---|---|
| Shape | Append-only, immutable | Mutable, transactional |
| Access | Wide analytical scans | Small point lookups |
| Needs | Compression, fast scans | **ACID, constraints, audit trail** |
| **Store** | **Parquet + DuckDB** | **SQLite → Postgres** |

**Trades are an append-only event log, never mutable position rows.** Position = fold
over the event log. Free audit trail, and reconciliation becomes possible. Mutable
`current_position` rows silently drift and you never learn when.

### Repo layout

```
algotrade/
  config/
    universe.yaml
    costs.yaml                    # versioned — cost params are data, not constants
    strategies/reversal_v1.yaml   # pre-registered configs, committed to git
    trials.jsonl                  # append-only trial counter
  data/                           # gitignored
    raw/                          # immutable: bhavcopy, corporate actions
    curated/                      # adjusted panel (derived, rebuildable)

  algotrade/
    io/                    # ---- SHELL: all I/O ----
      bhavcopy.py  actions.py  store.py  state.py

    core/                  # ---- PURE: no I/O, no clock, no randomness ----
      calendar.py          # trading days, holidays
      universe.py          # (turnover_panel, date) -> names + liquidity tier
      adjust.py            # (raw, actions) -> adjusted series
      features.py          # panel -> features, vectorised
      strategies/base.py   # Strategy protocol
      strategies/reversal.py
      portfolio.py         # weights -> sized positions, vol target, caps, damping
      costs.py             # cost + slippage model

    backtest/
      engine.py            # vectorised (fast, for sweeps)
      engine_event.py      # event-driven (slow, validation twin)
      metrics.py  evaluate.py  registry.py

    live/                  # does not exist until Phase 3
      recommend.py  shadow.py  parity.py

    cli.py
  notebooks/               # exploration only — never defines logic
  tests/
```

**The one rule: `core/` must not import from `io/`.** Pure functions in, DataFrames out.
This makes core trivially testable, and makes backtest/live parity *structurally
guaranteed* rather than a discipline — `core` literally cannot tell which it's running in.

### Strategy interface

```python
class Strategy(Protocol):
    def required_features(self) -> list[str]: ...
    def target_weights(self, features: pl.DataFrame, as_of: date) -> pl.DataFrame:
        """symbol -> weight. Gross <= 1.0. Net ~0 if market-neutral."""
```

### Notebook discipline

**Notebooks import from `algotrade.*` and contain only calls and plots.** The moment a
function is written in a notebook it moves into the library before its output is
trusted. Otherwise: 40 notebooks with 40 slightly different copies of the feature logic
and no way to tell which produced the result you got excited about.

### Six tests worth more than 200 generic ones

1. **Look-ahead detector** — features on data truncated at `T` == full-data features for
   dates ≤ `T`, byte-identical. *Highest-value test in the project.*
2. **Engine agreement** — vectorised vs event-driven, same equity curve within float
   tolerance.
3. **Cost model golden test** — hand-compute one trade's charges from the broker's
   published rate card, assert exact match. STT/GST errors silently skew everything.
4. **Corporate action adjustment** — synthetic 1:2 split → adjusted series continuous.
5. **Point-in-time universe** — a stock delisted in 2020 appears in the 2019 universe
   and is absent from 2021.
6. **Ledger fold** — trade event log folds to expected positions and cash.
7. **Corporate-action reconciliation** — every price discontinuity has a matching action,
   and every action has a matching discontinuity. Mismatches halt ingestion. See §10.6 —
   this is the test whose absence lets eod2 corrupt history silently.

---

## 7. Roadmap

**Build order is deliberately inverted from intuition: measurement instrument first,
delivery system last.** Steps 1–2 are ~90% of the value and ~30% of the code. If nothing
passes the gates in step 2, four months of infrastructure work was saved — and that is
the *likely* outcome, not the pessimistic one.

| Phase | Deliverable | Exit gate | Est. @12hr/wk |
|---|---|---|---|
| **0a. Instrument** | **First task: the ~2hr `eod2` audit (§10.3).** Then data layer, universe service, features, cost model, backtest engine, evaluation harness. **Notebooks only — no service, UI, cron, or DB server.** | Reproduces a known published effect | 6–8 weeks |
| **0b. Research** | 20–30 hypotheses tested on daily bars, ~300 names | Pre-registered numeric gates (§8) | 2–3 months |
| **0c. Plumbing** | Monolithic service + cron + Streamlit page + shadow portfolios | **Zero unexplained signal discrepancies** | 6–8 weeks |
| **0d. Live** | Real capital, escalating ₹25k → ₹50k → ₹1L → ₹2L | Live tracks backtest within tolerance | 3–6 months |
| **1. TBD** | Chosen from Phase 0 findings — **likely a 2nd uncorrelated swing strategy, not intraday** | — | — |
| **2. Don't plan it** | Determined by what 0 and 1 teach | — | — |

**Phase 0a validation trick:** calibrate the backtester against a *known* published
effect (short-term reversal, momentum on NSE) before trusting it on unknown ones. If you
can't reproduce an effect known to be there, the instrument is broken — and you learn
that before betting on it. Same principle as putting a thermometer in ice water.

**Why Phase 1 is probably not intraday:** Sharpe scales ~√N for uncorrelated strategies.
Three strategies at Sharpe 0.6 combine to ~1.0 — bigger, safer, cheaper than one
intraday strategy at Sharpe 0.8 with 10× the operational risk in the most
algo-saturated arena in India. Let Phase 0's *results* pick Phase 1.

**Capital escalation is not binary.** Real slippage, partial fills, and real bugs only
appear with real money. Meet them at ₹25k.

---

## 8. Validation Protocol — Gates, Trial Budget, Holdout

### 8.1 Pre-registered gates (Phase 0b)

**Write these down before looking at any results.** Otherwise you will rationalise —
everyone does.

- Out-of-sample Sharpe **> 0.8** after full modelled costs
- Max drawdown **< 20%**
- Positive in **≥ 65%** of rolling 3-month windows
- Profitable across **≥ 2 distinct regimes** (2018–19, 2020, 2022, 2023–24)
- **Parameter robustness:** ±20% perturbation on every parameter doesn't flip the sign
- **Trade concentration:** remove the top 5 trades, still positive
- **Beats NIFTYBEES risk-adjusted** after costs
- Turnover and cost assumptions verified against the actual broker rate card

The last two robustness checks are the cheapest and most effective overfitting detectors
that exist. **A real edge is a broad plateau; an overfit one is a narrow spike.**

**If in-sample Sharpe comes out above ~2.5, treat it as a bug report, not a discovery.**
At this frequency and cost structure that number means look-ahead bias somewhere.

### 8.2 The multiple-testing problem (why a grid search is self-defeating)

**If you test many strategies, some look excellent by pure luck.** Take 1,000 strategies
that are pure coin flips with zero edge, backtest all on 5 years — the best will show a
great Sharpe, not because it works but because you took a *maximum over 1,000 draws of
noise*.

Expected best-of-N Sharpe from **pure noise**, approximately `√(2·ln N / T)`:

| Configs tested | 5 years of data | 10 years |
|---|---|---|
| 5 | 0.68 | 0.48 |
| 10 | 0.96 | 0.68 |
| 100 | **1.36** | 0.96 |
| 1,000 | **1.66** | 1.18 |
| 10,000 | **1.92** | 1.36 |

**The gate is 0.8 OOS Sharpe. With 100 configs tested on 5 years, noise alone delivers
1.36 — it clears the gate comfortably.**

Therefore: *"I found a Sharpe 1.3 strategy"* is a meaningless statement in isolation.
**Sharpe 1.3 from 5 trials is a discovery. Sharpe 1.3 from 500 trials is nothing.** The
number is uninterpretable without the denominator, and almost nobody reports the
denominator — including to themselves.

Consequence: a naive grid search (5 params × 5 values = 3,125 trials) **guarantees** the
winner is indistinguishable from noise. More work, strictly less knowledge.

### 8.3 The resolution: constrain *confirmation*, not *exploration*

Total experiments need not be limited. The structure does:

| Set | Years | Trials allowed | Purpose |
|---|---|---|---|
| **Development** | 2016–2021 | **Unlimited** — sweep freely | Explore. Accept that you're overfitting here. Walk-forward inside it for a first honest-ish read |
| **Holdout** | 2022–2026 | **3–5 looks for the ENTIRE project** | Confirm. Sacred |

The holdout works because **N = 1 on data never touched** — a single look is an unbiased
estimate. The moment you peek, tweak, and re-test, you've spent a second trial and the
table in §8.2 begins applying to your holdout too.

> **HARD RULE: if a candidate fails the holdout, that hypothesis is dead. Do not adjust
> and retry.** That retry is exactly how people burn their holdout without noticing, and
> it is the most common way a rigorous-looking process produces a fake result.

### 8.4 Per-hypothesis workflow

1. **Reason economically about parameters — do not optimise them.** 5-day reversal
   *because that's a week*; 12-1 momentum *because that's the literature standard*.
2. Sweep in-sample freely; narrow to 1–3 candidates via walk-forward.
3. Spend **one** holdout look. Live with the answer.

**Realistic budget at 12 hrs/week over 2–3 months: 15–25 distinct hypotheses explored,
3–5 holdout looks total.** Vastly more informative than 50,000 backtests that prove
nothing.

**Note the binding constraint:** free bhavcopy archives start ~2016, so there are only
~10 years total. The 6-year dev / 4-year holdout split above is close to the maximum
available, which caps how much can ever be proven. Budget holdout looks accordingly.

---

## 9. Human-in-the-Loop Design (Phase 0c)

System recommends; human accepts / declines / discards via UI. Decisions are saved as
the actual trade record and drive subsequent sell recommendations and fund adjustment.
Taxes and charges accounted per trade.

### Shadow portfolios — not a cloned database

Run parallel portfolios in the **same tables**, keyed by `portfolio_id`. Same code, same
data, one schema. No clone drift, strictly more information.

| `portfolio_id` | Contents | What comparing it tells you |
|---|---|---|
| `live` | Actually-accepted trades | Reality |
| `shadow_full` | **Every** recommendation, taken blindly | `live` vs this = **cost of your discretion** |
| `benchmark` | NIFTYBEES buy-and-hold | `shadow_full` vs this = does the strategy justify itself |
| `variant_a` | A parameter variant | Sensitivity in real time |

### Forward simulation cannot validate edge

8 positions at ~7-day holds → ~15–20 trades/month. Distinguishing Sharpe 0.8 from
Sharpe 0.0 at real confidence needs **3–5 years** of forward data. After three months
you'll have a number that *feels* like evidence and is pure noise — dangerous in both
directions (abandoning a good strategy after an unlucky quarter, scaling a bad one after
a lucky one). **Only out-of-sample backtesting validates edge.**

Simulation / paper trading's real purposes:
1. **Plumbing** — does the daily pipeline run without breaking?
2. **Signal parity** — does today's live signal match what the backtester computes for
   today? Every discrepancy is a bug, and these are the bugs that silently kill live
   performance while the backtest still looks great. **Highest-value check in the system.**
3. **Cost calibration** — assumed slippage vs actual fills
4. **Behavioral audit** — below

### The insight the discard button unlocks

**The backtest assumes every signal is taken.** Decline 30% of recommendations and you
are running a *different strategy* than the one validated — the backtest no longer
describes you.

Measurable, and almost nobody measures it. Track **acceptance rate** and **P&L of
declined recommendations vs accepted ones.** If declines would have outperformed
accepts, **discretion is destroying alpha** — the usual finding, and an evidence-based
trigger to automate rather than a gut decision.

UI implications: **"accept all" is the default action**, and **every decline requires a
logged text reason.** The reasons become a dataset about your own biases.

### Backtester: separate trigger, shared core

Separate entry point and schedule: yes. Separate *code*: never. The backtester and live
recommender import the **same** feature and strategy modules. Only the data source and
the sink differ.

- **Research backtests** → manual, interactive, in notebooks. Sub-minute iteration; cron
  adds nothing.
- **Weekly automated regression run** → re-run the approved config on updated data,
  alert if metrics drifted beyond tolerance. **Strategy decay detection** — the one thing
  you'd otherwise never notice until it cost money.

### Risk limits apply even with a human in the loop

Max single-name weight, max sector exposure, max position count, and a drawdown circuit
breaker that stops emitting recommendations entirely. **Human review is not a risk
control** — you'll approve things at 11pm.

---

## 10. Data Layer — RESOLVED

Four needs. Only the first is easy.

| # | Need | Difficulty | Why it matters |
|---|---|---|---|
| 1 | Daily OHLCV, ~500 NSE names, 10 yrs | Easy, free | Baseline |
| 2 | **Delisted/suspended names' history** | **Hard** | Without it, survivorship bias |
| 3 | **Corporate actions** (splits, bonuses, dividends) | Medium | Unadjusted 1:10 split looks like a 90% crash; mean-reversion signals will feast on it |
| 4 | **Point-in-time index/universe membership** | **Hard** | Today's list on 2019 data pre-selects winners |

### 10.1 Frequency was never the problem — data *shape* is

Daily OHLCV is trivially available. The issue is what shape the source gives it in:

- **Free APIs are symbol-oriented.** You ask for `RELIANCE`, you get RELIANCE. There is
  no query for "every stock that traded on 2018-03-15," and **no way to discover a stock
  you don't already know existed.** So the universe gets built from today's listings, and
  every name that delisted, merged, or was suspended is structurally **invisible** — not
  incomplete, invisible. A symbol-oriented API *cannot* be survivorship-free.
- **Bhavcopy is date-oriented.** Each file is a snapshot of the full cross-section that
  traded that day. Delisted names are in the 2018 files **by construction**, because they
  traded in 2018. **Survivorship-free by construction.**

This is a data-*structure* difference, not a data-quality one, and it is the whole
ballgame.

### 10.2 The India-specific magnitude (quantified)

Study on NIFTY Smallcap 250 — 1,437 stocks, 2016–2025 (Ranse, SSRN 5833162 /
arXiv 2603.19380):

| Metric | Finding |
|---|---|
| **Universe turnover over 9 years** | **82.5%** (16.1% delisted, 33.1% graduated up, 33.2% demoted) |
| Survivor-only backtest return | 26.17%/yr |
| True universe return | **21.23%/yr** |
| **Overstatement** | **4.94pp — a 23.3% inflation** |
| Sharpe overstatement | +0.097 |

**82.5% turnover** means today's Smallcap 250 contains ~44 of the 250 names that were in
it in 2016. Backtesting 2016 with today's list = testing 44 pre-selected survivors.

**Why this hits our strategy harder than it hit that study:** that study was essentially
passive. **Ours ranks stocks and buys the extremes.** Delisted names cluster
overwhelmingly in the worst-performer bucket — precisely where a mean-reversion or
reversal signal buys. Every stock that "reverted" survived into the data; every one that
kept falling to zero is gone. **A reversal strategy on survivor-only data will look
fantastic for reasons unrelated to edge** — and reversal is the first thing we'd
naturally test.

**So 23.3% is the floor of the bias, not the estimate.**

### 10.3 Sources (decided)

Bhavcopy archives — all date-oriented, all survivorship-free by construction:

| Source | Notes |
|---|---|
| `nseindia.com/all-reports` | Official, authoritative |
| **getbhavcopy.com** | Free NSE+BSE EOD downloader, pulls from exchange servers |
| **github.com/tilak999/NSE-Data-bank** | Historic + daily NSE equity bhavcopy, updated daily |
| SAMCO archive | Hosted from 1 Apr 2016 |
| `nser` (R) | Bhavcopy from 1 Jan 2016 |

Corporate actions (#3): **`nsefin`** (`get_corporate_actions()` with dividend/split/bonus
filters), `NseUtility`, or BSE's site as CSV.

### `eod2` audit — DONE 2026-08-21. **VERDICT: DO NOT ADOPT.**

Audited [`BennyThadikaran/eod2`](https://github.com/BennyThadikaran/eod2) as a candidate
ingestion layer. **Fails both criteria.**

**Criterion 1 — delisted names: FAIL, by design.** `src/defs/defs.py:1114`:

```python
def cleanOutDated():
    """Delete CSV files not updated in the last 365 days"""
    deadline = dates.today - timedelta(365)
    for file in DAILY_FOLDER.iterdir():
        if lastUpdated < deadline:
            file.unlink()          # deletes the stock's entire history
```

Not a bug — a maintained feature. Confirmed empirically against the data repo:
`jetairways`, `dhfl`, `manpasand`, `gitanjali`, `videoind` → **404**;
`reliance`, `tcs` → 200. **A survivorship-bias generator on a schedule.**

The README's "data since 1995" makes this *worse*: 30 years with failures continuously
purged is more misleading than 10, because more names have had time to die.

*(In fairness: eod2 targets charting/TA on currently-tradeable stocks, where purging dead
symbols is sensible. Its "ideal for backtesting" claim is simply wrong on this dimension.)*

**Criterion 2 — split adjustment: FAIL.** `defs.py:828` rounds adjusted historical prices
to the ₹0.05 tick:

```python
df.loc[:, col] = ((df[col] / adjustmentFactor / 0.05).round() * 0.05).round(2)
```

Error scales inversely with price — and **the adjustment itself creates the danger zone**
(a 1:10 split drops a ₹200 stock's history to ₹20). It also compounds: each split
re-divides and re-rounds already-rounded history.

| Adjusted price | Quantization error |
|---|---|
| ₹1000 | 2.5 bps |
| ₹100 | 25 bps |
| ₹20 | **125 bps** |
| ₹5 | **500 bps** |

Against a budget of 8–15bps costs and signals worth tens of bps, **this swamps the signal
in exactly the mid/small caps where the edge lives.**

Mostly mean-zero noise → attenuates measured edge (you'd reject good strategies). But the
bad case is specific to our likely first hypothesis: **a ₹10 stock shows fake ±2.5% moves
from quantization alone; a reversal strategy buys those artificial extremes and "reverts"
— spurious backtest profit that cannot be realised.**

Three further structural problems:
1. **Destructive in-place mutation** of history — violates §5 ("adjustment on read").
2. **Event-triggered, not derived.** `adjustNseStocks()` scans actions *for the current
   date only*; a missed run means a permanently missed adjustment, with no way to rebuild
   or verify from raw + actions.
3. **Split factors regex-parsed from NSE free-text "purpose" strings.** The code itself
   logs `"Unable to verify adjustment on {sym} - Please confirm manually"`.

**Salvage from it:**
- **`src/defs/symbol_tracker.py`** (226 lines) — maintains `isin2hist` with
  `from_date`/`to_date`/`action` per ISIN, tracking symbol renames and mergers. Solves a
  real problem we hadn't designed for; the data model is right. **Reimplement the idea.**
- **Delivery data** — eod2 stores NSE deliverable-quantity % alongside OHLCV. India-specific,
  no US analogue, plausible signal (low delivery % = speculative churn, high = genuine
  accumulation). **Added as a hypothesis candidate in §4.**
- Useful reference for NSE endpoint quirks, holiday handling, bhavcopy URL patterns.

**Consequence: build ingestion from raw bhavcopy. The 2–3 weekends are unavoidable.**

### Source audits — `NSE-Data-bank` **PASS**, `getbhavcopy` reject (2026-08-21)

**Generalisable lesson from the eod2 failure: raw bhavcopy files are survivorship-free by
construction, but *tools wrapped around them may not be*.** eod2 ingested good data and
then pruned it. So every candidate source gets the same two tests.

**✅ [`tilak999/NSE-Data-bank`](https://github.com/tilak999/NSE-Data-bank) — ADOPT as the
archive source.** Stores **raw NSE bhavcopy files, one per trading date**, auto-updated
daily via GitHub Actions. Date-organised, so:
- Survivorship-free by construction — a date file is immutable once written; there is no
  mechanism by which a dead symbol could be removed
- No adjustment applied at all → **no quantization corruption** (we do our own adjustment,
  on read, as §5 requires)
- Coverage: files observed 2011→2026; README claims 2+ decades

**Empirically verified** against `data/sec_bhavdata_full_01012018.csv`:

| Symbol | Fate | Present in 2018 file? |
|---|---|---|
| `JETAIRWAYS` | delisted 2019 | ✅ yes — closed ₹841.75 |
| `DHFL` | delisted 2021 | ✅ yes — closed ₹590.85 |
| `GITANJALI` | suspended | ✅ yes |
| `MANPASAND` | suspended | ✅ yes |

**❌ `getbhavcopy.com` — reject.** Windows GUI app requiring .NET 4.0, exporting to
Metastock/Amibroker/Fcharts — i.e. per-symbol series for TA platforms, not a scriptable
research pipeline. Site is silent on delisted names, corporate actions, and survivorship.
Non-starter on macOS regardless.

### Bhavcopy schemas — two eras, and they differ

**`data/sec_bhavdata_full_DDMMYYYY.csv`** (newer):
```
SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, LAST_PRICE,
CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY, DELIV_PER
```

**`historic_data/cmDDMMMYYYYbhav.csv`** (older):
```
SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,
TOTALTRADES,ISIN,
```

Useful fields we now know we have:
- **`TURNOVER_LACS`** → directly gives the §10.4 liquidity metric. No computation needed.
- **`AVG_PRICE`** → the day's VWAP. A far more defensible fill assumption than close.
- **`DELIV_QTY` / `DELIV_PER`** → the delivery signal from §4.
- **`NO_OF_TRADES`** → microstructure / liquidity input.

**Four concrete ingestion gotchas discovered:**

1. **`SERIES` must be filtered.** `DHFL` appears **8 times** on 01-Jan-2018 — once as `EQ`
   and seven times as debt series (`N6, N8, NC, NF, NL, NN, NP`). Unfiltered, NCDs get
   treated as equity. Use `SERIES == 'EQ'`; note `SERIES == 'BE'` is trade-for-trade and
   maps to the §10.4 exclusion.
2. **Whitespace changed between eras.** The 2018 file is `SYMBOL,SERIES,DATE1`; the 2024
   file is `SYMBOL, SERIES, DATE1` with leading spaces in values (`" EQ"`, `" 01-Jul-2024"`).
   A naive parser silently matches nothing. Strip on read.
3. **`ISIN` exists only in the OLD format.** `sec_bhavdata_full` has no ISIN column, so the
   §10.5 ISIN-identity requirement needs the `historic_data` files or a separate mapping
   for recent years.
4. **Newer fields are zero-filled in backfilled old files.** In the 2018 file `AVG_PRICE`,
   `NO_OF_TRADES`, `DELIV_QTY`, `DELIV_PER` are all `0`; in the 2024 file they are populated
   (RELIANCE delivery 53.99%, TCS 58.27%). **The delivery signal is only testable from
   whenever real population begins** — start date not yet determined.

**Universe size sanity check:** EQ-series rows = **1,417 (2018) → 1,914 (2024)**. Selecting
top-300 from ~1,400–1,900 tradeable names is comfortable.

**Gap the repo itself flags:** its README notes missing-data tracking as unimplemented
("may be NSE was down or there was actual holiday"). **We must cross-check ingested dates
against a trading calendar** — a silently missing date corrupts every trailing-window
feature computed across it.

**History constraint: most free bhavcopy archives start ~2016**, so ~10 years total. Good
regime diversity (2018–19 slowdown, 2020 crash, 2022 drawdown, 2023–24 bull), but it caps
how much can ever be proven — see §8.4.

**Kite Connect is the wrong tool for research history.** Its historical data API is a paid
add-on (~₹2,000/mo) and is **symbol-oriented**, so it inherits the §10.1 survivorship
problem and won't serve delisted names. **Bhavcopy for research history, Kite for live
data later.** Two tools, two jobs.

### 10.4 Universe definition (DECIDED — do not tune)

| Decision | Value | Why |
|---|---|---|
| Size | **Top 300 by liquidity** | Enough breadth; avoids the microcap data swamp |
| Liquidity metric | **Trailing 20-day median turnover (₹)** | Volume in *shares* is meaningless across price levels |
| Rebalance | **Monthly** | Weekly adds turnover for no benefit |
| Min turnover floor | **₹5 crore/day** | Below this, fills are fiction |
| Exclusions | ASM/GSM, trade-for-trade (`SERIES == 'BE'`), circuit-locked on signal day, **and ETFs** | Untradeable, or not a stock. ETFs sit in `SERIES == 'EQ'` — see §10.8 |
| Delisted names | **Retained in history, excluded from forward selection** | The whole point |

> **⚠️ Universe definition is itself a trial.** Trying 200/300/500 and keeping whichever
> backtests best spends three trials and introduces a fresh overfit. **Define it once from
> liquidity reasoning, commit it to config, and never tune it against results.** Same
> applies to rebalance frequency and the turnover floor.

### 10.5 Other data-layer requirements

- **Track identity by ISIN, not by symbol.** Symbols get renamed; companies merge. Keep an
  `isin → [{symbol, from_date, to_date, action}]` history so a rename doesn't look like one
  stock dying and another being born. *(Requirement discovered via the §10.3 eod2 audit —
  its `symbol_tracker.py` is a good reference model.)*
- **Config stores the RULE, never the list of names.** "Top 300 by trailing 20-day median
  turnover as of date D" is a rule evaluated point-in-time. A hand-maintained list of
  tickers is a survivorship-bias generator.
- **Raw prices + separate action-event table**; adjusted series computed at read time.
  Never mutate raw prices.
- **Fill timing must be modelled exactly: signal on close of day D, fill at OPEN of
  D+1**, with slippage. This is subtle and expensive to get wrong — for short-term
  reversal signals **most of the reversion happens in the overnight gap.** Filling at
  close-of-D captures it; in reality you receive the gap and capture nothing. Same
  signal, opposite conclusion. One of the most common reasons Indian retail backtests lie.
- **Bhavcopy lands ~6–7pm IST and is sometimes late.** Cron needs retry-with-backoff and
  a hard "data unavailable → emit no recommendations" path. Silent stale-data
  recommendations are worse than none.

### 10.6 Corporate actions — VERIFIED 2026-08-21. `nsefin` **ADOPT**, with two traps.

Tested `nsefin` 0.1.1 `get_corporate_actions(from, to, subject_filter)`.

**Coverage: PASS.** Historical lookback verified working to **2011** (114–126 rows per
2-month window, split/bonus rows present throughout) — matches the bhavcopy archive span.
Dividends, splits, bonuses, buybacks, demergers all present.

**Schema:** `bcEndDate, bcStartDate, caBroadcastDate, comp, exDate, faceVal, ind, isin,
ndEndDate, ndStartDate, recDate, series, subject, symbol`

**Factor extraction — the subject text is highly structured, not free-form:**
- Splits: `"Face Value Split (Sub-Division) - From Rs 10/- Per Share To Re 1/- Per Share"`
  → factor = 10 / 1
- Bonuses: `"Bonus 1:1"`, `"Bonus 1:2"`, `"Bonus 2:1"` → factor = 1 + a/b
- Dividends: `"Dividend - Rs 18 Per Share"`

*(Caveat: `nsefin` is at v0.1.1. **Pin the version.** Don't assume API stability.)*

> #### ⚠️ TRAP 1 — `faceVal` is the CURRENT face value, NOT the post-action value.
>
> Evidence: `MGEL` ex-18-Aug-2022 has `faceVal = 1` while its subject reads *"From Rs 10/-
> To Rs 2/-"*. The stock split again afterwards, and the field reflects today. Cross-checked:
> `TATASTEEL faceVal=1`, `TORNTPHARM faceVal=5`, `UNOMINDA faceVal=2` — all equal their
> *current* face values.
>
> **Never derive the split factor from `faceVal`.** Parse the subject's from→to. Using
> `faceVal` gives a silently wrong answer for every stock that split more than once.

> #### ⚠️ TRAP 2 — the action feed uses TODAY's symbol; bhavcopy uses the symbol of the day.
>
> Evidence: the feed lists a 1:1 bonus for **`UNOMINDA`**, ex-07-Jul-2022. But in July 2022
> that stock traded as **`MINDAIND`**:
>
> ```
> MINDAIND, 06-Jul-2022, PREV_CLOSE 970.35 ... CLOSE 969.20
> MINDAIND, 07-Jul-2022, PREV_CLOSE 969.20, OPEN 530.00, CLOSE 496.55
> ```
>
> **A join on `SYMBOL` returns zero matches. The bonus is silently dropped and MINDAIND
> retains a phantom −49% single-day crash forever.** No error is raised.
>
> **Join corporate actions to price history on ISIN, never on symbol.**
>
> **Why this is lethal for *our* strategy family specifically:** a phantom −49% day lands
> at the very top of a "buy the biggest losers" ranking. Missed corporate actions don't add
> noise — **they inject fake extremes into precisely the tail a cross-sectional strategy
> selects from.** With ~50–100 splits/bonuses a year in a 300-name universe, this is a
> recurring, systematic contaminant, not an edge case.

#### Mandatory reconciliation: feed factor × price discontinuity

Price data reliably **detects** actions but cannot reliably **derive** the factor:

| Case | Implied ratio (PREV_CLOSE/CLOSE) | True factor | Error |
|---|---|---|---|
| TATASTEEL 1:10 split | 9.56 | 10.0 | 4.4% |
| MINDAIND 1:1 bonus | 1.952 | 2.0 | 2.4% |

Detector quality is excellent though — scanning 28-Jul-2022 with a `|ratio−1| > 0.25`
threshold gave **1 hit in 1,805 EQ symbols, zero false positives.**

**So: take the factor from the feed, and verify it against the price discontinuity.** Two
mechanical checks, both required:

1. **Action in feed but no price discontinuity** → wrong ex-date or bad feed row.
2. **Price discontinuity with no matching action** → **MISSING ACTION. Halt and queue for
   review.** *This is the check that catches the MINDAIND class of failure.*

Check 2 needs a human review queue, not auto-adjustment — a genuine 30% news gap will
false-positive, and auto-"adjusting" a real price move would be worse than the disease.

**This reconciliation is exactly what eod2 lacks**, and it is why its regex-parsed factors
can corrupt history silently. It becomes **test #7** in the §6 list.

#### Dividends matter more than initially assumed

Unadjusted dividends depress measured returns by ~1–1.5%/yr — but worse, they bias
**cross-sectionally against high-dividend stocks**, which is systematic distortion in a
ranking strategy rather than noise. The feed carries per-share amounts, so adjust for them.

### 10.7 The `PREV_CLOSE` chain — one primitive, three problems solved

**Key architectural finding (2026-08-21).** `PREV_CLOSE` in the bhavcopy chains exactly to
the prior session's `CLOSE` — verified across many consecutive sessions. That single
invariant is the detector for the three hardest data problems at once:

```
assert today.PREV_CLOSE == prev_session.CLOSE
```

| Violation pattern | Means |
|---|---|
| Ratio ≈ a simple fraction (2, 1.5, 10 …) | **Corporate action** (§10.6) |
| Symbol vanished, another appeared with matching `PREV_CLOSE` | **Symbol rename** (below) |
| Chain breaks with no action and no rename | **Missing date** in the archive |
| Vanished, nothing matches | Delisting *(correct — keep the history)* |
| Appeared, nothing matches | New listing *(correct)* |

Build this as one integrity primitive in `io/` and all three checks come free.

#### Symbol renames — reconstructible without any ISIN source

**Verified exactly** on the MINDAIND → UNOMINDA rename:

```
04-Aug-2022   MINDAIND   close      = 529.05
05-Aug-2022   UNOMINDA   prev_close = 529.05      <- exact match, not approximate
```

**Algorithm:** for each date `D`, pair every newly-appearing symbol with every symbol that
vanished on `D−1` where `new.PREV_CLOSE == old.CLOSE`.

**Three-layer ISIN strategy (resolves the §11 blocker):**

| Period | ISIN source | Status |
|---|---|---|
| 2011 → ~Jun 2021 | `historic_data/cm*bhav.csv` carries ISIN + symbol-of-the-day | **Verified present** |
| Today | `nsefin.get_equity_list()` — 2,553 rows: `SYMBOL, ISIN NUMBER, DATE OF LISTING, FACE VALUE` | **Verified** |
| ~Jul 2021 → present | **Reconstruct from `PREV_CLOSE` continuity** — no external source needed | **Method verified** |

*(Old format confirmed present through Jun 2021, absent from Jul 2021 onward.)*

> **The validation setup is the best part: 10 years of ISIN-labelled ground truth
> (2011–Jun 2021) exists to test the rename detector against *before* applying it to the
> unlabelled 2021→present gap.** Proper train/validate for a data-quality tool.

Caveats to handle:
- **Require a unique match.** If two symbols vanish and two appear the same day at similar
  prices, flag for review rather than guess. Use turnover as a secondary key.
- **Rename + corporate action on the same day** breaks exact continuity (`PREV_CLOSE` is
  unadjusted, the new symbol trades adjusted) — also allow a simple-ratio match.

#### Missing dates are real, not theoretical — verified

**`sec_bhavdata_full_08082022.csv` returns 404, but 08-Aug-2022 was a trading day.** Proof
from the chain: UNOMINDA closed 521.40 on 05-Aug and its `PREV_CLOSE` on 10-Aug was 549.50
— so a session closed at 549.50 in between. 09-Aug-2022 was Muharram (genuine holiday), so
the missing session is 08-Aug.

**NSE-Data-bank has real gaps.** An authoritative trading calendar is required to
distinguish "missing file" from "holiday" — otherwise every trailing-window feature
spanning the gap is silently computed over the wrong window.

*(Also: `get_equity_list()` column names carry leading spaces — `' SERIES'`,
`' ISIN NUMBER'`. Same whitespace gotcha as §10.3 #2. Strip everywhere.)*

### 10.8 Findings from the first real ingestion run (2026-08-21)

Archive cloned: **1.5 GB, 3,995 `sec_bhavdata_full` + 2,706 `cm*bhav` files (~16 years).**
Parser and integrity checks built and validated. Four new defects found, none of which
raises an error anywhere.

#### 🔴 76 of 3,995 files (1.9%) are stale duplicates named after NSE holidays

The filename/content date guard caught this on its first run.
`sec_bhavdata_full_15082022.csv` contains **12-Aug-2022** data — and 15-Aug-2022 was
Independence Day. The scraper ran on holidays, received the last available bhavcopy, and
saved it under the holiday's date.

Every mismatched date is a holiday: 01-May (Maharashtra Day), 02-Oct (Gandhi Jayanti),
Mahashivratri, and so on. Distribution by year — **2021: 8, 2022: 14, 2023: 15, 2024: 16,
2025: 13, 2026: 10** — i.e. it begins when the automated downloader started; earlier files
were backfilled correctly.

Ingested naively this yields duplicate sessions, phantom trading days, spurious 0% return
days, and trailing windows spanning one fewer real session than they claim.

**Mitigation: reject any file whose rows disagree with its filename's date.** Implemented
in `io/bhavcopy.py`; the panel builder must catch and record rather than abort.

**Silver lining: these defects are a free NSE holiday calendar** (2021 onward) — the set of
dates where a file exists but its content is stale *is* the set of days the scraper
believed were sessions and weren't.

#### 🔴 Missing sessions are more common than the one we found by hand

In a 39-session sample (Jul–Aug 2022) there were **two** missing sessions: 2022-07-12 and
2022-08-08. Extrapolated, this is a recurring rather than rare defect.

**Detection needs no external calendar** (resolves most of open item #6):

| Situation | Chain-break rate |
|---|---|
| Missing archive file | **~97% of symbols** |
| Genuine holiday | **0%** |

A missing file breaks `prev_close[i] == close[i-1]` for nearly every symbol at once; a
holiday breaks nothing, because NSE reports the last *traded* close. Verified: 15-Aug-2022
(holiday) correctly produced no flag while 08-Aug-2022 (real session, absent) produced 97%.

#### 🟡 Listing days must be excluded from action detection

`SYRMA` listed 2022-08-26 with `prev_close = 220` (the **IPO issue price**, not a market
close) and closed at 310.50 — a 41% gap that reads as a corporate action. **Skip each
symbol's first row.** Implemented.

#### 🟡 ETFs live in `SERIES == 'EQ'`

`MOLOWVOL`, `MOMOMENTUM`, `ICICIMOM30` all surfaced as action candidates — they are ETFs
undergoing unit splits, sitting in the EQ series alongside ordinary shares. **An equity
universe must exclude them**; `nsefin.get_etf_list()` provides the list. Added to §10.4's
exclusion rules.

#### §10.6 Trap 2 confirmed twice more

`SONAMCLOCK` in the 2022 panel appears in the corporate-action feed as **`SONAMLTD`**
(today's symbol). Second independent instance after MINDAIND→UNOMINDA. The symbol-join
failure mode is routine, not exceptional.

#### Validation results — Jul–Aug 2022, 39 sessions, 70,672 rows

| Check | Result |
|---|---|
| Action candidates | **17**, every one corroborated by the `nsefin` feed |
| Missing sessions | **2** (2022-07-12, 2022-08-08) |
| Renames detected | **6**, all correct, **0 ambiguous** |
| Chain breaks | 3,542 — **exactly** 1,768 + 1,774, the two missing sessions |
| **Unexplained chain breaks** | **0** ← the data-quality score |

Renames found, all genuine: `ZUARIGLOB→ZUARIIND`, `TMRVL→HEADSUP`,
`COSMOFILMS→COSMOFIRST`, `NBVENTURES→NAVA`, `MINDAIND→UNOMINDA`, `MODISNME→MODISONLTD`.

#### Adjustment asymmetry across fields

| Field | On a split/bonus of factor `f` |
|---|---|
| `OPEN/HIGH/LOW/CLOSE/PREV_CLOSE/AVG_PRICE` | **divide** by `f` |
| `TTL_TRD_QNTY`, `DELIV_QTY` | **multiply** by `f` |
| `TURNOVER_LACS` | **unchanged** (rupee value is invariant) |
| `DELIV_PER` | **unchanged** (it's a ratio) |

**This retroactively validates §10.4's choice of turnover over volume as the liquidity
metric — turnover needs no split adjustment at all.** Had we ranked liquidity by share
volume, every split would have corrupted the universe selection.

---

## 11. Decisions Log & Open Items

### Resolved

| # | Item | Decision |
|---|---|---|
| 1 | **Data sourcing** | Bhavcopy archives (date-oriented, survivorship-free by construction). **`eod2` audited 2026-08-21 → rejected** (deletes delisted names; rounds adjusted prices to ₹0.05 tick). **Build ingestion ourselves.** See §10.3 |
| 2 | **Evaluation protocol** | Unlimited dev-set trials 2016–2021; **3–5 holdout looks total** on 2022–2026. See §8 |
| 3 | **Universe** | Top 300 by trailing 20d median turnover, monthly rebalance, ₹5cr floor. **Not to be tuned.** See §10.4 |
| 4 | **Broker** | **Zerodha / Kite.** Rate card to be supplied by Akash → parameterises `config/costs.yaml`. Kite for live data only, *not* research history |
| 5 | **Regulatory** | **Zero SEBI algo surface in Phase 0.** Recommendations + manual order placement = no API automation, nothing to register. Becomes relevant only if order placement is automated (Phase 0d/1), and even then a few orders/day sits far below any rate threshold |
| 6 | **Storage** | Parquet + DuckDB (market data); SQLite → Postgres (portfolio state). **Not Mongo** — see §6 |
| 7 | **Build order** | Data layer + backtester + research in notebooks FIRST. Service/cron/UI only after a strategy clears the gates |

### Open

1. ~~Data source audit~~ — **RESOLVED 2026-08-21.** `eod2` rejected, `getbhavcopy`
   rejected, **`NSE-Data-bank` adopted** and delisted-name retention empirically verified.
   See §10.3.
2. ~~Zerodha rate card~~ — **RESOLVED 2026-08-21.** Fetched, worked examples computed,
   §3 corrected. Delivery ≈ 30 bps round trip, not the 8–15 bps assumed earlier.
3. ~~Corporate-action coverage~~ — **RESOLVED 2026-08-21.** `nsefin` verified: coverage to
   2011, structured subject text, dividends included. **Two traps found** (`faceVal` is
   current-not-post-action; feed uses today's symbol while bhavcopy uses the symbol of the
   day). Reconciliation test now mandatory. See §10.6.
4. **Delivery-data start date** — determine when `DELIV_QTY`/`DELIV_PER` become genuinely
   populated (zero-filled in 2018, real in 2024). Bounds the testable window for the
   delivery hypothesis in §4.
5. ~~ISIN → historical-symbol mapping~~ — **RESOLVED 2026-08-21.** Three-layer strategy:
   old `cm*bhav.csv` (ISIN present, 2011→Jun 2021), `nsefin.get_equity_list()` (today), and
   **`PREV_CLOSE`-continuity rename detection** for the Jul 2021→present gap — verified exactly
   on MINDAIND→UNOMINDA. 10 years of ISIN-labelled data available to validate the detector.
   See §10.7.
6. ~~Trading calendar~~ — **mostly RESOLVED 2026-08-21.** Missing sessions are detectable
   from the data alone (~97% chain-break rate vs 0% for holidays, §10.8), so no external
   calendar is needed to *find* them. Two independent holiday sources also fell out: the 76
   stale-duplicate filenames (2021+) and, for earlier years, dates absent from the archive
   with no chain break. **Residual open question:** whether to backfill the ~2 missing
   sessions per 39 observed directly from NSE, or drop them and let the integrity check
   mark affected windows unusable.
7. **Deflated-Sharpe implementation detail** — the trial-count haircut from §8.2 needs a
   concrete formula in `backtest/evaluate.py`.
8. **Sector classification source** — needed for sector caps in `portfolio.py` and for
   sector-relative signals. Not yet sourced.
9. **Verify before relying on** — ASM/GSM list access, buyback tender-route rules (SEBI has
   been reshaping these), F&O lot sizes/margins (only matters at the leverage stage).
   *(STT/GST now captured in §3.)*

---

## 12. Honest Probability of Success

| Stage | Odds |
|---|---|
| Find something that backtests beautifully | ~90% *(the danger, not the good news)* |
| Survives walk-forward + OOS + real cost modelling | ~25% |
| Survives 6 months of live paper trading | ~50% of those |
| **Net: a genuinely tradeable edge after 12 months** | **~10–15%** |

**Accept this before starting.** If a >50% chance of "there is no edge I can capture at
retail cost structure" is unacceptable, the project shouldn't start — because the
pressure to make it work anyway is exactly what produces overfit backtests and blown
accounts.

### Where these projects actually die

**Month five.** Novelty gone, six hypotheses dead, nothing working. A scope that fits
real weekly hours is the main thing that gets you through it.

### The engineering is not the blocker

The v1 research platform is ~3–6 weekends of work for a competent engineer. Your skills
make the easy 10% easy. **Budget effort accordingly and resist polishing infra as
procrastination from research.**

### Base rate you're competing against

- SEBI: ~93% of individual F&O traders lost money FY22–FY24 (₹1.8 lakh crore aggregate)
- SEBI: ~71% of intraday cash traders lost money in FY23
- Same period: prop desks and FPIs — overwhelmingly algo-driven — booked tens of
  thousands of crores gross

You are choosing to enter as the counterparty to the people who win. **"I'll be careful
and use stop losses" is not a reason you'll be in the minority.** Low turnover,
capacity-constrained niches, honest cost modelling, and mechanical anti-overfitting
discipline are.

---

## 13. References

**Survivorship bias (India-specific, quantified — §10.2)**
- Ranse, *Survivorship Bias in Emerging Market Small-Cap Indices: Evidence from India's
  NIFTY Smallcap 250* — [SSRN 5833162](https://papers.ssrn.com/sol3/Delivery.cfm/5833162.pdf?abstractid=5833162&mirid=1)
  / [arXiv 2603.19380](https://arxiv.org/abs/2603.19380)

**Data sources (§10.3)**
- [Getbhavcopy](https://www.getbhavcopy.com/) — free NSE/BSE EOD downloader
- [NSE-Data-bank](https://github.com/tilak999/NSE-Data-bank) — historic + daily bhavcopy
- [eod2](https://github.com/BennyThadikaran/eod2) — NSE downloader with split/bonus
  adjustment *(audit before adopting)*
- [nsefin](https://pypi.org/project/nsefin/) — corporate actions via
  `get_corporate_actions()`
- [Fetching NSE corporate actions — FabTrader](https://fabtrader.in/how-to-fetch-corporate-actions-data-bonus-splits-dividends-buy-back-from-nse/)
- [SAMCO bhavcopy archive](https://www.samco.in/bhavcopy-nse-bse-mcx) — from 1 Apr 2016
- `nseindia.com/all-reports` — official source

**Concepts to read up on**
- Deflated Sharpe Ratio — Bailey & López de Prado (the §8.2 haircut)
- Jegadeesh & Titman (1993) — cross-sectional momentum
- Novy-Marx — quality/profitability factor
- Post-earnings announcement drift (PEAD) literature
