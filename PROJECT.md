# Algo Trading System — Project Plan

**Status:** DEPLOYED (paper) 2026-08-23 — research closed, system live on GitHub Actions
**Created:** 2026-08-21
**Last updated:** 2026-08-23
**Owner:** Akash Ranjan
**Time budget:** 10–15 hrs/week

---

## 0. Current State — read this first

**Status: research closed, system DEPLOYED to paper trading 2026-08-23.**

`paper_momentum_v1_001` runs unattended on GitHub Actions against MongoDB
Atlas, with Telegram reporting and a healthchecks.io dead-man switch. The
10-session acceptance gate is in progress. **No real money is committed and no
code places orders.**

The sections below are the plan as written. **§11 through §11p are the findings, and they
have moved a long way from the plan.** This is the summary; the detail is in §11o, §11p
and §12.

### Where the research landed

**Momentum works and is the only candidate that clears the benchmark by a margin worth
having.** Measured on the audited engine over the full archive (2012-02 → 2026-08):

| | Pure Momentum | 25/75 Mom + LowVol | NIFTY buy & hold |
|---|---|---|---|
| CAGR | **22.4%** | 14.8% | 11.7% |
| After-tax CAGR | **18.3%** | 12.0% | 10.9% |
| Max drawdown | **−53.1%** | −36.4% | −37.8% |
| Longest drawdown | 1,083 days | 742 days | 423 days |
| 5Y windows beating NIFTY | **100%** | 91% | — |
| 10Y SIP XIRR, ₹2L + ₹20k/mo (median) | **20.2%** | 12.6% | 13.0% |
| 10Y SIP XIRR (worst of 54 starts) | **15.4%** | 10.2% | 10.1% |

**The 25/75 momentum/low-vol blend is eliminated** (§11o). It was the leading candidate for
two days and it does not survive contact with tax and realistic rebalancing: it loses to
buy-and-hold on SIP XIRR (12.6% vs 13.0%), fails to beat the index in 30–53% of rolling
windows, trails NIFTY for *longer* than momentum does (4.8 years vs 3.3), and has an
after-tax Martin ratio **below** the index.

### 🟢 The decision: deploy 50% momentum / 50% NIFTYBEES

Not 100% momentum, despite momentum's higher return. §11o's position-sizing study found that
**return per unit of maximum drawdown is flat at 0.34 from 100% down to 50% momentum** — the
first half of the dilution is free on a risk-adjusted basis — while it buys:

*(All figures below are §11q-corrected: Sharpe and Martin deduct the 6.5% risk-free rate —
these are long-only books — and the index sleeve's tax is deferred rather than charged
annually at 20%.)*

| | 100% momentum | **50/50** | 100% NIFTY |
|---|---|---|---|
| After-tax CAGR | 18.3% | **15.3%** | 10.8% |
| Max drawdown | −53.1% | **−41.5%** | −37.8% |
| Ulcer index | 17.0 | **9.8** | 7.1 |
| **After-tax Martin** | 0.69 | **0.91** ← peak | 0.61 |
| Sharpe | **0.58** | 0.53 | 0.30 |
| **After-tax per unit max DD** | 0.34 | **0.37** ← peak | 0.29 |

**Both risk-adjusted measures peak exactly at 50/50**, and both put it above buy-and-hold.
25/75 gives up too much (after-tax 13.3%, per-unit-DD falls to 0.35) and 75/25 sheds too
little risk.

**Sharpe is the one measure that argues against dilution** — it falls monotonically, 0.58 →
0.53, because the risk-free deduction bites harder as volatility drops. §11q found that an
earlier version of this table showed Sharpe *improving* with dilution; that was an artefact
of reporting return/vol as a Sharpe on a 100%-long book, and the argument is withdrawn.

The deciding reason is behavioural, not statistical: **I would rather hold half the position
for ten years than the full position for three.**

### 🔴 The one rule that must be committed to before any capital is deployed

> **Never sell the book. Pausing contributions is permitted.**

§11p priced every way of quitting (figures §11q-corrected). Stopping contributions is
recoverable — it costs 26.9% of terminal wealth if you quit at 12 months, 11.0% at 24, and
**3.6% at 36**, because a quitter who holds on has already absorbed the damage and is near
the recovery. **Selling costs 27–40% whenever you do it**, because it crystallises the tax
*and* forfeits the recovery permanently.

Quitting at the trough — the moment it genuinely feels most rational — costs a median of
**14.6%** if you merely stop contributing and up to **49.8%** if you sell.

This rule has to be written down now, because **it cannot be decided in month 30.**

### The three things that could still sink it

1. **~1.4 independent 10-year windows.** The strongest result is close to a single
   observation (§11n).
2. **One bear year in fourteen (§11i).** Momentum's global worst case is a bear-market
   *recovery* — US momentum lost ~80% in 2009 — and no such episode exists in this data. The
   −53% is the *benign-sample* figure.
3. **Behaviour, not the factor.** Over a ten-year hold, **100% of start months contain a
   >3-year underperformance streak** (§11o) — it is a certainty, not a risk. §11p prices the
   assumption at roughly a third of the strategy's value. The 50/50 allocation halves the
   depth of the shortfall that triggers selling; it does *not* shorten the streak
   (1,203 → 1,198 days), because duration is set by when the factor turns, not by position
   size. **The behavioural problem cannot be sized away, only made survivable.**

### Instrument reliability — the honest record

Four audits, **~55 defects**, and the rate is not falling. Every consequential one was
caught by an implausible *number*, never by reading code:

| Audit | What it invalidated |
|---|---|
| §11f | ~28 defects; a core premise (§11e) |
| §11h | 3 reported results, including the regime filter (0.70→1.07 was an artefact; honest answer is 0.91→0.80) |
| §11j | "Monthly rebalance" was trading 92.9% of sessions; every net number in §11g–§11i understated |
| §11q | **Reported "Sharpe" was return/vol on long-only books** — inverted the dilution comparison and hid a second failed gate; the blend's index sleeve was over-taxed; `capitulate` double-taxed |

Two more measurement errors were caught *during* the diagnostics that followed: downside
capture returned exactly 1.00 for every book (§11l), and a row-count vs calendar-span
mismatch inflated benchmark CAGR by 0.9pp (§11k). **Assume the next audit finds something
material.**

### 🔴 Holdout status

`holdout_looks_remaining` still reads 5 and no trial has been registered against it, **but
the 2022–2026 window has been inspected** (§11m, §11n) with explicit authorisation. That is
bookkeeping versus epistemics: the remaining looks are worth less than the counter implies.
Any future "out-of-sample confirmation" on this window is no longer clean for drawdown shape
or rolling-window statistics.

### What has been built since (Phases 1–5, 2026-08-23)

The research plan below stops at "deploy". What actually exists now:

| Phase | Delivered |
|---|---|
| 1 | Domain model + 11 Atlas collections, verified against a real M0 |
| 2 | Reconciliation-driven daily ingestion, config guards, dead-man switch |
| 3 | Signal generation + recommendation engine; **signal parity proven** against the research pipeline |
| 4 | Execution recording, FIFO holdings rebuild, cash ledger, valuation, reconciliation |
| 5 | Backups (**restore rehearsed**), health scoring, acceptance framework |

**860 tests, 93% coverage on new code.** Design docs under `docs/`; operations
under `docs/operations/`.

A **dashboard** was added 2026-09-01 (`dashboard/`, deployed on Vercel). It
lives in this repository rather than beside it because it records executions,
and doing that safely means running the trading system's own
`ExecutionService`, `HoldingsService` and `ReconciliationService` — a second
implementation of the FIFO rebuild and the cash ledger would drift, and the
drift is silent until the book is already wrong. It is read-only until
`DASHBOARD_ALLOW_WRITES` is set. Two of the findings recorded below
(`benchmark_close`, `realised_pnl_ytd`) were found while building it.

Two properties are demonstrated rather than asserted. The second was claimed
here for a week before it was true, and the correction is worth keeping:

* **Signal parity** — the live service reproduces the research pipeline's
  top-30 *exactly*, from a 400-session window versus the full 16-year archive
  (`tests/integration/test_signal_parity.py`). This has caught two real
  divergences already.
* **Self-healing** — **demonstrated 2026-09-01**, on the fourth attempt at
  measuring it honestly. A deliberately injected fault failed
  `generate_signals` on a scheduled run for `2026-08-31`; the next scheduled
  run's backfill claimed attempt 2 for the same date, succeeded, filled the
  gap, and the book reconciled. Nobody pressed anything.

  It was reported as demonstrated from 08-23, and was not. The only qualifying
  evidence was a hand-injected pair for `value_portfolio` — a job that wrote no
  ledger rows at all, so those rows could only have been fabricated. Worse, the
  check asked for two attempts at one business date, which the daily jobs never
  produced: real self-healing had already happened (2026-08-21's data was
  backfilled unaided three days later) and was invisible to it.

  Fixed by making the jobs record per-date failures, recording which GitHub
  event triggered each run so "unaided" is a fact rather than an assumption,
  and excluding the fabricated rows. See `docs/acceptance/10-session-gate.md`.

The general lesson is the one §9.1 already states: *a reconstruction that
reads the snapshot cannot detect a corrupt snapshot.* A self-healing check
that reads a ledger the failing jobs never wrote to cannot detect that they
never wrote to it.

### Immediate next steps — none of them are research

The factor, cost, tax and behavioural questions are answered as well as backtesting can
answer them. Everything remaining needs real orders or elapsed time:

1. **~~Complete the 10-session acceptance gate~~** — **DONE 2026-09-01.**
   13 sessions, reconciliation clean, and self-healing demonstrated on a real
   injected failure: `generate_signals` failed for 2026-08-31 on a scheduled
   run and the next scheduled run repaired it unaided, with the gap actually
   filled. Took four attempts to *measure* honestly — the checker was asking
   for a shape the jobs could not produce, and the only qualifying evidence
   was fabricated. `docs/acceptance/10-session-gate.md` records both the
   result and the caveat that criterion 2 is not met on a strict reading.
2. **~~Write down the never-sell rule and a deliberate drawdown tolerance~~** —
   **DONE 2026-08-23:** [`INVESTMENT-POLICY.md`](INVESTMENT-POLICY.md), written
   before any capital was committed. Records the 50/50 allocation, the five
   behavioural rules, the measured cost of each way of quitting, what would
   genuinely falsify the strategy, and an explicit acceptance of the two failed
   pre-registered gates. **Signed 2026-09-05.**
3. **~~Shadow portfolio~~** — **DONE 2026-09-02.**
   `shadow_momentum_v1_001` exists in `algotrade_paper`, **forked** from the
   paper book rather than started from cash, and reconciled clean. It
   auto-fills every instruction at the reference price inside the rebalance
   workflow, so `paper − shadow` is the cost of your discretion and your fills
   together. Forking was free precisely because all 30 paper executions were
   still auto-fills at the reference price — paper had not yet diverged from
   perfect obedience, so the copy lost nothing. That window is now closed: the
   fork job refuses once the source contains a hand-entered fill.
   **The gap is 0.00% over 13 shared sessions and the dashboard refuses to
   call that a measurement** — the first real number arrives at the first
   September rebalance executed by hand.
   See [`docs/operations/shadow-portfolio.md`](docs/operations/shadow-portfolio.md).
4. **~~A fourth audit~~** — done (§11q, ~13 findings, Sharpe was not a Sharpe).
5. **₹25k live, to measure real fills.** The edge is mid-cap concentrated where the slippage
   model is least tested (§11i). §11o shows ~4× headroom before the edge dies, which is why
   this gates *scaling* rather than *starting*. Gated behind **6+ months** of
   paper trading — see `docs/deployment/production-checklist.md`.
6. **~~Populate `benchmark_close` in `portfolio_snapshots`~~** — **DONE
   2026-09-01.** NIFTYBEES close captured in the same `prices_as_of` lookup as
   the portfolio's own prices, so carry-forward cannot disagree; 13 existing
   snapshots backfilled by setting only that field, with every NAV verified
   byte-identical afterwards. The dashboard charts it rebased to 100. The
   original finding follows.

   **Was an open bug, not a feature.** The field was declared in the schema
   and **written by nothing** —
   0 of 11 snapshots have a value, and `data/indices/` does not exist. So the
   system currently cannot answer *"how am I doing against NIFTY?"*, which is
   the exact number `INVESTMENT-POLICY.md` §4 is built around: the five
   historical underperformance streaks, the 1,198-day worst case, and Rule 2
   ("underperformance versus NIFTY is not a reason to sell") are all
   unmeasurable against live data until this exists.

   NIFTYBEES is NSE-listed, so its close is **already in the bhavdata ingested
   daily** — this is a small change to `ValuationService` plus a backfill of
   the existing snapshots, not a new data source. Found 2026-09-01 while
   verifying the dashboard plan, which wanted to chart it.

   **Deliberately deferred**, not forgotten: `value_portfolio` is the job that
   must run cleanly to close the self-healing acceptance criterion, and it
   should not be modified mid-gate. Do this once the gate closes.
7. **~~`realised_pnl_ytd` is not year-to-date~~** — **DONE 2026-09-01.**
   Renamed to `realised_pnl`, since lifetime is a perfectly good thing to
   store and holdings are rebuilt from executions anyway; a yearly figure is a
   *query* over them, not a stored field that must be kept true. All 30 stored
   documents migrated on the next rebuild (`save` uses `replace_one`), with a
   fallback read for the transition and the old name kept in the money-field
   declaration so nothing became a float. The original finding follows.

   **Mislabelled field.**
   `HoldingsService.rebuild` replays the **entire** execution history and
   accumulates `realised_pnl_ytd += sale.gain` with no year boundary, and
   nothing anywhere resets it. The field is therefore **lifetime** realised
   P&L wearing a name that asserts an annual reset. `value_portfolio` sums it
   into each snapshot's `realised_pnl`, so the snapshot inherits the same
   error.

   Harmless *today*: the book has 30 buys and zero sells, so the figure is
   zero. It becomes wrong at the first sale, and actively misleading after
   **1 April 2027**, when a reader — or a tax return — would reasonably take
   "YTD" at face value.

   Two defensible fixes, and the choice matters: either rename it to
   `realised_pnl` (lifetime is a perfectly good thing to store, and holdings
   are rebuilt from history anyway), or make it genuinely per-financial-year.
   Indian FY bounds already exist in
   `dashboard/app/repositories/executions.py` — 1 April to 31 March,
   half-open — because the trading system had no notion of a financial year
   at all. Renaming is the smaller change and probably the right one: the
   *derived* yearly number belongs in a report, not in a stored field that
   must be kept true.

   Found 2026-09-01 while building the dashboard's FY tax export. Deferred
   for the same reason as step 6: it touches `HoldingsService`, which is on
   the acceptance path.

### 🔴 This hypothesis fails TWO of its own pre-registered gates

* **`max_drawdown_max = 0.20`** — momentum is −53.1%, the 50/50 allocation −41.5%. Both fail.
* **`oos_sharpe_min = 0.8`** — momentum's true Sharpe is **0.58** and 50/50's is **0.53**
  (§11q). Both fail. This was invisible until the fourth audit because every reported Sharpe
  since §11g omitted the risk-free deduction on a long-only book. The argument for proceeding is that the gate was written for a
market-neutral book that §11g proved unavailable at ₹2L (§1) — but **rewriting a gate after
seeing the result is precisely what §8 exists to prevent.** The original gate is recorded as
FAILED. Any replacement must be chosen deliberately as a risk-tolerance decision and written
down as such, not fitted to the number that came out.

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

### 🔴 Target vs measured (added 2026-08-23)

The targets above were set before any measurement. Here is what the surviving candidate
actually does, and where the plan was wrong:

| Target | Set at | **Measured (Pure Momentum)** | Verdict |
|---|---|---|---|
| Net annual return | 10–18% unlevered | **22.4%** (18.3% after tax) | **exceeded** |
| Volatility | < 12%, ideally 7–9% | **27.3%** | **missed by 2–3×** |
| Sharpe | 0.7–1.0 | **0.82** | met |
| Max drawdown | < 20% | **−53.1%** | **missed by 2.7×** |
| Construction | market-neutral or low-beta | **beta 1.07, long-only** | **not achieved** |

**Three of five targets were missed, and the two that were hit were hit the wrong way** —
by taking more risk for more return, which is exactly the trade §1 said not to make.

**The "optimise for low volatility" principle turned out to be unactionable here, and the
reason is structural, not a failure of effort.** Its logic — that a 10%/6%-vol book can be
levered into a 16%/20%-vol one but not vice versa — depends on leverage being available.
§11g established it is not: at ₹2L the short leg does not exist (cash shorts are
intraday-only, Zerodha rejects CNC shorts, SLB needs 500-share minimum orders, stock futures
need ₹2–3L margin per lot). Every available return stream is long-only Indian equity with
beta ~0.7–1.1, and §11g's diversification sprint found none that decorrelates during crashes.

**So the low-volatility route was tested and it is a dead end at this capital.** The low-vol
factor itself was measured (§11k): Sharpe 0.94 against NIFTY's 0.68, and still **−0.2pp worse
than the index after tax**, because 384% turnover plus 20% STCG consumes the entire 1.4pp
pre-tax edge. It is a real factor and not an investable one at retail scale.

The honest restatement of the target is therefore: **there is no low-volatility path to 20%+
available at ₹2L without leverage.** The choice is a high-volatility path or the index.

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

### Repo layout — ✅ built, ⬜ planned

Marked explicitly, because an aspirational tree that looks like an inventory is worse than
no tree at all. Line counts as of 2026-08-21.

```
algotrade/
  config/
    costs.yaml            ✅  versioned rate card -- cost params are data, not constants
    exclusions.yaml       ✅  symbols with unexplained moves, generated + reviewed
    trials.jsonl          ✅  append-only trial log (§8). Committed.
    universe.yaml         ⬜  universe rule still hardcoded in core/universe.py
  data/                       gitignored
    raw/                  ✅  immutable: bhavcopy mirror, backfill, action JSON
    curated/              ✅  parquet panel + manifest (208 MB)

  algotrade/
    io/                       ---- SHELL: all I/O ----
      bhavcopy.py    243  ✅  both NSE eras -> one canonical schema
      archive.py     117  ✅  multi-directory load, defects recorded not logged
      nse.py         269  ✅  bhavcopy backfill + action fetch (requests only)
      actions.py     170  ✅  quarterly action cache + ISIN map
      store.py       226  ✅  parquet panel, manifest, fingerprinted adjusted cache
      state.py            ⬜  live portfolio ledger (Phase 0c)

    core/                     ---- PURE: no I/O, no clock, no randomness ----
      integrity.py   320  ✅  PREV_CLOSE: actions, renames, missing sessions, delistings
      actions.py     151  ✅  factors from subject text, never faceVal
      reconcile.py   236  ✅  feed <-> observed moves, bridged by rename chains
      adjust.py      256  ✅  on-read back-adjustment, asymmetric field handling
      universe.py    161  ✅  point-in-time top-300 by turnover, coverage filter
      costs.py       226  ✅  rate card + slippage model
      portfolio.py   229  ✅  vol targeting, turnover control, position caps
      features.py         ⬜  Phase 0b
      strategies/         ⬜  Phase 0b
      calendar.py         ⬜  not needed -- holidays derive from data (§10.8)

    backtest/
      engine.py      195  ✅  vectorised, fills at open(D+1)
      metrics.py     142  ✅  incl. concentration + NIFTYBEES benchmark
      lookahead.py   153  ✅  mechanical truncation detector (§6 test #1)
      registry.py    271  ✅  pre-registration, trial count, deflated Sharpe
      engine_event.py     ⬜  event-driven validation twin (§6 test #2)

    live/                 ⬜  Phase 0c: recommend.py, shadow.py, parity.py

  scripts/                ✅  backfill_sessions, fetch_actions, build_panel,
                              reconcile_all, calibrate_backtester
  tests/                  ✅  14 files, 262 tests
```

**Deliberately not built:** `calendar.py` turned out unnecessary — holidays are derivable
from the data itself (§10.8), which is more reliable than the library that returned False
for Independence Day (§10.9). `engine_event.py` is deferred until a vectorised result is
actually distrusted.

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
| **0a. Instrument** ✅ | **COMPLETE 2026-08-21.** Data layer, universe, cost model, backtest engine, look-ahead harness, pre-registration. See §11, §11a. | ✅ Reproduces a known published effect (§11a) | done |
| **0b. Research** ✅ | **CLOSED 2026-08-23.** 8 registered trials, 69 diagnostic configs. Momentum established (net SR 0.82, CAGR 22.4%); turnover solved; drawdown **characterised but not solved** — it is the price, not a bug. Allocation selected: **50/50 momentum/NIFTYBEES** (§11o). See §11g–§11p | §8 gates **FAILED on max drawdown** — carried forward on a recorded judgement, not a pass | ~1 month spent |
| **0c. Plumbing** ⬅ **NEXT** | Monolithic service + cron + Streamlit page + shadow portfolios. **Plus two non-code prerequisites: the written never-sell rule (§11p) and a deliberate drawdown tolerance replacing §8.1's failed gate.** A fourth audit against the deployment path | **Zero unexplained signal discrepancies** | 6–8 weeks |
| **0d. Live** | Real capital, escalating ₹25k → ₹50k → ₹1L → ₹2L, at **50/50 momentum/NIFTYBEES**. The ₹25k stage exists to measure real fills against the slippage model (§11o gives ~4× headroom, so this gates scaling rather than starting) | Live tracks backtest within tolerance; realised slippage inside the +100 bps/side budget | 3–6 months |
| **1. TBD** | Chosen from Phase 0 findings. **The "2nd uncorrelated strategy" plan is now doubtful:** §11g tested it and found no economically distinct stream available at ₹2L — everything on offer is long-only Indian equity at beta ~1, and correlations barely fall during crashes (0.77 → 0.76). Revisit only with leverage or a genuinely different asset class | — | — |
| **2. Don't plan it** | Determined by what 0 and 1 teach | — | — |

**Phase 0a validation trick:** calibrate the backtester against a *known* published
effect (short-term reversal, momentum on NSE) before trusting it on unknown ones. If you
can't reproduce an effect known to be there, the instrument is broken — and you learn
that before betting on it. Same principle as putting a thermometer in ice water.

**Why Phase 1 is probably not intraday:** Sharpe scales ~√N for uncorrelated strategies.
Three strategies at Sharpe 0.6 combine to ~1.0 — bigger, safer, cheaper than one
intraday strategy at Sharpe 0.8 with 10× the operational risk in the most
algo-saturated arena in India. Let Phase 0's *results* pick Phase 1.

> **Update 2026-08-23 — this argument was tested and it does not survive at ₹2L.** The √N
> logic needs the strategies to be *uncorrelated*. §11g looked for a stream that rises when
> momentum crashes and found none: low volatility, long-term reversal, NIFTY and cash are
> all long-only Indian equity sharing beta ~1, and the one blend that helped (25/75
> momentum/low-vol) turned out to be **de-risking at a fair price rather than
> diversification** — after-tax return per unit of drawdown is 0.36 for both it and momentum
> alone (§11l). Blending here buys a smaller position, not a better one.

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
| 5 | 0.80 | 0.57 |
| 10 | 0.96 | 0.68 |
| 100 | **1.36** | 0.96 |
| 1,000 | **1.66** | 1.18 |
| 10,000 | **1.92** | 1.36 |

**The gate is 0.8 OOS Sharpe. With 100 configs tested on 5 years, noise alone delivers
1.36 — it clears the gate comfortably.** Note the N=5 row: even *five* trials on five years
of data produce 0.80 from noise, exactly at the gate.

*(This table is `registry.expected_max_noise_sharpe(..., method="conservative")` and is
pinned by tests. The N=5 row was originally mis-transcribed here as 0.68/0.48; the test
caught it. A more accurate Gumbel-based estimator is also implemented and gives materially
lower thresholds — ~1.13 for N=100 over 5 years — but the conservative form is the default,
since an estimator that errs toward demanding more evidence is the right one for guarding
against self-deception.)*

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

**Built 2026-09-02** as `paper_momentum_v1_001` / `shadow_momentum_v1_001` in
`algotrade_paper` — the same tables keyed by `portfolio_id`, as above. Two
corrections to the plan, both learned from building it:

- The shadow was **forked**, not created. Starting it from cash would have made
  it pay entry costs on thirty names the paper book already held, and that
  arithmetic about start dates would have swamped the signal being measured.
- `live` vs `shadow` is **discretion *and* slippage**, not discretion alone.
  The raw difference must never be reported as the cost of judgement. **Split
  on the dashboard since 2026-09-05**: the shadow fills at the reference price,
  so its own slippage is zero by construction and the whole component belongs
  to the traded book. An attribution rather than an identity — it ignores
  compounding and uses one denominator — so it is labelled good to a basis
  point and no further.

`benchmark` is a column (`benchmark_close`) on every snapshot rather than a
portfolio; `variant_a` remains unbuilt.

**Adding another portfolio is now one command; adding another *strategy* is
not.** Signals are keyed `(strategy_version, session)` and portfolios carry
their own params hash, so the data model supports several — but
`settings.strategy_config` is a single path loaded by six jobs, and
`StrategySpec` is momentum-shaped (`lookback`/`skip`, ranked descending). A
second strategy is ~a day of work in five places, and until then the
frozen-hash gate refuses the attempt rather than trading the wrong signal.
[`docs/operations/adding-a-portfolio.md`](docs/operations/adding-a-portfolio.md)
has the list, and the one place that would fail *quietly*.

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

### 10.9 `nsefin` is not trustworthy on the critical path (2026-08-21)

Attempting to use `nsefin` for backfill and holidays surfaced two defects that
change the dependency decision.

#### 🔴 The version pin does not pin the behaviour

```
pip show nsefin  ->  Version: 0.1.1
nsefin.__version__  ->  1.0.0
```

The same declared version ships **different code with a different API surface**
(`NSEClient.get_corporate_actions` in one environment, module-level
`get_corporate_action` in another). Package metadata and code disagree, so
`nsefin==0.1.1` in `requirements.txt` guarantees nothing.

#### 🔴 Its holiday calendar returns confidently wrong answers

```python
nsefin.is_nse_trading_holiday("15-08-2022")   # Independence Day
>>> False
```

The upstream endpoints 404, the exception is swallowed, a warning is printed to
stdout, and a wrong boolean is returned. Had we trusted it we would have judged
15-Aug-2022 a trading session and accepted the stale duplicate file as genuine —
compounding the corruption it was meant to prevent.

**This is the fifth data source in this project to fail silently rather than
loudly** (after eod2's deletions, `faceVal`'s wrong era, the feed's today-symbols,
and the mirror's stale duplicates). Treat silent-failure as the default
assumption for every external source here.

#### Consequences

- **`io/nse.py` depends only on `requests`.** Own the critical fetch path.
- **Holiday determination comes from data, not a library.** Our chain-break rate
  (~97% vs 0%) already beats it, and NSE returning **404** for a candidate gap
  settles authoritatively that the date was never a session.
- **Any remaining `nsefin` use must sit behind a thin adapter** so an API change
  is a one-file fix. The corporate-action validation in §10.6 was done against
  the 0.1.1 API and **needs re-verifying against 1.0.0** — see open items.

#### 🟡 NSE serves XLSX bodies from `.csv` URLs

`https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_08082022.csv`
returns a **ZIP containing an XLSX workbook**, not CSV. This is what made
`nsefin`'s fetch raise `UnicodeDecodeError`. Content is sniffed, and three shapes
are handled: plain CSV, zipped CSV, and XLSX-in-ZIP.

Knock-on effect: Excel stores all numbers as floats, so volume arrives as
`"1000.0"`. Integer columns are therefore cast via `Float64` — a direct `Int64`
cast silently nulls them.

Downloads are normalised to CSV on disk so `bhavcopy.read_file` stays the single
parsing path, and the written file doubles as a permanent cache.

#### Backfill result — self-verifying

| | Before | After |
|---|---|---|
| Sessions | 39 | **41** |
| Chain breaks | 3,542 | **0** |
| Unexplained | 0 | **0** |

2022-07-12 and 2022-08-08 fetched and repaired; 2022-08-09 (Muharram) returned
404, correctly classified as a non-session. **The chain reaching exactly zero is
stronger evidence the backfilled data is right than any assertion about the
bytes could be** — wrong data would not heal the breaks.

### 10.10 Rename detection and reconciliation, validated (2026-08-21)

#### Rename detector over the full 16 years

| | |
|---|---|
| Renames detected | **434** |
| Delistings (unexplained disappearances) | **982** |
| Symbols that stopped trading | 1,416 of 4,046 |
| Ambiguous pairs withheld | 0 |

Rate is 10–27/yr through 2021, then rises sharply: **36 (2022), 62 (2023), 39 (2024),
43 (2025)** — a real acceleration in Indian corporate rebranding.

**Spot-checked against known events — 5/5 real renames found, 2/2 non-renames correctly
excluded.** Two of those corrected *my own* mistaken expectations:

| Expected | Actual | Verdict |
|---|---|---|
| `MINDAIND → UNOMINDA` | ✅ 2022-08-04 @ ₹529.05 | correct |
| `CADILAHC → ZYDUSLIFE` | ✅ 2022-03-04 @ ₹345.95 | correct |
| `GMRINFRA → GMRAIRPORT` | ✅ 2024-12-10 @ ₹85.13 | correct |
| `MOTHERSUMI → MSUMI` | ❗ found `MOTHERSUMI → MOTHERSON` | **detector right** — `MSUMI` first appears 2022-04-11 as a *new listing* (the demerged wiring business), two months before the parent's rename |
| `SHRIRAMCIT → SHRIRAMFIN` | ❗ classified as a **delisting** | **detector right** — SHRIRAMCIT was dissolved in a merger; the actual rename was `SRTRANSFIN → SHRIRAMFIN` (2022-12-19), which *was* found |

#### Known limitation: renames across a trading gap are not detected

`find_renames` requires the new symbol to begin on the **very next session**. Observed
counter-example:

```
SONAMCLOCK   2022-04-07 .. 2023-10-25
SONAMLTD     2024-01-29 .. 2026-08-20     <- 3-month gap
```

The detector correctly declines this, but the action feed reports it under `SONAMLTD`, so
its 2022 bonus cannot be bridged and lands in `move_no_action` for manual review.

**Proposed fix (not yet built): use ISIN from the action feed itself.** Every feed row
carries an ISIN, so accumulating the full 2010–2026 action history yields a symbol→ISIN
map for every stock that ever had a corporate action — which is most of them. Combined
with cm-era ISINs (2010–Jun 2021), that should bridge most gap-renames.

#### Reconciliation results — Jul–Aug 2022 window

| Outcome | Count | Explanation |
|---|---|---|
| **matched** | **12** | Deviations 0.005–0.089, all within tolerance |
| factor_mismatch | 1 | `HNDFDS` implied 4.167 vs factor 5.0 (16.7%) — a genuine ~17% ex-date move, not an error |
| action_no_move | 2 | `XPROINDIA` (window-edge artefact, below) and `SONAMLTD` (gap-rename) |
| move_no_action | 5 | 3 ETFs + 1 demerger + 1 gap-rename |

**`MINDAIND → UNOMINDA` matched with 0.024 deviation — Trap 2 defeated in practice.** The
feed said UNOMINDA, the panel said MINDAIND, and the rename chain joined them.

Every residual is accounted for:
- **3 ETFs** (`MOLOWVOL`, `MOMOMENTUM`, `ICICIMOM30`) — their unit splits aren't in the
  `index=equities` feed. Benign, since §10.4 excludes ETFs anyway.
- **1 demerger** (`PEL`) — no ratio is derivable from the subject, so it is *deliberately*
  left unmatched rather than given a fabricated factor. Needs manual handling.
- **1 gap-rename** (`SONAMCLOCK`/`SONAMLTD`) — the limitation above.

#### ⚠️ Two usage traps found by running it

**1. Window-edge false negatives.** A symbol's first row is skipped in `find_actions` (the
listing-day rule), so an action on the *first session of a loaded window* can never be
verified — that is why `XPROINDIA`'s 01-Jul-2022 bonus shows as `action_no_move`.
**Load padding sessions before any reconciliation window.**

**2. Rename chains must come from the FULL panel**, not the analysis window — a rename
occurring after the window is invisible within it. `reconcile` takes renames as an
argument precisely so the caller can supply full-history chains.

#### Tolerance calibration

At 15%, `HNDFDS` (16.7%) is a false alarm: roughly **1 per 18 actions, ~5.5%**. Acceptable
for a review queue rather than a hard halt. Raising the tolerance to 25% would silence it
but would still catch the 2× errors that matter (§10.6 Trap 1). Left at 15% and treated as
review-not-error.

### 10.11 Adjustment layer built and verified (2026-08-21)

Adjustment happens **on read** and never mutates the raw panel (§5). A bad factor is then
a bug to re-derive rather than permanent corruption — which is precisely how eod2 destroys
history (§10.3).

#### Verified on TATASTEEL's 1:10 split

| date | raw close | divisor | adjusted | prev_close |
|---|---|---|---|---|
| 2022-07-26 | 949.50 | 10.0 | **94.95** | 96.07 |
| 2022-07-27 | 959.40 | 10.0 | **95.94** | 94.95 |
| 2022-07-28 *(ex)* | 100.35 | 1.0 | **100.35** | 95.94 |
| 2022-07-29 | 107.60 | 1.0 | **107.60** | 100.35 |

Across the whole Jun–Sep 2022 window, **all 32 adjusted events fall to normal-sized
moves** — extremes −9.7% to +16.1%, none above 25%.

#### `prev_close` is recomputed, not scaled

NSE leaves `prev_close` unadjusted on the ex-date — that *is* the discontinuity the
integrity checks depend on — so scaling it by the row's own divisor would leave it wrong by
exactly the factor. After adjustment the only self-consistent definition is the previous
adjusted close.

**Consequence: `find_actions` is a RAW-panel diagnostic only.** Running it on an adjusted
panel is a category error — its input no longer exists, so it reports history gaps rather
than corporate actions. Verified: 31 "discontinuities" on the adjusted window decompose
exactly into **16** from recomputed `prev_close` spanning trading gaps + **15** unmatched
actions. Now stated in the docstring.

#### Conservative by default

| Reconciliation bucket | Applied? | Why |
|---|---|---|
| `matched` | ✅ yes | corroborated by an observed price move |
| `factor_mismatch` | ❌ opt-in only | feed and prices disagree, so one is wrong; applying either silently is worse than leaving the gap visible |
| `action_no_move` | ❌ never | adjusting on a wrong ex-date *introduces* a discontinuity |
| `move_no_action` | ❌ n/a | no factor exists; `unadjusted_symbols()` excludes these names from the universe pending review |

---

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

## 11. Full-History Results (2026-08-21)

> ### Index to the findings (§11 – §11n)
>
> Written in order as the work happened, so **later sections correct earlier ones.** Where
> they conflict, the later one wins.
>
> | § | Subject | Still current? |
> |---|---|---|
> | 11 | Data assets, reconciliation over 16 years | yes |
> | 11a | Phase 0a exit gate — instrument validated | yes |
> | 11b | Data state detail | yes |
> | 11c | Design decisions in the final modules | yes |
> | 11d | Near-miss: the benchmark itself was corrupted | yes |
> | 11e | External review invalidated a core premise | yes |
> | 11f | First audit — ~28 defects | yes |
> | 11g | Momentum sprints: turnover solved, drawdown not | **net numbers superseded by 11j** |
> | 11h | Second audit — 3 results invalidated | **net numbers superseded by 11j** |
> | 11i | Robustness: mid-cap effect, no bear market in sample | tables re-measured post-11j |
> | 11j | **Third audit — "monthly" was trading daily** | yes; invalidates 11g/11h net figures |
> | 11k | Four finalists re-measured; after-tax ranking reverses | yes |
> | 11l | Drawdown profile — ulcer, capture, episode shape | yes |
> | 11m | Drawdown duration; **holdout inspected** | yes |
> | 11n | Investor experience: rolling windows, streaks, SIP | yes; **its 100%-momentum recommendation is superseded by 11o** |
> | 11o | **Deployment readiness — the decision (50/50)** | yes, current conclusion |
> | 11p | The cost of quitting — the never-sell rule | rule yes; **costs corrected by 11q** |
> | 11q | **Fourth audit — the Sharpe was not a Sharpe** | yes, current; corrects 11k–11p |

All four data tasks completed. Two bugs surfaced that a 4-month slice had hidden.

### Assets now on disk

| Asset | Coverage | Size |
|---|---|---|
| Bhavcopy prices | 3,993 sessions, 2010-06-10 → 2026-08-20 | 1.5 GB raw |
| **Curated Parquet panel** | 6,384,242 rows, 4,046 symbols | **208 MB** |
| **Corporate actions** | **34,659 actions, 66 quarters, all with ISIN** | 5.8 MB |
| Load time | **20.5s (CSV) → 1.1s (Parquet)** | **18× faster** |

### Reconciliation over 16 years

| | |
|---|---|
| Price discontinuities detected | 2,583 |
| Actions implying a price factor | 1,124 |
| **matched** | **858** |
| factor_mismatch | 54 |
| action_no_move | 179 |
| move_no_action | 1,671 |
| **Match rate among paired events** | **94.1%** |

Rename chains: **434 from price continuity + 12 from ISIN = 446 merged.**

### 🐛 Bug 1: the split regex silently missed 145 splits

It required `"Per Share"` between the two values. These real forms all failed:

```
Face Value Split From Rs 10 To Re 1              <- no "Per Share"
Fv Splt Frm Rs 10 To Re 1                        <- abbreviated
Face Value Split From Rs.10/- To Re.1/-          <- period, no space
Bonus - 1:1 And Face Value Split From Rs. 10 To Rs. 2   <- dash defeats bonus regex too
```

**Fixing it: price factors 984 → 1,124, match rate 91.6% → 94.1%, unparsed splits 145 → 9.**

Only findable by running over 16 years — the 4-month window happened to contain
only canonical forms.

### 🐛 Bug 2: symbol-level exclusion was itself a selection bias

Excluding a symbol's whole history for one unexplained day removed **243 of 1,430 universe
names (17%)** — reintroducing precisely the bias this project exists to avoid. Replaced with
`unadjusted_windows()`, dropping `(symbol, date)` rows instead:

| | Symbol-based | **Window-based** |
|---|---|---|
| Universe symbols affected | 243 / 1,430 (17%) | **46** |
| Universe rows dropped | — | **130 / 3,446,420 (0.004%)** |

The threshold also moved from 1.25× to **1.75×**, because **1,093 of 1,800 unexplained moves
sit in the 1.25–1.45 band** — circuit limits reach 20% and illiquid names move further, so
treating those as data errors would discard real returns. Above ~1.75× the moves cluster at
bonus and split ratios.

### ⚠️ Task D underdelivered — and the reason matters

The ISIN bridge added only **12 chains**, not the dozens hoped for. **The action feed reports
only *today's* symbol**, so a historical name like `SONAMCLOCK` never appears in it at all —
there is nothing to pair its ISIN against. The 12 found are ISINs mapping to two current
symbols (share classes, DVRs), not renames.

`SONAMCLOCK → SONAMLTD` remains unbridged.

**The real historical symbol→ISIN source is the `cm*bhav` mirror (2010 → Jun 2021)**, which
is cloned but unused for this. That would bridge gap-renames up to mid-2021; after that no
ISIN source exists and the limitation stands. Recorded as open.

### Rights issues: identified, deliberately unadjusted

**320 rights issues** found, e.g. `"Rights 1:10 @ Prem Rs 197/- Per Share"`. The adjustment
is price-dependent:

```
factor = ((held x P_cum) + (new x S)) / ((held + new) x P_cum)
```

`S` is in the subject but `P_cum` is not, so no factor is derivable from text alone.
`rights_ratio()` extracts the ratio; `price_factor()` deliberately returns None. Fabricating
a pure-ratio factor for 320 events would corrupt prices, so they surface as unexplained
instead — which is the honest outcome.

### Reproducibility: manifests, and why the adjusted panel is fingerprinted

`results = f(code, data, config)`. Git covers code and config. The manifest covers data —
necessary because **the mirror updates daily**, so an identical-code rerun after a backfill
would otherwise produce different numbers silently.

The adjusted panel is cached but **keyed by a hash of the factor set**, so it can never
become a source of truth. Change one factor and the cache misses rather than serving a
differently-adjusted panel. That is the eod2 failure mode designed out.

---

## 11a. Phase 0a Exit Gate — PASSED (2026-08-21)

Cost model and backtest engine built. The gate is "reproduces a known published
effect" (§7), tested on short-term cross-sectional reversal — chosen because its sign and
rough magnitude are documented, and confirmed on NSE.

**Development set only (2010-08 → 2021-12). The 2022–2026 holdout was not touched** — it
has 3–5 looks for the entire project (§8.3) and a calibration run is not one of them.

Setup: 5-session lookback, long worst 15 / short best 15, equal weight, market-neutral,
858 corporate-action factors applied, 194 monthly rebalances over 1,430 names.

| Metric | Value |
|---|---|
| **Gross CAGR** | **+8.5%** |
| **Net CAGR** | **−20.8%** |
| **Cost drag (annualised)** | **29.4%** |
| Volatility | 15.2% |
| Avg turnover per session | 23% (≈57 round trips/yr) |
| Beats NIFTYBEES | No (−32.8% excess) |
| Survives dropping best 5 days | No |

### Why this is a pass, not a failure

**The effect reproduces with the right sign and a plausible magnitude gross (+8.5%).** The
instrument works. And the cost model has teeth: it turns a real edge into a −21% disaster,
which is precisely what §4 predicted for this strategy — *"Short-term reversal: very high
statistically, **poor — turnover kills it**"*. The backtester independently confirmed the
plan's own prediction.

It also validates the §3 correction. Under the original wrong assumption (8–15 bps for
equity cash) the drag would have looked like ~10% and this would have appeared *marginal*
rather than catastrophic. **Getting the delivery cost right is what makes the difference
between rejecting this strategy and wasting months tuning it.**

### ❌ Correction: slowing the rebalance does NOT rescue this

An earlier claim here — that weekly rebalancing would cut drag ~5× and take net to roughly
**+2.5%** — was **wrong, and measured to be wrong**:

| Cadence | Turnover/session | Cost drag | **Gross CAGR** | Net CAGR |
|---|---|---|---|---|
| daily | 20.6% | 29.5% | **+8.6%** | −21.0% |
| weekly | 13.0% | 18.4% | **−5.2%** | −23.6% |
| monthly | 7.5% | 13.8% | **−7.8%** | −21.6% |

Two things were wrong. Drag fell only ~1.6× weekly (not 5×) — holding stale weights still
forces trades when the target *set* turns over at each rebalance. And far more importantly:

> **Gross return collapsed from +8.6% to −5.2%. Turnover reduction is not free.**

A 5-session reversal signal decays within days. Holding it for a week means holding long
after the reversion has already happened, so the alpha does not merely shrink — it inverts.

### The real lesson, and it reshapes Phase 0b

**Turnover reduction only helps when the signal's alpha persists over the longer holding
period.** Reversal's does not, so it is *structurally* dead at retail cost — not "dead
unless slowed down". The high turnover it needs is exactly what it cannot afford.

Which reframes why §4's momentum, PEAD, low-volatility and quality candidates are the
cost-friendly ones. **Not because they happen to trade less, but because their alpha decays
slowly enough to survive being traded less.** That is now the primary screening question for
Phase 0b:

> Before testing a signal, ask how fast its alpha decays. If it needs daily rebalancing to
> work, it is dead on arrival at ~30 bps and does not deserve a trial.

*(Max drawdown of −94% is an artefact of a losing strategy compounding down, not a
meaningful risk statistic.)*

---

## 11b. Data State Detail

**This section records what actually exists on disk, which is not the same as what has been
validated.**

| Asset | Coverage | Persisted | Notes |
|---|---|---|---|
| **Bhavcopy prices** | **3,993 sessions, 2010-06-10 → 2026-08-20** | ✅ `data/raw/nse-data-bank` + `data/raw/backfill` | Complete. 6.38M EQ rows, 4,046 symbols |
| Backfilled sessions | 86 | ✅ `data/raw/backfill` | Converged; a second pass fetched nothing |
| cm*bhav (ISIN source) | 2,706 files, 2010-06-10 → 2021-06-04 | ✅ cloned | **Not a panel source** — adds zero sessions, lacks VWAP/delivery |
| **Corporate actions** | ⚠️ **Jun–Sep 2022 only (~4 months)** | ❌ **not saved** | Fetched into memory during testing and discarded |
| Adjusted panel | ⚠️ same 4-month window, as a test | ❌ not built | Never run over full history |
| Curated Parquet panel | ❌ none | ❌ | Still reading CSVs on every load (~21s for 16 years) |

**So: every module is validated, but only against a 4-month slice.** The three gaps, in
priority order:

1. **Actions are not cached.** `io/nse.py` writes bhavcopy to disk so a session is never
   re-fetched, but `fetch_corporate_actions` has no equivalent. Full history needs ~65
   windowed API calls (~3 min) and must be persisted — NSE is not a dependency to hit
   repeatedly during research.
2. **Full-history reconciliation has never run.** The real match rate is unknown, and so is
   the definitive `unadjusted_symbols` exclusion list — which the universe needs *before*
   any signal is computed.
3. **No Parquet layer.** Re-parsing 3,993 CSVs per run is tolerable now and will not be
   once research iterates.

### Code state

| Module | Purpose |
|---|---|
| `io/bhavcopy` | Both NSE eras → one canonical schema |
| `io/archive` | Multi-directory load, defects recorded not logged |
| `io/nse` | Bhavcopy backfill + corporate-action fetch (`requests` only) |
| `core/integrity` | `PREV_CLOSE` invariant: actions, renames, missing sessions, delistings |
| `core/actions` | Factors parsed from subject text, never `faceVal` |
| `core/reconcile` | Feed ↔ observed moves, bridged by rename chains |
| `core/adjust` | On-read back-adjustment with asymmetric field handling |
| `core/universe` | Point-in-time top-300 by turnover, with coverage filter |

**103 tests. 8 commits. Not yet built: cost model, backtest engine, features, strategies.**

---

## 11c. Design Decisions in the Final Modules (2026-08-21)

Three modules completed Phase 0a. Each carries a decision worth stating, because each was
made in response to a specific failure rather than convention.

### `core/portfolio.py` — vol targeting excludes the current period

Scaling today's position by a volatility computed *including* today's return is look-ahead,
and a subtle one: the equity curve still looks plausible. The trailing window therefore
shifts by one, and a test asserts that a 30% return spike does **not** change the leverage
applied on that same day.

**`cap_weights` had a real bug, caught by its own test.** The first version clipped weights
to the cap and then renormalised gross exposure back up — which scaled the capped names
straight back *above* the cap. It now water-fills: excess goes only to names with headroom,
repeatedly. When the cap is too tight to absorb the original gross, the book reports the
**lower gross** rather than silently breaching. Honest is better than tidy.

### `backtest/lookahead.py` — a detector that is proven to fire

The §6 test #1 harness. Computes a feature on the full panel and on a truncated one, then
requires overlapping rows to be byte-identical.

A detector that never fires is worthless, so it is tested against four realistic bugs, all
of which it catches and **none of which is visible by reading the code carefully**:

| Bug | Why it hides |
|---|---|
| Forward shift (`shift(-1)`) | One character wrong |
| Centred rolling window | `center=True` looks innocuous |
| Full-sample z-score | Feature looks well-behaved; bias is diffuse |
| Full-history rank | Reads naturally as "rank within symbol" |

It also confirms `universe.liquidity` and the calibration signal are clean — worth knowing,
since look-ahead in the *universe* would taint every backtest through selection rather than
signal.

### `backtest/registry.py` — the holdout budget is enforced, not documented

Registration writes hypothesis, params and gates to `config/trials.jsonl` **before** a run,
and results append as separate records so registration order stays provable. Dev trials are
unlimited; holdout looks are capped at five for the whole project and a sixth raises
`HoldoutExhausted`.

Refusing is the point. A sixth look is not a fresh unbiased estimate — the §8.2 deflation
table starts applying to the holdout too.

**Gates reject an implausibly HIGH Sharpe as well as a low one.** Above ~2.5 in sample at
this frequency and cost structure means look-ahead, so a Sharpe of 4 fails.

**This module caught an arithmetic error in §8.2's own table.** The N=5 row read 0.68/0.48
where `sqrt(2·ln5/T)` gives **0.80/0.57**. The correction sharpens the argument rather than
softening it: even *five* trials on five years of data produce 0.80 from noise — exactly at
the gate. Pinning the doc's table to the implementation in a test is what surfaced it.

A Gumbel-based estimator is also implemented and is materially lower (~1.13 vs ~1.36 for
N=100 over 5 years). It is **not** the default: when the purpose is guarding against
self-deception, an estimator that errs toward demanding more evidence is the correct one.

---

## 11d. Near-Miss: the benchmark itself was corrupted (2026-08-21)

While checking whether the §1 benchmark could be measured rather than assumed, `NIFTYBEES`
computed to **−10.1% CAGR with a −93.5% drawdown** over 2011–2021 — during a period when
NIFTY rose substantially.

Cause: **`NIFTYBEES` had a 1:10 unit split on 2019-12-19** (implied ratio 0.1007), and
**ETF corporate actions are not in NSE's `index=equities` feed** — exactly the gap §10.8
recorded when `MOLOWVOL`, `MOMOMENTUM` and `ICICIMOM30` surfaced as unexplained moves. The
ETF sits unadjusted in the panel.

**Why this was dangerous rather than merely wrong.** Had the benchmark been wired to
`NIFTYBEES` without checking, `momentum_v1`'s 11.9% CAGR would have appeared to beat a
−10% benchmark by 22 points. The `beats_benchmark` gate would have passed enthusiastically,
and the "discovery" would have been a corporate action.

It was caught only because the number was checked against prior knowledge that NIFTY rose
over the period. **A gate is only as good as the input it compares against**, and this one
is currently a hardcoded `0.12` assumption — safe, but not measured.

### Consequences

1. **Do not use an unadjusted ETF as a benchmark.** Either adjust it for its own split
   (detectable — the implied ratio gives the factor to ~1%), or use the index level.
2. **Prefer the NIFTY 50 index level.** An index is a computed level, not a tradeable
   security, so it is never split-adjusted and cannot carry this class of error. NSE
   publishes daily index closes.
3. **The tradeable benchmark is still the ETF**, so once a clean index series exists, the
   gap between the two measures the ETF's tracking difference — worth knowing, since that
   is the return actually available by clicking buy.
4. **Ingesting index data also unlocks** beta measurement and neutralisation (needed: a
   −38% drawdown on a supposedly market-neutral book suggests real residual factor
   exposure) and the regime filters §4 lists as a candidate.

---

## 11e. 🔴 EXTERNAL REVIEW: a core premise is invalidated (2026-08-21)

An adversarial market-structure review, verified against live sources, found that **the
market-neutral construction this project has assumed throughout is not executable at ₹2L.**

### 1. 🔴 FATAL — the short leg does not exist

§1 targets "market-neutral or low-beta". §11a's gate run and both Phase 0b hypotheses are
long-15 / short-15, and **charged the short leg at the equity-delivery rate card.** That is
fiction:

| Route | Why it fails at ₹2L |
|---|---|
| **Cash segment short** | **Intraday-only.** Naked shorting is prohibited; an uncovered short goes to auction at the higher of (highest price to auction day) or (close + 20%). Zerodha **rejects a CNC sell** without the shares in demat. There is no configuration that holds a cash short overnight |
| **SLB** | 500-share **minimum order** (a ₹400 stock = ₹2L minimum position — the entire account), **100–130% collateral**, **offline/dealer only** (no API), 176 eligible stocks, contracts expiring first Tuesday monthly |
| **Single-stock futures** | ₹7.5L–15L contract value post-Oct-2024, **₹2–3L margin per lot** |
| **Index futures hedge** | ₹15L minimum contract, ~₹2L margin |

**Consequence: every long/short number computed so far describes a portfolio that cannot
be held**, and the measured cost drag *understates* reality because the short leg's true
cost (SLB fee + 130% capital lock, or futures margin) was never modelled.

**This was my own miss, not a subtle one.** `strategies.long_only` was written on the
reasoning that "shorting Indian cash equity has real frictions" — but treated as a variant
worth testing rather than the only executable form. The frictions are not frictions; they
are a wall.

**Resolution — Phase 0b must be long-only.** The realistic options are: long-only
cross-sectional ranking with cash/beta management; long-only plus a bought index put or
bear spread (a different, premium-decaying risk profile); or accept that market-neutral is
a **₹15–20L+ strategy** and rewrite §1's construction target. `momentum_v2` and `lowvol_v2`
must be re-specified before running.

### 2. 🔴 20% STCG tax is entirely unmodelled — and it breaks the §1 benchmark

STCG on listed equity is **20% flat** (raised from 15% on 23 Jul 2024), no Chapter VI-A
deductions. Every gain on a 2–10 day hold is realised and taxed **annually**.

**The benchmark is tax-deferred and the strategy is not.** NIFTYBEES held through pays
12.5% LTCG eventually, with a ₹1.25L annual exemption — effective drag near zero over the
comparison window. The strategy pays 20% of every realised rupee, every year.

Restating §1's own test: **to beat a 12% buy-and-hold after tax the strategy needs roughly
15% net-of-cost.** §1's stated target band of "10–18% unlevered" is therefore *largely below
its own benchmark* once tax is applied symmetrically. This is a bigger lever than the 30bps
we modelled so carefully.

Also unpriced: at ~15–20 delivery trades/month an assessing officer may treat this as
**non-speculative business income** (slab rates to 30%, but STT and expenses become
deductible, ITR-3, books, possible audit). CBDT Circular 6/2016 only protects the
>12-month election. Either treatment is a 20–31% haircut.

**Action: tax becomes a first-class term in `costs.yaml`, and the benchmark comparison must
be after-tax on both sides.**

### 3. 🟠 §4's small-shareholder claim is half wrong

- **₹2L threshold and 15% buyback reservation: CORRECT**, intact through the SEBI Buy-back
  (Amendment) Regulations 2026. Entitlement edge is real and roughly **4×** (Wipro 2026:
  19.6% small-shareholder vs 5.1% general).
- **Open offers and delisting offers have NO small-shareholder quota.** SAST 2011 acceptance
  is proportionate across all tendering shareholders. §4 conflated three different
  mechanisms — **only the buyback tender route carries the advantage**, which materially
  shrinks the event universe.
- **SEBI re-introduced open-market buybacks effective 1 Aug 2026**, a route with no retail
  reservation and one SEBI itself said favours institutions and algo traders. Expect tender
  event supply to fall from its 2025-26 peak.
- **Buyback tax whipsawed twice**: deemed dividend at slab rates Oct 2024 → Mar 2026 (the
  arb was *deeply negative* for a 30%-bracket participant in that window), back to capital
  gains from 1 Apr 2026. The arb's viability is a function of tax year, not a structural
  feature.

### 4. 🟢 The cost card is verified line-perfect — with four additions

Every line of `config/costs.yaml` confirmed against Zerodha's live page, and the ₹20,000
round trip independently recomputed at **₹59.79 → 29.9 bps**. The §3 delivery-vs-intraday
correction was called "the single best piece of analysis in the document".

Additions needed:
1. **DP is per scrip per *day*, not per sell.** Multiple sells of one scrip in a session =
   one ₹15.34; a position exited across two sessions pays **twice**. Encode the day key.
2. **Demat AMC ₹300/yr + GST ≈ 18 bps/yr on ₹2L** — permanent fixed drag, belongs in the
   annualised hurdle.
3. **NSE IPFT** ₹0.01/crore — negligible, add for completeness.
4. **§3's "index futures 3–5 bps" is STALE.** Budget 2026 raised futures STT 0.02% → **0.05%**
   and options premium STT to **0.15%**, effective 1 Apr 2026. Index futures round trip is
   now ~**8–9 bps**. *Equity delivery and intraday were unchanged, so the main card is safe.*

### 5. 🟡 Regulatory: right conclusion, wrong friction identified

Manual placement = zero algo surface: **correct**. But the real obstacle for Phase 0c is not
the 10-orders-per-second threshold (never approachable at ~10 orders/day) — it is that
**any** broker-API use requires a **registered static IP**, and **from 1 Apr 2026 API orders
are rejected if they do not originate from it**. A residential dynamic IP silently breaks
the pipeline. Budget a static IP or fixed-IP VPS.

### 6. 🟡 Exit feasibility is not modelled

- **ASM/ESM Stage II moves a name to Trade-for-Trade with a 2% band under periodic call
  auction**, with about one session of notice. **You may be unable to exit at any modelled
  price.** This applies to the live mid-cap universe, not just the deferred microcap lever.
- **Circuit bands are asymmetric**: non-F&O names have fixed 2/5/10/20% bands and can lock;
  F&O names have dynamic bands that flex. Exit feasibility differs across the universe and
  should be two regimes.
- **The 4pm-signal → 9:15am-fill gap is not in the slippage model.** `costs.yaml` models
  half-spread plus square-root impact; neither captures the overnight gap nor post-call-
  auction spreads, and the gap is *directional against* a cross-sectional ranking signal.
  Phase 0c must measure signal-close → actual-fill slippage separately.

### 7. ⚠️ An internal contradiction we had not noticed

§3 derives "target ~6–10 positions at ₹2L" from the DP floor. §11a then ran **30 positions**
(long 15 / short 15) — ₹6,667 each, where DP alone costs **23.0 bps per rotation**, worse
than §3's stated worst case. Verified:

| Positions | Size | DP alone |
|---|---|---|
| 8 | ₹25,000 | 6.1 bps |
| 10 | ₹20,000 | 7.7 bps |
| **30** | **₹6,667** | **23.0 bps** |

The two sections contradict each other and the measured 29.4% drag was computed at the
position count §3 says not to use.

---

## 11f. Review Round and Repair (2026-08-21)

Three adversarial reviews — methodology, code correctness, market structure — found
**~28 confirmed defects**. Almost all were mine. This section records what they cost and
what they bought, because the pattern matters more than the individual bugs.

### The headline: every fix moved the result the same way

`momentum_v1`, re-run after each round of repair:

| | Before review | After data fixes | **After all fixes** |
|---|---|---|---|
| CAGR | 11.9% | 10.4% | **8.4%** |
| Sharpe | 0.389 | 0.25 | **0.13** |
| Benchmark | *assumed* 12% | *measured* 14.1% | *measured* 14.1% |
| Excess | −0.1% | −3.7% | **−5.4%** |
| Deflated Sharpe | +0.39 | −0.22 | **−0.46** |

**Not one fix improved the result.** The original +11.9% was the sum of an inert liquidity
filter, 81,723 compounded gap returns, a flattering benchmark assumption, and a
risk-free-rate error partly offsetting the others. That is what removing favourable errors
looks like, and it is the strongest available evidence that the reviews were worth their
cost.

### The four that mattered most

**1. Turnover was 10⁵× too large for 68% of the archive.** `TURNOVER_LACS` is already in
*rupees* in pre-2021 files and in *lakhs* afterwards. Multiplying unconditionally made the
₹5 crore liquidity floor — "below this, fills are fiction" — **inert for 11 of 16 years**,
and collapsed modelled market impact to half-spread. *And a test asserted exactly this
property and passed*, because its two fixtures were a 2024 file (correctly lakhs) and a `cm`
file (correctly rupees). The one broken combination was never covered.

**2. The engine shifted by row position, not session.** 42,727 periods spanned more than one
session; worst compounded **+8,455%** over a 100-session hold; in-universe, ASIANPAINT
booked **−89.9% over 4 sessions as a single day.** Holes created by `drop_windows` were read
straight across.

**3. A `--holdout` run crashed *after* burning a look.** Registration happens before
computation, and `vs_benchmark_series` omitted `beta` on an early-return path. **The first
holdout invocation would have consumed 1 of 5 lifetime looks and returned nothing.**

**4. The ETF exclusion §10.4 mandates was never wired up.** `LIQUIDBEES` — a money-market
fund, therefore always the lowest-volatility name in a liquidity-ranked universe — was
`lowvol_v1`'s **most-held long in every single dev session**, with 14.6% of that book in
ETFs and gold. That trial did not test its stated hypothesis; **its result is void, not
merely wrong.**

### The recurring pattern, stated plainly

| I documented | Then did |
|---|---|
| §10.8: two eras differ in schema | Multiplied turnover unconditionally across both |
| §11d: a corrupted benchmark nearly passed a gate | Wired in a 43%-coverage series eight commits later |
| §10.10: identity changes over time (symbol renames) | Filtered the index on its current name only |
| §6: "the highest-value test in the project" | Left it blind to closure-captured state |

**The discipline was in the document and not reliably in my hands.** Writing a section about
a failure mode is not protection against it. Three things did work: an adversarial reader
with no stake in the answer, constructing the failing case *before* the fix, and pinning
the document's own numbers to executable tests (which is how §8.2's arithmetic error
surfaced).

### Things the audits could NOT break

Worth recording so the list above reads as criticism rather than a verdict:
`adjust.cumulative_divisors` across every edge case tried; the point-in-time universe
property; the cost arithmetic (verified line-perfect against Zerodha's live card);
`vol_scalar`'s current-period exclusion; fill timing itself. The parts most load-bearing for
correctness held.

### Fixes worth knowing about beyond the bug

- **`exclusions.yaml` was write-only** — generated by one script, read by nothing. A review
  artefact masquerading as a control, and the most insidious shape of bug: the file's
  existence *was* the evidence it worked.
- **`rolling_3m_positive` silently always failed** — required by the gates, computed nowhere,
  defaulted to 0.0. It would have rejected every hypothesis in Phase 0b.
- **`beats_benchmark` was absolute, not risk-adjusted** — enforcing the opposite of §1's
  central argument, and penalising exactly the low-vol profile the plan targets.
- **Sharpe subtracted the risk-free rate from a dollar-neutral book** whose return is already
  a self-financing spread. Worth 0.46 Sharpe units, and it *flipped* momentum_v1's gate.
- **The look-ahead detector's own blind spots**: closure-captured state (the shape of
  `load_adjusted_panel`, where back-adjusted price *levels* embed future splits) and
  late-history peeks. Both now have tests proving the blind spot existed and is closed. A
  companion test shows why existing features escaped harm — **a ratio of two prices cancels
  the divisor**, so this was a latent trap rather than active contamination.

### Walk-forward: resolved by not building what the plan asked for

§5, §8.3 and §13 all claimed walk-forward; no such code existed, and the gate was named
`oos_sharpe_min` while checking the dev Sharpe.

The honest resolution: **in this project parameters come from reasoning, not fitting (§8.4),
so splitting the data creates no out-of-sampleness.** Every period is equally "not fitted".
`backtest/walkforward.py` therefore measures **stability across time** — implementing §8.1's
regime gate, specified from the start and never built — and says so in its own output.
Building a splitter and calling the result out-of-sample would have been the comfortable
choice and a false claim.

What folds still cannot fix, recorded rather than hidden: universe size 300, coverage 0.80,
`min_ratio` 1.75, slippage k=0.6, `n_side` 15, cadence — **all chosen with sight of the same
history.** Only the untouched holdout separates those from evaluation.

---

## 11g. Momentum: Two Implementation Sprints (2026-08-22)

Phase 0b proper. Not factor discovery — the factor is established, and these sprints ask
whether its *implementation* can be made investable at ₹2L. **30 configs, all diagnostic,
recorded as `exploration` in the trial log** so §8.2's denominator stays honest.

### 🔴 First, a correction: momentum was never dead

§11f reported momentum at Sharpe 0.13 and called it indistinguishable from noise. **That was
a reporting bug, not a result.** `metrics.compute` used the default 6.5% risk-free rate while
the gates used 0.0, so one run produced three different Sharpes — and subtracting a
risk-free rate from a **dollar-neutral book** is a category error, since its return is
already a self-financing spread.

**Corrected: gross Sharpe 0.97 at n_side=15, rising to 1.24 at n_side=50.** Over 11 years
that is a real factor. Everything below follows from that.

> ⚠️ **§11h supersedes the NET figures in this section.** The engine did not mark positions
> to market, so every net-of-cost number below is optimistic by roughly −1.8pp CAGR and
> −0.07 Sharpe. The frozen book is Sharpe **0.73**, not 0.80. Gross figures and all
> *relative* comparisons stand.

### Turnover: diagnosed and largely solved

Attribution of the 951%/yr on a *monthly* rebalance:

| Source | Share |
|---|---|
| exit / trim | 50.0% |
| **re-entry (same name returning)** | **29.2%** |
| entry (genuinely new name) | 11.1% |
| add to holding | 9.7% |

Only 11% is new opportunities. **29% is the same names round-tripping the portfolio
boundary** — noise, not conviction.

**Rank buffering fixes it.** Buy at rank ≤30, sell only past rank ≤60:

| | Net SR | Gross SR | Turnover |
|---|---|---|---|
| hard cut (30/30) | 0.74 | 0.95 | 995% |
| **buffer 30/60** | **0.80** | 0.95 | **633%** |

**Turnover −36%, net Sharpe +0.06, gross Sharpe untouched** — confirming the buffer costs no
factor exposure. It changes nothing about the signal, only how much evidence is required to
act on a change of mind.

**Cadence does NOT control turnover.** Monthly → 2-monthly → quarterly moved turnover only
695% → 714% → 648% while net Sharpe *fell* 0.78 → 0.74 → 0.70. A third of the rebalances,
each turning over three times as much. Buffering is the lever; cadence is not.

### 🔴 Second correction: the drawdown is NOT inherited market beta

§11f concluded the binding constraint was market beta — that a beta-1.08 long-only book
simply inherits NIFTY's −38.4% and 25% is therefore arithmetically unreachable.

**That was wrong**, and the first attribution metric that suggested it was also wrong
(measuring NIFTY start-to-end across a 708-session episode is meaningless when the market
falls 38% mid-window and recovers). Measuring NIFTY's **own drawdown within each episode**:

| Episode | Strategy DD | NIFTY DD in window | Verdict |
|---|---|---|---|
| 2018-01 → 2020-12 | −54.4% | −38.4% | market-driven, amplified |
| 2013-01 → 2014-05 | −35.8% | −14.6% | **factor-specific** |
| 2016-01 → 2016-10 | −27.5% | −10.5% | **factor-specific** |
| 2016-10 → 2017-02 | −25.6% | −8.4% | **factor-specific** |
| 2011-11 → 2012-03 | −22.1% | −7.6% | **factor-specific** |

**Four of the five worst drawdowns are momentum crashes** — the strategy losing 2–3× the
market's decline during style rotations, in windows where the index barely moved. The
drawdown is therefore *addressable in principle*, but not by anything that targets beta.

### What worked, what failed

| Lever | Effect on drawdown | Effect on Sharpe | Verdict |
|---|---|---|---|
| **Rank buffering** | none | +0.06 | ✅ keep — solves turnover |
| **Vol targeting (8%)** | **−54% → −23%** | 0.80 → 0.74 | ⚠️ works, costs absolute return |
| Market trend filter | −54% → −44% | ~~0.80 → 1.10~~ | ❌ **ARTEFACT — see §11h.** Honest version is 0.91 → 0.80: it *costs* 0.11 Sharpe |
| Stock > 200DMA | none (−53%) | 0.80 → 0.78 | ❌ and turnover nearly doubles |
| Inverse-vol weighting | **none (−55%)** | 0.80 → 0.78 | ❌ refutes "high-vol names cause DD" |
| ERC approximation | none (−55%) | 0.80 → 0.79 | ❌ |

**P4's failure is informative**: all three weighting schemes left drawdown unchanged at
−55%, which directly refutes the hypothesis that a few volatile names dominate. Momentum
crashes are a **whole-book** event, not a concentration problem.

### The best combination hits both targets and is still not worth trading

Frozen buffer + ERC weighting + NIFTY 200DMA filter + 10% vol target:

| Metric | Value | Target |
|---|---|---|
| Net Sharpe | **1.16** | ≥0.80 ✅ |
| Max drawdown | **−25.3%** | ≤25% ✅ |
| CAGR | 12.7% | — |
| **Excess vs NIFTY** | **−1.0%** | ❌ |
| Average exposure | 52% | — |

**It clears both stated criteria and underperforms the index.** 30 positions, monthly
rebalancing, 485% turnover, to earn slightly less than one click on NIFTYBEES — and with a
20% STCG bill the ETF defers (§11e). **A high Sharpe with no leverage available is an
unbankable asset**, which is §1's own argument arriving from the other direction: optimise
for low volatility *because it can be levered*, and if it cannot be levered the Sharpe does
not convert into anything.

### Conclusion: no production candidate

The best *strategy* is **buffer 30/60, monthly, equal-weight** — Sharpe 0.80, +11.4% excess
over NIFTY, turnover 633%. But at **−54% drawdown it is untradeable at ₹2L**, and every
de-risking route lands below the index.

**The dominant bottleneck is momentum-crash risk** — not turnover (solved), not costs
(2.9pp), not market beta (only the worst episode), not implementation constraints.

Two caveats on all of the above: **P2's regime filter is the only thing that lifted Sharpe
past 1.0 and it is under audit for look-ahead** — if it fails, the combined result goes with
it. And **nothing here has touched the holdout.**

The honest next question is not more implementation work. It is whether momentum-crash risk
is hedgeable at all at this capital — and if it is not, **₹10L+ becomes a precondition
rather than a preference**, consistent with the capital-constraint finding (net Sharpe 0.72
at ₹2L → 0.92 at ₹10L, then flat).

---

## 11h. Second Audit: three reported results invalidated (2026-08-22)

An independent re-audit verified **11 of 12 prior fixes as genuinely working**, and found
**six new defects — three of which invalidate results recorded in §11g.**

### 🔴 The regime-filter result was an artefact, and the honest version HURTS

§11g reported a NIFTY 200DMA filter lifting net Sharpe **0.70 → 1.07**. The filter was
implemented as `filter(date.is_in(risk_on_dates))`, which **deletes sessions rather than
going to cash**. `engine.run` builds its date list from the weights, so a date with no rows
is not a flat day — it is absent from the equity curve entirely. Two consequences:

- **222 dev sessions have no index file at all** (the archive starts 2012-02-21, the book
  starts 2011-07-11), so they were dropped for reasons unrelated to regime — and they sit
  inside the 2011-12 drawdown.
- `metrics.compute` then annualised 1,697 sessions as 6.8 years instead of 10.5 calendar
  years.

Like-for-like on the same window, with an honest cash-on-risk-off implementation:

| | Sharpe | Max DD | Turnover |
|---|---|---|---|
| no regime filter | **0.91** | −55.3% | 810% |
| + regime, cash on risk-off | **0.80** | −49.0% | 1,344% |

**Reported 0.70 → 1.07. Actual 0.91 → 0.80.** The filter costs 0.11 Sharpe and trebles
turnover. §11g flagged it as "under audit, do not build on it" — that caution was correct.

### 🔴 The engine never marked positions to market

`held[sym]` was the rupee exposure *at the moment of the trade* and was never grown by the
position's return. Next session `target = w × running` had moved with the P&L while
`previous` had not, so `delta` was non-zero for a book doing nothing at all. A single
buy-and-hold position generated a phantom trade **every session**.

It also missed the *real* rebalancing driven by cross-sectional dispersion, so the net error
ran **optimistic**:

| buffer 30/60 monthly | §11g reported | **Corrected** |
|---|---|---|
| CAGR | 20.5% | **18.7%** |
| Net Sharpe | 0.80 | **0.73** |
| Turnover | 633% | **753%** |

**Every net-of-cost number in §11g and the trial log is optimistic by roughly −1.8pp CAGR
and −0.07 Sharpe.** A buy-and-hold position now books exactly one trade.

### 🔴 A "fix" from §11f introduced look-ahead

§11f wired `exclusions.yaml` into the universe, describing it as "a review artefact
masquerading as a control". **That was wrong and made things worse.** The list drops a
symbol's *entire history* on the strength of one unexplained move, so a 2019 event removes
the name from the 2011 universe — and **50 of the 127 symbols are excluded on events dated
≥2022, putting holdout information into the dev universe.** 2,404 of 36,001 dev membership
rows (6.7%) affected.

The file's own header said *"for review only — dropping whole symbols would itself bias the
universe"*, and `adjust.unadjusted_windows` already masks these events point-in-time, which
is the correct treatment. **Reverted.**

### Also fixed

- **`beats_benchmark` compared raw return/vol, not excess.** With a negative CAGR the
  ordering inverts: a book matching the index at half its volatility scored −0.625 and
  FAILED, while one losing 12% at 60% vol scored −0.200 and PASSED. Replaced with the
  information ratio of the difference series, which is sign-safe.
- **§8.1's regime gate was mathematically unpassable.** `purge = lookback + skip = 273`
  requires ≥2,620 sessions; dev has 2,577 and holdout 1,141. `make_folds` always raised, the
  advisory was printed, `positive_fold_fraction` stayed 0.0, and `stable_across_folds` always
  failed. **The same silent-always-fail class as `rolling_3m_positive` — the third time.**
- **Holdout deflation faced a zero noise threshold.** Scoped to `dataset="holdout"`,
  `n_trials=1`, whose threshold is 0.0 by definition — zeroing the denominator at exactly
  the irreplaceable look, when the candidate got there only because N dev trials selected it.
- **`cap_weights` invented positions in zero-weight names.** `[0.5, −0.5, 0, 0]` came back
  `[0.2, −0.2, 0.2, 0.2]` — a dollar-neutral book turned 40% net long.
- **`vol_scalar` needed `shift(2)`, not `shift(1)`**, because equity rows are signal-dated
  while the return spans D+1→D+2. Its test used `keys=("date",)`, which cannot express label
  semantics, so it passed on the broken case.

### The audit's sharpest observation is not a bug

> **The look-ahead detector is never called outside `tests/`.** Every leak found this round —
> the exclusions list, the regime filter, `vol_scalar`'s labelling — lives *outside*
> `feature_fn` and is structurally invisible to it.

The mechanism is sound and catches everything feature-shaped. **The coverage is the problem**,
and §11c's claim that it "confirms the calibration signal is clean" is true only of the
signal, not of the pipeline around it.

### Verified working (11 of 12)

Turnover unit detection (all 4,081 files classified, ratio median 1.00 in every year);
`_fill_prices` session-grid alignment (984 symbols trading every session lose exactly zero
rows); sibling-action multiplication (0 duplicate groups in 34,659 feed rows, BAJFINANCE now
matches at deviation 0.005); `unadjusted_windows` session padding (0 of 1,075 dates off-grid);
**benchmark open-to-open alignment (a synthetic 100%-index tracker measures beta 1.0000;
joining on the signal date instead gives 0.0203)**; the NaN guard; `trial_id` assignment; the
look-ahead `rebuild` callback; and risk-free consistency across every call site.

### Standing result after correction

**Momentum long-only, buffer 30/60, monthly: Sharpe 0.73, max drawdown −55.3%.** Worse than
§11g reported, and still no production candidate. Rank buffering survives scrutiny as REAL
(turnover −35%, gross Sharpe unchanged); the regime filter does not.

---

## 11i. Robustness Diagnostics — and the sample has no bear market (2026-08-22)

Four dev-set questions run before spending any holdout look, two of which could have
changed *which* candidate is worth confirming. 15 configs, diagnostic.

### Universe robustness: survives, but it is a MID-CAP effect

| Universe | Gross SR | Net SR | CAGR | maxDD | Turnover |
|---|---|---|---|---|---|
| Liquid 100 | **0.51** | 0.43 | 11.6% | −48.2% | 602% |
| Liquid 200 | 0.79 | 0.69 | 17.5% | −50.7% | 633% |
| **Liquid 300** | **0.94** | **0.83** | **21.3%** | −53.2% | 576% |
| Liquid 500 | 0.89 | 0.78 | 18.9% | −48.7% | 483% |

*(Re-measured on the engine after §11j's fix. The first pass reported net 0.31 /
0.56 / 0.73 / 0.72 — every one of them understated by undisclosed daily
re-equalisation. Gross figures and every conclusion below are unchanged.)*

Book size scaled to a constant fraction of the universe, so selectivity is held fixed and
only breadth varies.

Monotonically stronger in less-liquid names, then **stable across 300→500** — so the factor
is not fragile under universe change, which is the reassuring half. The unreassuring half:
**in the largest 100 names it is half as strong**, so the edge lives exactly where costs and
market impact are worst and where the slippage model is least trustworthy. Economically
coherent (thinner coverage, slower information diffusion) and consistent with the literature.

### Concentration: momentum crashes are NOT sector crashes

Average pairwise correlation of the held book, per year: **0.13 – 0.23, every year,
including crash years** (2018: 0.18). A concentrated sector bet would show 0.5–0.7.

Book *volatility* does spike in crash years (2016: 33.6%, 2018: 32.4%) **without internal
correlation rising** — the signature of individual names blowing up rather than a sector
moving together. Hypothesis cleanly refuted; no sector labels were needed to refute it.

### Regime: momentum works in bull markets and fails in sideways markets

| Year | Momentum | NIFTY | Excess | Regime |
|---|---|---|---|---|
| 2012 | 44.6% | 8.5% | **+36.1%** | sideways |
| 2013 | −15.4% | 0.4% | **−15.9%** | sideways |
| 2014 | 84.1% | 30.6% | **+53.6%** | bull |
| 2015 | 10.3% | 1.7% | +8.5% | sideways |
| 2016 | −12.5% | 0.9% | **−13.4%** | sideways |
| 2017 | 102.4% | 27.8% | **+74.6%** | bull |
| 2018 | −26.5% | 3.7% | **−30.2%** | sideways |
| 2019 | 5.8% | 12.2% | −6.5% | bull |
| 2020 | 40.9% | 15.6% | +25.2% | bull |
| 2021 | 90.4% | 24.7% | **+65.7%** | bull |

**Bull years: 4 of 5 strongly positive. Sideways years: 2 of 5 positive.** The mechanism is
coherent — momentum needs trends to persist, and chop whipsaws it. This is the regime
dependence §11g's crash attribution implied but did not name.

### Holding period: decay is mild, monthly stands

Gross Sharpe 0.94 → 0.92 → 0.88 (monthly → 2-monthly → quarterly), net 0.83 → 0.82 → 0.79,
while turnover falls only 576% → 557% → 518%. Slower holding costs signal and saves almost
nothing. Monthly stands.

### 🔴 The finding that reframes everything: there is no bear market in the sample

NIFTY calendar-year returns across the **entire 14-year archive**:

```
2012  +7.0    2013  +1.9    2014 +29.7    2015  +1.9    2016  +0.3
2017 +29.2    2018  +3.9    2019 +12.2    2020 +14.1    2021 +22.8
2022  +8.4    2023 +16.8    2024  +8.4    2025 +10.2    2026  -6.7  <- the only bear year
```

**Exactly one bear year in fourteen, and it is in the holdout.** Everything else is bull or
sideways.

So the −55% drawdown this project has spent three sprints trying to fix is momentum's
behaviour **in benign conditions**. Momentum's worst episodes globally are bear-market
*recoveries* — US momentum lost roughly 80% in 2009 — and **that scenario is entirely
absent here.** Its actual tail risk is not merely unmeasured; it is unmeasurable from this
data.

**Consequence for the holdout.** 2022–2026 is four bull/sideways years plus one −6.7% year,
i.e. **the same regime that produced the dev result.** A pass would confirm the strategy in
conditions it is already known to suit and would say nothing about the risk that actually
matters. That is not a reason never to look — it is a reason not to treat a pass as
validation of tail behaviour.

---

## 11j. Third Audit: "monthly rebalance" was not monthly (2026-08-22)

Commissioned to verify the one number the second audit left untrusted — a cost drag that
jumped 2.9% → 7.2% after the mark-to-market fix. **The mark-to-market fix was correct. The
cost drag was correct. What they exposed was a defect nobody had looked for.**

### The finding

`buffered_long_only` and `portfolio.resample` emit `gross / len(held)` on **every session**,
not only on rebalances. Before the mark-to-market fix that was harmless: exposures were
carried forward unchanged, so the restated target always matched what was already held and
no trade resulted. **Once positions drift with their own returns, the restated equal weight
becomes an instruction to trade back to it — every single day.**

Measured on buffer 30/60 "monthly", dev (2,577 sessions):

| | |
|---|---|
| monthly rebalance dates | 126 |
| **sessions on which the book traded** | **2,393 (92.9%)** |
| trades on non-rebalance dates | 14,230 of 16,427 (**86.6%**) |
| traded value on non-rebalance dates | 38.0% |
| **cost incurred on non-rebalance dates** | **64.4%** |
| sessions on which the held SET changed | 150 |

The signal changed its mind 150 times. The engine traded 2,393 times. **The other 94% was
paying to undo price drift the strategy never asked to correct.**

### Why it was so expensive: flat DP, not bps

| | as-shipped | fixed |
|---|---|---|
| trades | 16,427 | 3,517 |
| **median trade** | **₹741** | **₹3,724** |
| **realised cost** | **60.8 bps** | **35.3 bps** |
| share of cost that is the flat ₹15.34 DP charge | ~57% | — |

`costs.breakeven_bps` makes the mechanism plain: ₹20,000 → 29.9 bps, ₹1,000 → 175.6 bps,
₹500 → **329.0 bps**. **The "~30 bps round trip" assumption in §3 holds only for trades
around ₹20k.** At ₹2L over 30 names a full position is ₹6,667, so drift trades were an order
of magnitude below the size at which the rate card behaves like bps.

The enabling defect was `min_trade_value=500.0`. Its docstring says it exists to stop dust
trades manufacturing turnover — but ₹500 is 7.5% of a position, far below daily drift, so
post-mark-to-market it stopped nothing.

### What it cost, and the corrected frozen book

| variant | CAGR | Sharpe | maxDD | turnover | cost drag |
|---|---|---|---|---|---|
| as-shipped (daily re-equalise) | 18.7% | 0.73 | −55.3% | 753% | 4.70% |
| **trade only on rebalances** | **21.8%** | **0.85** | −52.6% | 561% | **2.15%** |

**Gross Sharpe is unchanged (0.95 → 0.96).** Nothing about the signal changed; the fix
removes cost, and only cost. The frozen book is **Sharpe 0.85, CAGR 21.8%** — better than
the 0.80 §11g claimed on a broken engine, and materially better than the 0.73 §11h recorded.

**Every net-of-cost number in §11g through §11i was understated**, for the mirror-image
reason §11a–§11f's were overstated. §11i has been re-measured; its conclusions are unchanged
(the mid-cap gradient, the refuted sector hypothesis, the regime split and the holding-period
decay all survive).

### The fix

`engine.run` gains **`trade_dates`** — the sessions on which positions may change. A weight
row states a *target*; it is not an instruction to trade, and the two coincide only on a
rebalance. Between them the book is held and its weights are allowed to drift, because that
drift **is** the position taken. `engine.implied_trade_dates(weights)` infers the set for
callers that do not have the cadence to hand. All 13 call sites now pass it.

### Six further defects, all fixed

**1. `beats_benchmark` had silently stopped being risk-adjusted.** §11f replaced a
sign-unsafe CAGR/vol comparison with the information ratio of the difference series — but
`IR > 0` is *identically* `mean(strat) > mean(bench)`, because the standard deviation
cancels. It also compared arithmetic means where `excess` compares CAGRs, so the two
disagreed in sign under volatility drag: **10.6% CAGR at 6% vol scored FALSE against 9.5% at
19.7% vol** — precisely the low-volatility profile §1 targets. Now gates on the **Sharpe
difference**, which is both risk-adjusted and sign-safe through zero. IR is retained as a
reported diagnostic.

**2. 🔴 The look-ahead detector's `settle` window was a blindfold.** A k-session forward peek
only perturbs rows dated later than `cutoff − k` — which is exactly the band `settle`
deleted from the comparison. At the shipped default of 25, a literal `close.shift(-1)` — the
fill-at-close leak the engine calls *"the single most consequential choice here"* — reported
**CLEAN**, as did every peek up to 25 sessions, flipping to caught at 26. Even `settle=2`
hides a one-day leak. Fixed at the root: cutoffs now default to **calendar month ends**,
which removes the truncation artefact `settle` was built to absorb (`group_by_dynamic`
treating a truncated run's last session as a completed month), so the default is now
`settle=0` and the comparison is exact. Regression test asserts both the catch and the old
miss.

**3. The abort path burned an irreplaceable holdout look for a check that never touched the
holdout.** `run_hypothesis` registered the trial *before* the pre-flight, so a `--holdout`
run aborting on a leak had already spent 20% of the project's entire budget — on a check that
runs against a **2017–2019 dev slice**. Registration now happens *after* the pre-flight;
everything before it is deterministic from the spec and touches no result.

**4. `pipeline_clean` was computed, then never gated on and never persisted.** `Gates` had no
such field and `check_gates` ignored it, so the `except ValueError` branch set it False and
proceeded to a full **PASS** verdict — the same silent-always-fail class as
`rolling_3m_positive`, in reverse. It also never reached the trial log, because the record
filter tested `isinstance(v, float)` and **`isinstance(False, float)` is `False`**. Now a
real gate that fails when absent, and bools are persisted.

**5. `information_ratio` wrote invalid JSON.** The `or 0.0` guard catches `None`; `inf` and
`nan` are truthy and passed straight through into `trials.jsonl` as bare `Infinity`/`NaN`.
Now guarded on `std > 0` and `math.isfinite`.

**6. `vs_benchmark_series` annualised by row count, not calendar span.** An inner join
against a gappy benchmark keeps the series aligned — so beta and correlation stayed valid —
but dividing by the survivors annualised a 400-session window as if it were 195 sessions:
**benchmark CAGR 39.1% where the truth was 15.1%.** `run_hypothesis` guarded this with
`MIN_BENCH_COVERAGE`; `sprint_risk.py` called it unguarded and printed the result. Now uses
the calendar span.

**Also:** the deflation denominator double-counted `sprint_diversify`'s 8 configs (the script
was interrupted and re-run, appending its exploration record twice). Re-running identical
code is not a fresh selection opportunity. Corrected by **appending** a −8 record rather than
editing the append-only log; `trial_count` now honours negative corrections. **69 configs,
not 77.**

### Verified correct — three things the audit tried to break and could not

* **The mark-to-market multiplier is period-exact.** At the end of iteration D the book is
  valued at `open(D+1)`; `rets[D] = open(D+2)/open(D+1) − 1`; iteration D+1 trades at
  `open(D+2)`. Buy-and-hold over a synthetic price path returns **exactly one trade** and
  final capital matching the analytic `capital × open[n]/open[1]` to the rupee.
* **`capital_at_open` is the pre-return capital and is assigned before use.**
  `max|turnover × capital_at_open − blotter value| = 1.7e−10` across the full dev run, against
  ₹15,443 of error had post-return capital been used.
* **`vol_scalar`'s `shift(1)` → `shift(2)` is tight.** A vol spike at index 30 first moves
  `realised_vol` at index 32; `net_return[30]` spans `open(31)→open(32)`, observable only at
  the close of 32. Neither leaky nor over-conservative, and no off-by-one at the series start.

### The pattern, three audits running

**Every one of these was found by an implausible number or an adversarial probe, never by
reading the code.** The re-equalisation defect had been present through two prior audits and
340 passing tests, and was invisible until the mark-to-market fix gave it a way to show up in
a cost figure. Three audits, ~42 defects, and the rate is not yet falling.

The narrower lesson: **a fix that is entirely correct can still change what the system
measures.** Marking to market was right, and it silently converted a dormant modelling
sloppiness into a 2.6pp-of-CAGR leak. The trigger for this audit was refusing to report one
number that looked wrong.

---

## 11k. Re-measurement of the four finalists on the audited engine (2026-08-22)

No new research, no new factors, no search — the same four configurations, re-run.
`scripts/remeasure_finalists.py`. **0 new configs; the deflation denominator is unchanged.**

The move toward blends rested on two pillars: **risk** and **cost**. §11j moved cost. This
asks whether the ranking survives it.

### Method corrections this run makes

Two, both of which change numbers materially and neither of which is a new idea:

**One common window.** The equity panel starts in 2011, the NIFTY file in Feb 2012, and the
index carries ~135 fewer sessions over the overlap. `metrics.compute` annualises by *row
count*, so a sparser series is reported over a shorter year and its CAGR is flattered.
Everything below is measured on the **intersection (2012-02-22 → 2021-12-31, 2,338
sessions)**, annualised by calendar span. This alone moves momentum's CAGR from 21.3% to
23.5% — it drops 2011, a −19.9% year — so these figures are **not** comparable to §11i's.

**Blends are rebalanced monthly and charged for it.** `sprint_blends.py` blended the two net
*return series*, which silently restores exact blend weights every session for free — the
same modelling error §11j found in the engine, one level up. Here each sleeve's capital
drifts with its own return and the monthly correction is a real trade at the measured rate.

### The four, re-measured

| Config | CAGR | SR | gross SR | maxDD | Turnover | Drag | bps | Beta | **After-tax** |
|---|---|---|---|---|---|---|---|---|---|
| 25/75 Mom + LowVol | 16.0% | **1.00** | 1.11 | −36.4% | 449% | 1.77% | 38.6 | 0.80 | 13.0% |
| Low Volatility | 13.4% | 0.94 | 1.05 | **−33.3%** | 384% | 1.57% | 39.4 | 0.71 | 10.8% |
| **Momentum** | **23.5%** | 0.92 | 1.00 | −53.1% | 576% | 2.25% | 36.2 | 1.07 | **19.2%** |
| 25/75 Mom + NIFTY | 15.0% | 0.82 | 0.85 | −37.6% | **161%** | **0.59%** | 31.5 | 1.02 | 12.2% |
| NIFTY buy & hold | 12.0% | 0.68 | 0.68 | −37.8% | 0% | 0.00% | — | 1.00 | 11.0% |

### Does the ranking change? On one measure no, on the other decisively yes.

**By Sharpe the order is unchanged — but the gap has all but closed.**

| | before (§11g/h) | now | change |
|---|---|---|---|
| 25/75 Mom + LowVol | 1.11 | 1.00 | −0.11 |
| Momentum | 0.82 | 0.92 | **+0.10** |
| **gap** | **0.29** | **0.08** | **−72%** |

The blend still ranks first, and it moves in both directions at once: momentum gains from the
cost fix, the blend gives some back to the blend-rebalancing cost that was previously free.
**A 0.08 Sharpe gap is not a ranking.** At 69 configs over 9.86 years the multiple-testing
noise threshold is **0.93** — the entire gap sits inside the noise band, and momentum is
*at* the threshold:

| Config | SR | Deflated | Verdict |
|---|---|---|---|
| 25/75 Mom + LowVol | 1.00 | +0.07 | survives |
| Low Volatility | 0.94 | +0.01 | survives, barely |
| Momentum | 0.92 | −0.01 | indistinguishable from noise |
| 25/75 Mom + NIFTY | 0.82 | −0.11 | indistinguishable |
| NIFTY buy & hold | 0.68 | −0.25 | indistinguishable |

Nothing here is comfortably distinguishable from noise. Choosing the blend over momentum on
0.08 Sharpe is selecting on a difference smaller than the measurement error.

**By after-tax CAGR the ranking reverses outright.**

| Config | After-tax CAGR | vs NIFTY | maxDD | After-tax return per unit of DD |
|---|---|---|---|---|
| **Momentum** | **19.2%** | **+8.2pp** | −53.1% | **0.36** |
| 25/75 Mom + LowVol | 13.0% | +2.0pp | −36.4% | **0.36** |
| 25/75 Mom + NIFTY | 12.2% | +1.2pp | −37.6% | 0.32 |
| NIFTY buy & hold | 11.0% | — | −37.8% | 0.29 |
| Low Volatility | 10.8% | **−0.2pp** | −33.3% | 0.32 |

*(20% STCG annually with loss carry-forward, paid out of the book so the reduced base
compounds. NIFTY buy-and-hold defers entirely: one 12.5% LTCG event on exit.)*

**Momentum alone now beats the index by 8.2pp after tax where the best blend manages 2.0pp.**
The earlier reading — that the best candidate was worth ~1pp after tax — was an artefact of
the broken engine. Momentum survives tax better *because* it earns more, not because it is
tax-efficient: it gives up the most to tax in absolute terms (4.3pp against NIFTY's 1.0pp)
and still wins by a wide margin.

**Low volatility is now worse than the index after tax.** Sharpe 0.94 versus NIFTY's 0.68 and
it still loses money relative to buy-and-hold, because 384% turnover and annual STCG together
consume a 1.4pp pre-tax edge. It is a real factor and not an investable one at this scale.

### The finding that decides it: the blend buys nothing per unit of risk

**After-tax return per unit of maximum drawdown is 0.36 for momentum and 0.36 for the
blend — identical to two decimals.** The blend cuts drawdown from 53% to 36% and cuts
after-tax return from 19.2% to 13.0%, in almost exactly the same proportion.

That reframes the choice. The blend is not risk-adjusted *improvement*; it is **de-risking at
a fair price** — the same thing holding 60% of the momentum book in cash would achieve, and
close to what §11g's diversification sprint predicted when it found correlations barely fall
during crashes. The honest statement is that momentum and the blend are the same trade at two
sizes, and the question is not which is better but how much drawdown is tolerable.

### What this does and does not change

**Changed.** Cost is no longer a reason to prefer blending. Momentum's drag is 2.25%, not the
~4.7% believed this morning; the blend's cost advantage (1.77%) is now 0.5pp, not 3pp. Of the
two pillars, **cost has collapsed as an argument.**

**Unchanged.** Risk has not moved at all. Momentum's drawdown is −53.1%, against a 25% target
in §1 — still the binding constraint, and §11i's finding stands: this is momentum's behaviour
in a sample with **one bear year in fourteen**, so the true tail is worse than measured and
cannot be measured from this data.

**Still true.** Every one of these is a long-only Indian equity book with beta 0.71–1.07. The
short leg does not exist at ₹2L (§11g), so none of this is a market-neutral proposition.

### Consequence

Momentum alone is the highest after-tax return available here and the only config clearing
the index by a margin that survives contact with 20% STCG. It is also the one that violates
the drawdown constraint by the widest margin. **That is a position-sizing decision, not a
factor-selection one** — and it is the user's to make, since it turns on tolerance for a
−53% drawdown that the data cannot bound from above.

---

## 11l. Drawdown Profile — max DD was hiding the important differences (2026-08-22)

Diagnostic only. `scripts/drawdown_profile.py`, backed by `algotrade/backtest/drawdown.py`
(15 tests). Same window, costs, engine and return series as §11k. **No parameters searched,
no holdout touched, no trial registered — 0 new configs.**

Max drawdown is one number describing one day. It cannot tell a book that falls 50% and
recovers in four months from one that takes three years, and it says nothing about how often
the investor is underwater.

### 1. Maximum drawdown, with its dates

| Strategy | Max DD | Start | Trough | Recovery | Sessions | Calendar days |
|---|---|---|---|---|---|---|
| NIFTY buy & hold | −37.8% | 2020-01-21 | 2020-03-25 | 2020-11-10 | 202 | 294 |
| **Momentum** | **−53.1%** | **2018-01-17** | 2020-03-25 | **2021-01-04** | **727** | **1,083** |
| 25/75 Mom + LowVol | −36.4% | 2018-09-04 | 2020-03-25 | 2020-09-15 | 497 | 742 |
| 25/75 Mom + NIFTY | −37.6% | 2020-01-21 | 2020-03-25 | 2020-10-08 | 179 | 261 |

**Momentum's worst drawdown lasted three years — 1,083 calendar days from peak to recovery,
against NIFTY's 294.** All four bottom on the same day (COVID), but momentum was *already
20 months underwater* when it arrived. That is the fact max drawdown cannot express: the
same −53% suffered over three years is a different investment than over ten months.

### 2. Frequency — momentum has FEWER drawdowns, not more

| Strategy | >10% | >20% | >30% | >40% | episodes >5% | >10% per year |
|---|---|---|---|---|---|---|
| NIFTY buy & hold | **9** | 1 | 1 | 0 | 16 | **0.91** |
| Momentum | **5** | 3 | 2 | **1** | 17 | **0.51** |
| 25/75 Mom + LowVol | 6 | 1 | 1 | 0 | **10** | 0.61 |
| 25/75 Mom + NIFTY | 6 | 3 | 1 | 0 | 14 | 0.61 |

**Momentum goes below −10% barely half as often as the index** (0.51/yr against 0.91), and
its *median* drawdown is shallower: **8.1% against NIFTY's 10.4%**. The pain is not frequent.
It is rare and severe — three episodes past 20% where the index had one, and the only
episode past 40% in the study.

### 3. Time underwater — no material difference

| Strategy | % underwater | Avg recovery | Median recovery | Longest recovery |
|---|---|---|---|---|
| NIFTY buy & hold | 89.7% | 61 | 45 | 157 |
| Momentum | 89.2% | 49 | **13** | **254** |
| 25/75 Mom + LowVol | **86.6%** | 68 | 37 | **145** |
| 25/75 Mom + NIFTY | 88.3% | 83 | 25 | **300** |

*(sessions, trough → prior peak, episodes ≥5%)*

All four sit below their high ~87–90% of the time — that is simply what a daily equity curve
looks like, and it does not discriminate. **Momentum is not underwater longer than the index;
it is underwater deeper.** Its median recovery is the *fastest* of the four (13 sessions)
while its longest is the second worst (254). A book that usually bounces back in three weeks
and occasionally takes a year.

### 4. Severity distribution (episodes ≥5%)

| Strategy | n | Average | Median | 90th pct | Worst |
|---|---|---|---|---|---|
| NIFTY buy & hold | 16 | 12.2% | 10.4% | 19.1% | 37.8% |
| Momentum | 17 | 13.2% | **8.1%** | **35.0%** | **53.1%** |
| 25/75 Mom + LowVol | **10** | 13.2% | 10.8% | 36.4% | 36.4% |
| 25/75 Mom + NIFTY | 14 | 12.0% | 9.8% | 20.7% | 37.6% |

Momentum's distribution is the most skewed in the study: **the lowest median and the highest
90th percentile.** Its three worst episodes are −53.1% (727 sessions), −35.0% (351) and
−27.7% (387).

### 5. Relative drawdown — the blends are market-driven, momentum is not

Comparing each strategy episode against **NIFTY's own worst peak-to-trough over the same
calendar window** (point-to-point would score a round trip as harmless):

| Momentum episode | Strategy DD | NIFTY DD | Excess | Verdict |
|---|---|---|---|---|
| 2018-01 → 2020-03 | −53.1% | −37.8% | −15.3% | market-driven |
| 2013-01 → 2013-09 | −35.0% | −17.5% | **−17.5%** | **FACTOR-SPECIFIC** |
| 2015-08 → 2016-02 | −27.7% | −18.5% | −9.2% | market-driven |
| 2017-08 → 2017-08 | −12.0% | −2.5% | **−9.5%** | **FACTOR-SPECIFIC** |
| 2021-10 → 2021-10 | −11.0% | −9.5% | −1.5% | market-driven |

**Momentum: 3 market-driven, 2 factor-specific. Both blends: 6 market-driven, 0
factor-specific.** Every blend episode tracks the index within ~3pp. So the blends do not
merely reduce drawdown — they **change its character**, converting factor-specific falls into
market ones. That matters because a market drawdown is one the investor was going to take
anyway by owning equities, while a factor drawdown is uncompensated and arrives with no
narrative to hold on through.

The −53.1% episode classifies as market-driven only because it spans COVID. On the 2013
episode the index fell 17.5% and momentum fell twice as far.

### 6. Downside capture

| Strategy | Downside | Upside | Up/down | Monthly down | Monthly up | Reading |
|---|---|---|---|---|---|---|
| NIFTY buy & hold | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | market-like |
| Momentum | 1.03 | 1.14 | 1.11 | **0.82** | **1.29** | market-like |
| **25/75 Mom + LowVol** | **0.75** | 0.82 | 1.09 | **0.69** | 0.95 | **defensive** |
| 25/75 Mom + NIFTY | 1.01 | 1.03 | 1.03 | 0.96 | 1.07 | market-like |

Only the low-vol blend is genuinely defensive, and it earns the label: downside capture 0.75
against upside 0.82, so the up/down ratio is *above* 1 — it gives up less than it saves,
rather than simply holding a smaller position.

**Momentum's monthly figures are the surprise: 0.82 down, 1.29 up.** Over contiguous months
it captures less of the market's falls and more of its rises. Momentum's problem is not that
it amplifies market declines — it is the factor-specific episodes that show up in no capture
statistic at all.

> **A measurement error worth recording.** Downside capture first read **exactly 1.00 for all
> four books**, including a beta-0.71 one and a beta-1.07 one. The cause was the compounded
> definition: chaining 1,081 non-contiguous down days sends every cumulative to ≈ −100%, and
> the ratio of two numbers approaching −1 is 1.00 whatever happened along the way. Now a
> ratio of *means*, with `capture_by_period` for the compounded form over contiguous months.
> Regression-tested. **The implausible number was the only thing that caught it** — the third
> time in two days.

### 7. Ulcer index — the metric that separates them

| Strategy | Ulcer | CAGR | Martin | **After-tax CAGR** | **After-tax Martin** |
|---|---|---|---|---|---|
| NIFTY buy & hold | **7.3** | 12.0% | 1.64 | 11.0% | 1.50 |
| Momentum | **18.3** | 23.5% | 1.29 | **19.2%** | **1.05** |
| 25/75 Mom + LowVol | 8.5 | 16.0% | 1.88 | 13.0% | 1.52 |
| 25/75 Mom + NIFTY | 7.8 | 15.0% | 1.93 | 12.2% | **1.57** |

**Momentum's ulcer index is 2.5× the index's.** On return per unit of ulcer — after tax —
momentum ranks **last, below buy-and-hold**: 1.05 against 1.50–1.57 for the other three.

**This directly contradicts §11k's ranking, and both are correct.** Momentum wins on
after-tax *return* by 8.2pp. It loses on after-tax return *per unit of pain*. Sharpe hid this
because volatility is symmetric and ulcer is not: momentum's 0.92 Sharpe is respectable
because its upside volatility is large, and the ulcer index refuses to count that as
compensation.

### 7b. Is it one bad episode, or a worse shape? **Both — but mostly the shape.**

Ulcer recomputed with each book's single worst episode excised:

| Strategy | Ulcer (full) | Ulcer (ex-worst) | Share from worst episode |
|---|---|---|---|
| NIFTY buy & hold | 7.3 | 5.7 | 22% |
| **Momentum** | **18.3** | **11.7** | **36%** |
| 25/75 Mom + LowVol | 8.5 | 5.4 | 37% |
| 25/75 Mom + NIFTY | 7.8 | 6.6 | 15% |

**Delete momentum's three-year disaster entirely and it is still 2.05× more painful than the
index** (11.7 vs 5.7). The worst episode explains 36% of its ulcer, not most of it. This
refutes the charitable reading — momentum is not a normal book with one catastrophe, it is
structurally a more painful book that *also* had a catastrophe.

### 8. Risk experience scorecard

| Strategy | Max DD | DD>20% | Median DD | Underwater | Longest rec | Down cap | Ulcer | **Martin (AT)** |
|---|---|---|---|---|---|---|---|---|
| NIFTY buy & hold | −37.8% | 1 | 10.4% | 89.7% | 157 | 1.00 | 7.3 | 1.50 |
| Momentum | −53.1% | 3 | **8.1%** | 89.2% | 254 | 1.03 | **18.3** | **1.05** |
| 25/75 Mom + LowVol | **−36.4%** | **1** | 10.8% | **86.6%** | **145** | **0.75** | 8.5 | 1.52 |
| 25/75 Mom + NIFTY | −37.6% | 3 | 9.8% | 88.3% | **300** | 1.01 | **7.8** | **1.57** |

### Answers

**1. Is momentum genuinely more painful, or just one bad drawdown?** **Genuinely more
painful, and it is not the one episode.** Excise the −53% entirely and its ulcer is still
2.05× the index's. It is *less* frequently in trouble than NIFTY (0.51 vs 0.91 episodes >10%
per year) with a shallower typical drawdown (8.1% vs 10.4%) — but when it goes, it goes far
and stays down: 3 episodes past 20%, the only one past 40%, and three years to recover.
**Rare, severe, slow — the worst combination for actually holding a position.**

**2. Does 25/75 Mom + LowVol materially improve the experience despite similar max DD?**
**Yes, and by more than max DD suggests.** Max DD moves 53.1% → 36.4%, but the experience
improves on every other axis at once: episodes >5% 17 → 10, episodes >20% 3 → 1, ulcer
18.3 → 8.5 (a 54% reduction), longest recovery 254 → 145 sessions, downside capture 1.03 →
0.75, and **every one of its 6 material episodes is market-driven rather than
factor-specific.** After-tax Martin 1.05 → 1.52. Max drawdown understated this improvement
badly.

**3. Best risk-adjusted investor experience across depth, frequency, recovery and time
underwater?** **25/75 Momentum + Low Vol.** Deepest-drawdown-lowest, fewest episodes, fewest
>20%, least time underwater, shortest longest-recovery, and the only genuinely defensive
downside capture. Mom + NIFTY edges it on ulcer (7.8 vs 8.5) and after-tax Martin (1.57 vs
1.52), but has 3 episodes past 20% and a **300-session** longest recovery — the worst in the
study — so it is worse on exactly the axes that break an investor's resolve.

**4. If max DD is removed as the sole criterion, what would a rational long-term investor
choose?** Three of the four are statistically indistinguishable on after-tax Martin
(1.50–1.57): **the low-vol blend, the NIFTY blend, and buy-and-hold itself.** Momentum alone
sits well below at 1.05. So on pain-adjusted after-tax return, **the honest answer is that
none of the active strategies beats simply owning the index** — the blends match it, they do
not beat it.

The choice therefore rests entirely on the question the §11k/§11l tension poses:

* If the investor can hold through **−53% and three years underwater**, momentum alone
  returns 19.2% after tax against the index's 11.0%. **+8.2pp/yr, and the pain is real.**
* If they cannot — and most cannot, which is precisely why the premium persists — the blends
  deliver an experience like the index's for a return like the index's, and the whole
  exercise has produced nothing worth 10–15 hours a week.

**This is a preference, not a measurement,** and §11i's caveat binds it: one bear year in
fourteen, so the −53% is momentum's behaviour in a *benign* sample and the true tail is
worse and unmeasurable from this data.

---

## 11m. Drawdown DURATION — and the holdout was opened (2026-08-22)

`scripts/drawdown_duration.py --through-holdout`. **This section contains held-out data.**

### 🔴 The holdout is no longer sealed

The user asked for 2010–2026, which crosses the 2022-01-01 boundary. I flagged it and asked;
the answer was to run the full archive. That decision is recorded here because it is
irreversible and because §11i had just established that **2026 is the only bear year in
fourteen** — making this simultaneously the most informative and the most contaminating
window available.

**No trial was registered and no formal look was consumed** (`holdout_looks_remaining` is
still 5), but that is a bookkeeping fact, not an epistemic one: **momentum's 2022–2026
behaviour has now been seen and cannot be un-seen.** Any future "out-of-sample confirmation"
on this window is no longer out of sample for drawdown shape. §8.3's protection against
selection bias is weakened for anything downstream of what follows. Recorded in
`trials.jsonl` with `touched_holdout: true`.

### What the holdout showed: the pattern held, and then repeated

Momentum's two largest post-2021 episodes:

| Peak | Trough | Recovery | Max DD | Cal days | Months |
|---|---|---|---|---|---|
| 2022-01-19 | 2023-03-29 | 2023-07-13 | −28.7% | 540 | 17.7 |
| **2024-07-16** | **2025-04-07** | **2026-08-13** | **−38.3%** | **758** | **24.9** |

The 2024–2026 episode is momentum's **second-worst ever by depth and second-longest by
duration**, and it recovered on 2026-08-13 — **seven days before the archive ends.** For
almost the entire holdout period the strategy was underwater.

This does not refute momentum. It confirms the dev-period diagnosis on fresh data: rare,
severe, slow. §11i warned the tail was unmeasured; it is now partly measured, and it looks
like the dev tail rather than better than it.

### Every completed momentum episode ≥5%, 2012–2026 (n=26)

The five that matter (>6 months) are tabled at the end. The other 21 are all under 3 months
except one, which is the finding.

### Duration buckets — peak to recovery, completed episodes

**Momentum**

| Bucket | Count | Share of episodes | Days | **% of total time** |
|---|---|---|---|---|
| < 3 months | **20** | **77%** | 634 | 12.0% |
| 3 – 6 months | 1 | 4% | 94 | 1.8% |
| **6 – 12 months** | **0** | **0%** | 0 | **0.0%** |
| 12 – 24 months | 3 | 12% | 1,666 | **31.5%** |
| > 24 months | 2 | 8% | 1,841 | **34.8%** |
| **Total** | **26** | | **4,235** | **80.0%** |

**NIFTY**

| Bucket | Count | Share of episodes | Days | % of total time |
|---|---|---|---|---|
| < 3 months | 6 | 30% | 177 | 3.3% |
| 3 – 6 months | 6 | 30% | 868 | 16.4% |
| 6 – 12 months | 5 | 25% | 1,206 | 22.8% |
| 12 – 24 months | 3 | 15% | 1,219 | 23.0% |
| > 24 months | **0** | **0%** | 0 | **0.0%** |
| **Total** | **20** | | **3,470** | **65.6%** |

**Episodes lasting at least:**

| Threshold | Momentum | NIFTY |
|---|---|---|
| ≥ 3 months | 6 | **14** |
| ≥ 6 months | 5 | 8 |
| **≥ 12 months** | **5** | 3 |
| **≥ 24 months** | **2** | **0** |

### 🔴 The finding: momentum's drawdowns are bimodal, NIFTY's are not

**Momentum has no episodes at all in the 6–12 month bucket.** 77% of its episodes resolve
inside three months; 20% run past a year. There is almost nothing in between. NIFTY's
distribution is the opposite — smoothly spread across every bucket, nothing past two years.

The consequence is the number that matters most in this section:

> **Momentum spends 66.3% of its entire fourteen-year history inside drawdowns lasting more
> than a year. NIFTY spends 23.0%.**

Two-thirds of the life of this strategy is inside a multi-year drawdown. That is not a tail
risk; it is the *typical* state of the book, punctuated by short violent recoveries.

### Duration comparison

**Calendar days, peak to recovery:**

| Strategy | n | Average | Median | 90th pct | Longest | Total underwater | % of sample |
|---|---|---|---|---|---|---|---|
| Momentum | 26 | 163 | **34** | **588** | **1,083** | 4,235 | **80.0%** |
| NIFTY | 20 | **174** | 175 | 411 | 423 | 3,470 | 65.6% |

**Trading days:**

| Strategy | n | Average | Median | 90th pct | Longest |
|---|---|---|---|---|---|
| Momentum | 26 | 107 | **23** | **387** | **727** |
| NIFTY | 20 | 114 | 119 | 268 | 288 |

**Momentum's average duration is shorter than NIFTY's (163 vs 174 days) and its median is
five times shorter (34 vs 175).** On any measure of central tendency momentum recovers
faster. On the tail it is far worse: 90th percentile 588 vs 411, longest 1,083 vs 423 — and
two episodes past 24 months where the index has none.

**This is why the average is the wrong statistic here and reporting it alone would mislead.**
The mean is dragged down by twenty sub-3-month blips that no investor would notice, while the
lived experience is dominated by five episodes that consume two-thirds of the timeline.

### Every episode longer than 6 months, by duration

| Strategy | Peak | Trough | Recovery | Max DD | Cal days | Months | To trough | To recover |
|---|---|---|---|---|---|---|---|---|
| **Momentum** | 2018-01-17 | 2020-03-25 | 2021-01-04 | **−53.1%** | **1,083** | **35.6** | 534 | 193 |
| **Momentum** | 2024-07-16 | 2025-04-07 | 2026-08-13 | **−38.3%** | **758** | **24.9** | 181 | 333 |
| Momentum | 2015-08-07 | 2016-02-29 | 2017-03-17 | −27.7% | 588 | 19.3 | 133 | 254 |
| Momentum | 2022-01-19 | 2023-03-29 | 2023-07-13 | −28.7% | 540 | 17.7 | 296 | 66 |
| Momentum | 2013-01-08 | 2013-09-04 | 2014-06-30 | −35.0% | 538 | 17.7 | 162 | 189 |
| NIFTY | 2024-09-30 | 2025-04-07 | 2025-11-27 | −17.1% | 423 | 13.9 | 129 | 159 |
| NIFTY | 2015-07-24 | 2016-02-25 | 2016-09-07 | −19.1% | 411 | 13.5 | 141 | 127 |
| NIFTY | 2021-10-20 | 2022-06-17 | 2022-11-09 | −16.3% | 385 | 12.6 | 162 | 97 |
| NIFTY | 2013-05-21 | 2013-09-04 | 2014-03-11 | −17.5% | 294 | 9.7 | 72 | 126 |
| NIFTY | 2020-01-21 | 2020-03-25 | 2020-11-10 | −37.8% | 294 | 9.7 | 45 | 157 |
| NIFTY | 2018-09-04 | 2018-10-29 | 2019-04-18 | −14.2% | 226 | 7.4 | 34 | 117 |
| NIFTY | 2012-02-23 | 2012-05-18 | 2012-09-17 | −13.8% | 207 | 6.8 | 57 | 84 |
| NIFTY | 2022-12-02 | 2023-03-29 | 2023-06-05 | −10.0% | 185 | 6.1 | 82 | 39 |

**All five of momentum's long episodes are deeper than every one of NIFTY's eight bar one**
(COVID, −37.8%). And momentum's long episodes are long on both legs: 534 sessions *down* in
2018–2020, 333 sessions *climbing back* in 2025–2026.

NIFTY has one episode still open at the sample end (2026-01-06, −15.0%, 153 sessions and
counting), excluded from every statistic above rather than closed at the sample end.

### What this changes

**Confirms** §11l's diagnosis on fresh data, in a bear period dev did not contain: rare,
severe, slow. Momentum recovered from its 2024–2026 drawdown one week before the archive
ends.

**Adds** the bimodality, which no depth-based metric could show and which reframes the
holding problem. The question is not "can you survive a −53% drawdown" — it is **"can you
stay invested through a strategy that is inside a multi-year drawdown two-thirds of the
time?"** That is a much harder ask, and it is the strongest argument yet for the blends,
whose episodes are shorter, shallower and market-shaped.

**Costs** the holdout's virginity. Whatever is decided next has to be honest that 2022–2026
has been inspected.

---

## 11n. Investor-Experience Analysis — momentum vs the blend (2026-08-22)

`scripts/investor_experience.py`. Diagnostic only, 0 new configs. Full archive
2012-02-22 → 2026-08-20 (14.49 years), including the holdout window already inspected in
§11m. Relative performance measured as a **ratio** of wealth curves, not a difference of
cumulative returns, which grows mechanically with elapsed time.

### 🔴 Read every percentage below against this

| Horizon | Windows reported | **Independent windows** |
|---|---|---|
| 1-year | 162 | ~14.5 |
| 3-year | 138 | **~4.8** |
| 5-year | 114 | **~2.9** |
| 10-year | 54 | **~1.4** |

**The "100% of 5-year windows beat NIFTY" result rests on about three independent
observations, and the 10-year figures on roughly one.** They are not probabilities. Combined
with §11i — one bear year in fourteen — the long-horizon numbers describe *this* history, not
a distribution. Everything below is subject to that.

### 1. Rolling returns

**1-year** (~14.5 independent)

| Strategy | Mean | Median | 10th | 25th | Worst | Best |
|---|---|---|---|---|---|---|
| Pure Momentum | 28.3% | 16.3% | **−17.0%** | −3.0% | −28.5% | 140.1% |
| 25/75 Mom + LowVol | 16.8% | 15.0% | −5.7% | 2.1% | −20.7% | 75.0% |
| NIFTY | 14.0% | 11.2% | −3.5% | 2.3% | −27.9% | 73.7% |

**3-year** (~4.8 independent)

| Strategy | Mean | Median | 10th | Worst | Ann. median |
|---|---|---|---|---|---|
| Pure Momentum | 103.0% | 99.0% | 32.8% | −5.6% | **25.8%** |
| 25/75 Mom + LowVol | 59.0% | 60.9% | 22.3% | −9.1% | 17.2% |
| NIFTY | 47.6% | 47.8% | 24.8% | −8.5% | 13.9% |

**5-year** (~2.9 independent)

| Strategy | Mean | Median | 10th | Worst | Ann. median |
|---|---|---|---|---|---|
| Pure Momentum | 221.0% | 201.7% | 75.5% | **+9.9%** | **24.7%** |
| 25/75 Mom + LowVol | 111.4% | 113.9% | 57.4% | +6.4% | 16.4% |
| NIFTY | 89.8% | 81.8% | 47.0% | **−4.9%** | 12.7% |

**Beat rates**

| Horizon | Strategy | Beats NIFTY | Loses by >5pp | Wins by >5pp |
|---|---|---|---|---|
| 1Y | Momentum | 63% | **31%** | 57% |
| 1Y | Blend | 62% | 14% | 38% |
| 3Y | Momentum | **95%** | 3% | 89% |
| 3Y | Blend | 71% | 15% | 57% |
| 5Y | Momentum | **100%** | 0% | 100% |
| 5Y | Blend | 91% | 8% | 81% |

**Momentum's edge is horizon-dependent in the strongest possible way.** At one year it is
close to a coin flip that loses badly a third of the time. By three years it wins 95% of
windows; by five, all of them. The blend is *steadier at one year* (loses by >5pp only 14% of
the time against momentum's 31%) but **never catches up** — 91% at five years against
momentum's 100%.

### 2. Underperformance streaks — the blend is worse, not better

Defined as a drawdown in relative wealth: from the strategy's best-ever standing against the
index until it regains that standing.

| | Pure Momentum | 25/75 Mom + LowVol |
|---|---|---|
| Streaks (≥1%) | 47 | **23** |
| Average | 105 days | 213 days |
| Median | 13 days | 16 days |
| **Longest** | **1,203 days (3.3 yrs)** | **1,742 days (4.8 yrs)** |
| > 1 year | 5 | 4 |
| > 2 years | 3 | 3 |

**Momentum's long streaks**

| From | Trough | To | Days | Shortfall |
|---|---|---|---|---|
| 2018-01-16 | 2019-08-06 | 2021-05-03 | **1,203** | **−39.7%** |
| 2012-06-01 | 2014-05-20 | 2014-09-09 | 830 | −27.4% |
| 2024-07-16 | 2025-04-07 | 2026-08-13 | 758 | −30.2% |
| 2016-01-14 | 2016-11-22 | 2017-09-07 | 602 | −21.3% |
| 2022-04-13 | 2023-03-29 | 2023-09-05 | 510 | −27.4% |

**Blend's long streaks**

| From | Trough | To | Days | Shortfall |
|---|---|---|---|---|
| 2021-08-04 | 2023-02-16 | 2026-05-12 | **1,742** | −12.3% |
| 2018-01-15 | 2019-07-25 | 2021-07-02 | 1,264 | −21.8% |
| 2012-09-07 | 2014-05-20 | 2014-12-16 | 830 | −14.7% |
| 2016-01-12 | 2016-03-21 | 2017-07-10 | 545 | −7.0% |

**This is the section that changed my mind.** The blend's strongest claim was that it is
easier to hold. On *absolute* drawdown that is true (§11l). On **relative** performance —
which is what an investor who has chosen an active strategy over an index fund actually
watches — **the blend trails NIFTY for longer than momentum does: 4.8 years against 3.3.**

It trails less deeply (−12.3% vs −39.7%), so the choice is between a shallow disappointment
lasting nearly five years and a sharp one lasting three and a half. Neither is comfortable,
and the blend does not obviously win.

### 3. Relative drawdown vs NIFTY

| Strategy | Max relative DD | Avg | Relative ulcer | Longest recovery | % time behind peak |
|---|---|---|---|---|---|
| Pure Momentum | **−39.7%** | −6.8% | **17.7** | 1,203 | 94.8% |
| 25/75 Mom + LowVol | −21.8% | −3.9% | 8.0 | **1,742** | 96.4% |

On depth and ulcer of *relative* performance the blend is clearly better (8.0 vs 17.7). On
duration it is worse. Both spend ~95% of the time below their best-ever standing against the
index, which is simply what a relative-wealth curve looks like and does not discriminate.

### 4. Terminal wealth — ₹100 lump sum at every month end

| Horizon | Strategy | Median | Worst decile | Best decile | **Worst** | P(beat NIFTY) |
|---|---|---|---|---|---|---|
| 3Y | Momentum | 199 | 133 | 279 | 94 | **95%** |
| 3Y | Blend | 161 | 122 | 185 | 91 | 71% |
| 3Y | NIFTY | 148 | 125 | 174 | 91 | — |
| 5Y | Momentum | **302** | 175 | 526 | **110** | **100%** |
| 5Y | Blend | 214 | 157 | 271 | 106 | 91% |
| 5Y | NIFTY | 182 | 147 | 235 | **95** | — |
| 10Y | Momentum | **797** | 681 | 1,330 | **618** | 100% |
| 10Y | Blend | 404 | 369 | 529 | 354 | 100% |
| 10Y | NIFTY | 338 | 301 | 395 | 290 | — |

**Over ten years the distributions do not overlap: momentum's worst outcome (618) exceeds
NIFTY's best (421) and the blend's best (561).** With ~1.4 independent windows that is one
observation dressed as a distribution — but it is the observation we have.

At five years momentum's worst case (110) is a gain, while NIFTY's worst (95) is a loss.

### 4b. 🔴 Monthly contributions — the frame that decides it

₹10,000 at every month end. **Lump-sum statistics answer a question this investor is not
asking.** A monthly contributor buys *more* during a drawdown, and momentum spends 66% of its
life inside multi-year drawdowns (§11m) — so its worst feature becomes an accumulation
mechanism. XIRR is money-weighted; a time-weighted CAGR would credit returns on money not yet
contributed.

**5 years of contributions** (₹6L invested, 114 start months)

| Strategy | Median value | Worst | Best | Median XIRR | Worst XIRR | P(beat NIFTY) |
|---|---|---|---|---|---|---|
| Pure Momentum | **₹11.6L** | ₹5.17L | ₹19.7L | **27.5%** | −6.0% | **94%** |
| 25/75 Mom + LowVol | ₹9.12L | ₹5.22L | ₹12.0L | 17.3% | −5.7% | 77% |
| NIFTY | ₹8.44L | ₹5.14L | ₹11.4L | 14.1% | −6.3% | — |

**10 years of contributions** (₹12L invested, 54 start months)

| Strategy | Median value | **Worst** | Best | Median XIRR | **Worst XIRR** | P(beat NIFTY) |
|---|---|---|---|---|---|---|
| Pure Momentum | **₹46.4L** | **₹34.3L** | ₹65.6L | **25.9%** | **20.2%** | 100% |
| 25/75 Mom + LowVol | ₹27.4L | ₹23.7L | ₹34.0L | 16.0% | 13.2% | 100% |
| NIFTY | ₹25.8L | ₹21.5L | ₹30.9L | 14.8% | 11.4% | — |

Two results carry the decision:

* **Momentum's WORST 10-year SIP outcome (20.2% XIRR) beats every other strategy's BEST**
  (blend 13.2% worst / NIFTY's median 14.8%). Non-overlapping across 54 start months.
* **Momentum's worst 5-year SIP outcome (−6.0%) is no worse than NIFTY's (−6.3%).** The
  downside an accumulator actually faces is not worse than the index's — because contributions
  during the drawdown are what recover first.
* **The blend's 10-year SIP advantage over simply buying the index is 16.0% vs 14.8% — 1.2pp
  for 449% annual turnover and 10–15 hours a week.**

### 5. Investor experience

| Strategy | P(gain) 1Y | 3Y | 5Y | P(beat NIFTY) 1Y | 3Y | 5Y |
|---|---|---|---|---|---|---|
| Pure Momentum | **68%** | 99% | 100% | 63% | **95%** | **100%** |
| 25/75 Mom + LowVol | 80% | 99% | 100% | 62% | 71% | 91% |
| NIFTY | **81%** | 99% | 99% | — | — | — |

Momentum has the **worst one-year experience of the three** (68% chance of a gain against
NIFTY's 81%) and the best at every longer horizon. That gap is the entire behavioural problem
in one row.

### 6. Final decision table

| Metric | Pure Momentum | 25/75 Mom + LowVol | NIFTY buy & hold |
|---|---|---|---|
| CAGR | **22.4%** | 14.8% | 11.7% |
| After-tax CAGR | **18.3%** | 12.0% | 10.9% |
| Sharpe | 0.82 | **0.93** | 0.68 |
| **Martin (after tax)** | **1.08** | 1.44 | **1.54** |
| Max drawdown | **−53.1%** | −36.4% | −37.8% |
| Ulcer index | **17.0** | 8.3 | **7.1** |
| Longest drawdown | 1,083 d | 742 d | **423 d** |
| % time underwater | 90.5% | **88.1%** | 90.1% |
| % 3Y windows beat NIFTY | **95%** | 71% | — |
| % 5Y windows beat NIFTY | **100%** | 91% | — |
| Longest underperf. streak | 1,203 d | **1,742 d** | — |
| P(positive 5Y) | 100% | 100% | 99% |
| P(beat NIFTY 5Y) | **100%** | 91% | — |

**Note the row that undoes the blend's case: after-tax Martin 1.44 against buy-and-hold's
1.54.** On return per unit of ulcer, after tax, **the blend is worse than doing nothing.**

### Decision: for a monthly contributor holding 10+ years, **Pure Momentum**

> **⚠️ SUPERSEDED THE SAME DAY by §11o.** The comparison below is momentum against the
> *blend*, and on that comparison it is correct — the blend is eliminated. But it never
> tested momentum against **diluting momentum with the index**, and §11o found that halving
> the allocation is free on a return-per-unit-of-drawdown basis (0.34 either way) while
> cutting ulcer 17.0 → 9.8. **The deployed choice is 50/50 momentum/NIFTYBEES, not 100%
> momentum.** Everything below stands as the argument against the blend.

Not on CAGR. On these measurements:

**1. Long-term wealth creation.** Over ten years of monthly contributions the distributions
do not overlap — momentum's worst outcome (₹34.3L, 20.2% XIRR) exceeds the blend's best
(₹34.0L) and NIFTY's best (₹30.9L). Across 54 start months there is no start date at which
momentum lost to either.

**2. Consistency of outperformance.** 95% of 3-year and 100% of 5-year windows beat NIFTY,
against the blend's 71% and 91%. The blend is *steadier* at one year but loses more often at
every horizon that matters to a decade-long holder.

**3. Ability to stay invested.** This was the blend's case and the data does not support it as
cleanly as expected. The blend's longest stretch of trailing NIFTY is **4.8 years against
momentum's 3.3** — shallower but longer. And under monthly contributions momentum's worst
5-year outcome (−6.0% XIRR) is no worse than the index's (−6.3%), because the drawdown is
when the units get bought. **The pain metrics that condemn momentum — ulcer 17.0, Martin 1.08
— are lump-sum, mark-to-market measures. They penalise volatility that an accumulator
monetises, and they are the wrong lens for the question asked.**

**The blend fails on its own terms.** Its purpose was a better ride at acceptable cost; it
delivers a 1.2pp XIRR edge over buy-and-hold across ten years, a *longer* period of relative
underperformance than momentum, and an after-tax Martin ratio **below** the index. If the
conclusion is that momentum is unholdable, the honest alternative is NIFTYBEES — not the
blend, which adds 449% turnover and weekly effort for almost nothing.

### What would make this wrong

Stated plainly, because the confidence here should be moderate:

1. **~1.4 independent 10-year windows.** The headline is close to a single observation.
2. **One bear year in fourteen (§11i).** Momentum's global worst case is a bear-market
   *recovery* — US momentum lost ~80% in 2009 — and no such episode exists in this data. A
   −53% drawdown may be the benign-sample figure.
3. **The mid-cap concentration (§11i, R6).** The edge is 0.51 Sharpe in the liquid 100 against
   0.94 in the liquid 300, so it lives where the slippage model is least tested.
4. **Behaviour.** The strategy's failure mode is not underperformance — it is abandonment in
   year three of a 3.3-year losing streak against an index the investor can see daily. Every
   number above assumes the contributions keep arriving. **That assumption, not the factor, is
   the load-bearing one.**

---

## 11o. Deployment Readiness Sprint — the decision (2026-08-23)

`scripts/deployment_readiness.py`. Diagnostic only: no factors, no tuning, no holdout test,
**0 configs**. Full archive 2012-02 → 2026-08.

### P1 — Rolling stability: the edge is stable; the BLEND's is not

**After-tax excess over NIFTY, by window length**

| Window | Strategy | Median | 10th pct | Worst | **Windows below zero** |
|---|---|---|---|---|---|
| 5-year | Momentum | +8.5% | +1.8% | −1.0% | **2%** |
| 5-year | Blend | +0.5% | −1.8% | −2.9% | **41%** |
| 7-year | Momentum | +7.6% | +2.3% | **+0.7%** | **0%** |
| 7-year | Blend | −0.1% | −1.5% | −2.5% | **53%** |
| 10-year | Momentum | +7.2% | +5.6% | **+5.0%** | **0%** |
| 10-year | Blend | +0.7% | −0.8% | −1.2% | **30%** |

**Momentum: no 7-year or 10-year window failed to beat NIFTY after tax, and the worst
10-year window still won by 5.0pp.** Performance is not concentrated in a few exceptional
periods — the *worst* 10-year CAGR is 20.0% and the median 23.3%, a spread of 3pp.

**The blend loses to the index after tax in 41% / 53% / 30% of 5 / 7 / 10-year windows.**
Its median 7-year after-tax excess is *negative*. This is the third independent line of
evidence against it (§11l Martin, §11n SIP, now rolling windows) and it should be considered
eliminated.

Momentum's risk metrics are stable and bad in a consistent way: 10-year ulcer ranges 17.3 to
18.5, Martin 0.90 to 1.48, and every 10-year window contains the same −53.1% drawdown.

### P2 — Cost sensitivity: the edge tolerates 3× the modelled friction

Extra bps added **per side** by widening the spread and re-running the engine, so trade
sizing and compounding respond properly.

| Friction | CAGR | After-tax | Sharpe | After-tax vs NIFTY | Realised bps |
|---|---|---|---|---|---|
| current model | 22.4% | 18.3% | 0.82 | **+7.4%** | 31.1 |
| +25 bps/side | 20.7% | 16.9% | 0.76 | +6.0% | 57.0 |
| +50 bps/side | 18.9% | 15.5% | 0.70 | +4.6% | 82.9 |
| +75 bps/side | 17.2% | 14.1% | 0.63 | +3.2% | 109.0 |
| +100 bps/side | 15.6% | 12.7% | 0.57 | **+1.8%** | 135.1 |
| +150 bps/side | 12.3% | 9.9% | 0.45 | **−1.0%** | 187.9 |

**The after-tax edge survives to +100 bps per side and dies between +100 and +150.** Realised
cost today is 31.1 bps, so the strategy tolerates roughly **4× the modelled friction** before
matching the index. Each +25 bps/side costs ~1.4pp of after-tax CAGR, linearly.

This is the most reassuring result in the sprint. §11i's warning stands — the edge is mid-cap
concentrated where the slippage model is least tested — but the margin for the model being
wrong is large.

### P3 — Real investor simulation: ₹2L + ₹20k/month, taxes actually paid

**10-year horizon, 54 start months, ₹26.2L invested**

| Strategy | Median | Worst | Best | Med XIRR | Worst XIRR | P(beat NIFTY) |
|---|---|---|---|---|---|---|
| **Momentum** | **₹82.8L** | **₹62.4L** | ₹124.9L | **20.2%** | **15.4%** | **100%** |
| 25/75 Mom+LowVol | ₹53.1L | ₹46.4L | ₹66.3L | 12.6% | 10.2% | **67%** |
| NIFTY | **₹54.2L** | ₹46.0L | ₹64.3L | **13.0%** | 10.1% | — |

| Strategy | Median tax paid | Median costs | Costs % of final | Max DD of a/c | Worst vs invested |
|---|---|---|---|---|---|
| Momentum | ₹14.5L | ₹6.0L | 7.3% | −45.1% | **−27.6%** |
| 25/75 Mom+LowVol | ₹6.6L | ₹3.4L | 6.4% | −32.1% | −22.4% |
| NIFTY | ₹4.0L | ₹0 | 0.0% | −37.0% | −22.2% |

**🔴 Under a realistic SIP with tax actually paid each year, the blend LOSES to buy-and-hold:
₹53.1L against ₹54.2L, XIRR 12.6% vs 13.0%, beating the index in only 67% of start months.**
The blend is now eliminated on every measure that matters.

**Momentum wins all 54 start months**, worst case 15.4% XIRR against NIFTY's best-case
median. It pays ₹14.5L of tax and ₹6.0L of costs to do it — ₹20.5L of friction, against
₹4.0L for the index — and still ends ₹28.6L ahead at the median.

**SIP materially softens the drawdown.** The strategy's own max drawdown is −53.1%, but the
worst the *account* ever sits below money-invested is **−27.6%**, because contributions keep
buying in. That is barely worse than NIFTY's −22.2% on the same measure. **This is the single
most important number for deployability**: the lived drawdown of a contributing investor is
roughly half the headline figure.

*(Costs are charged at simulated capital, which is conservative — the flat ₹15.34 DP fee
becomes a shrinking share as the account grows from ₹2L to ₹26L, so real costs would be
lower.)*

### P4 — Behavioural failure risk: underperformance is certain, not a risk

47 underperformance episodes vs NIFTY. Median 13 days, mean 105, 90th percentile 510,
**longest 1,203 days (39.5 months)**.

| From | Trough | To | Days | Months | Rel. drawdown | Cumulative shortfall |
|---|---|---|---|---|---|---|
| 2018-01-16 | 2019-08-06 | 2021-05-03 | **1,203** | **39.5** | −39.7% | −39.7% |
| 2012-06-01 | 2014-05-20 | 2014-09-09 | 830 | 27.3 | −27.4% | −38.4% |
| 2024-07-16 | 2025-04-07 | 2026-08-13 | 758 | 24.9 | −30.2% | −26.5% |
| 2016-01-14 | 2016-11-22 | 2017-09-07 | 602 | 19.8 | −21.3% | −17.0% |
| 2022-04-13 | 2023-03-29 | 2023-09-05 | 510 | 16.8 | −27.4% | −26.0% |

**Probability of living through a streak of at least N years, from a random start:**

| Holding period | >1 year | >2 years | >3 years |
|---|---|---|---|
| 3 years | **97%** | 26% | 0% |
| 5 years | **100%** | **53%** | 25% |
| **10 years** | **100%** | **100%** | **100%** |

**Over a ten-year hold, a streak of more than three years of underperformance is not a risk
— it is a certainty.** Every one of the 54 start months contains one. The honest framing is
not "how likely am I to have a bad stretch" but "I will spend at least three consecutive
years wishing I had bought the index, and I must decide now that I will not act on it."

### P5 — Position sizing: dilution is nearly free, but does NOT fix the behaviour

| Allocation | CAGR | After-tax | Sharpe | Ulcer | **Martin (AT)** | Max DD | Longest DD | **Longest u/p streak** |
|---|---|---|---|---|---|---|---|---|
| 100% mom | 22.4% | **18.3%** | 0.82 | 17.0 | 1.08 | −53.1% | 1,083 | **1,203** |
| 75/25 | 20.0% | 16.2% | 0.85 | 13.3 | 1.22 | −47.5% | 1,049 | **1,198** |
| **50/50** | 17.3% | **14.1%** | **0.85** | 9.8 | **1.44** | −41.6% | 1,027 | **1,198** |
| 25/75 | 14.6% | 11.8% | 0.80 | 7.5 | **1.56** | −37.6% | 689 | **1,196** |
| 100% NIFTY | 11.7% | 10.9% | 0.68 | 7.1 | 1.54 | −37.8% | 423 | 0 |

**After-tax return per unit of maximum drawdown**

| Allocation | Per unit ulcer | **Per unit max DD** | Excess vs NIFTY |
|---|---|---|---|
| 100% mom | 1.08 | **0.34** | +7.4% |
| 75/25 | 1.22 | **0.34** | +5.4% |
| 50/50 | 1.44 | **0.34** | +3.2% |
| 25/75 | 1.56 | 0.31 | +0.9% |
| 100% NIFTY | 1.54 | 0.29 | +0.0% |

**Two findings, and the second is the important one.**

**1. Dilution with NIFTY is almost free on a drawdown-adjusted basis.** Return per unit of
max drawdown is flat at 0.34 from 100% down to 50% momentum, and Martin *improves*
monotonically (1.08 → 1.44). You give up return exactly in proportion to the risk you shed —
which makes the allocation a pure preference dial rather than a trade-off with a right
answer. **This is a strictly better lever than switching strategies:** 50/50 momentum/NIFTY
delivers +3.2pp after tax where the low-vol blend delivers −0.4pp, at a *smaller* drawdown
(−41.6% vs... the blend's −36.4% is lower, but the blend's after-tax return is below the
index, so it is dominated regardless).

**2. 🔴 Dilution does not shorten the underperformance streak.** 1,203 → 1,198 → 1,198 →
1,196 days. **Holding any momentum at all buys the full 3.3 years of trailing the index.**
Only 0% momentum escapes it. Dilution reduces the *depth* of the relative shortfall
(roughly proportionally) but not its *duration*, because the streak's start and end are set
by when the factor turns, not by how much of it you hold.

**The behavioural problem cannot be sized away.** It can only be made shallower.

---

## Deployment decision

### 1. Is Pure Momentum deployable with ₹2L + monthly contributions?

**Yes, on the evidence — with the size caveat below.** Four things had to be true and all
four are:

* **The edge is stable, not lucky.** 0 of 90 seven-year and 0 of 54 ten-year windows failed
  to beat NIFTY after tax; the worst ten-year window still won by 5.0pp (P1).
* **It survives realistic friction.** The after-tax edge holds to +100 bps/side, ~4× the
  31.1 bps realised today (P2).
* **It survives tax paid in cash, annually.** ₹14.5L of tax and ₹6.0L of costs on ₹26.2L
  invested, and still ₹28.6L ahead of the index at the median (P3).
* **The lived drawdown is roughly half the headline.** −27.6% below money-invested against a
  −53.1% strategy drawdown, because contributions buy the fall (P3).

**The caveat is position size, not strategy.** At ₹2L across 30 names each position is
₹6,667, and §11j showed the flat ₹15.34 DP charge is 34.6 bps of traded value at that scale —
57% of all cost. That is already priced into the 31.1 bps above, and P2 says there is room.
But **any allocation that shrinks the momentum sleeve below ~₹2L makes the cost problem
worse, not better** — halving the sleeve roughly doubles the DP share. Contributions fix this
over time; the first two years are the expensive ones.

### 2. What is the largest realistic behavioural risk?

**Certain, prolonged, visible underperformance against an index the investor can check
daily.** Not the −53% drawdown — P3 shows contributions halve what is actually experienced.

Over a ten-year hold, **100% of start months contain a >3-year underperformance streak**
(P4). The worst was 1,203 days and 39.7% cumulative shortfall. And P5 shows **this cannot be
diluted away**: any momentum allocation carries the full ~3.3-year duration.

The concrete failure: it is month 30 of trailing the index by 30%+, ₹20k is due, and the
rational-sounding argument for stopping is overwhelming. **Every favourable number in this
sprint assumes that contribution is made.** That assumption, not the factor, is the project's
main risk.

### 3. What allocation would I deploy?

**50% momentum / 50% NIFTYBEES**, and I would not deploy 100%.

The reasoning is P5's flat 0.34. Return per unit of max drawdown is *identical* from 100%
down to 50%, so the first half of the dilution costs nothing on a risk-adjusted basis while
buying:

* Max drawdown −53.1% → **−41.6%**
* Ulcer 17.0 → **9.8** (a 42% reduction)
* After-tax Martin 1.08 → **1.44**, above the blend and near the index's 1.54
* Still **+3.2pp** after tax over the index, compounding to a large sum over a decade
* Sharpe actually *improves*, 0.82 → 0.85

I stop at 50% rather than 25% because 25/75 gives up most of the edge (+0.9pp after tax) for
a further drawdown reduction that P5 shows is no longer free — per-unit-of-drawdown falls
from 0.34 to 0.31 there. 50/50 is the last point on the flat part of the curve.

The honest reason for not going to 100%, though, is **not in the return statistics — it is
P4.** The strategy's survival depends on contributions continuing through a certain
three-year underperformance streak, and a 50% allocation halves the depth of that shortfall
while costing nothing risk-adjusted. **I would rather hold half the position for ten years
than the full position for three.**

**I would not deploy the 25/75 momentum/low-vol blend at any weight.** It loses to
buy-and-hold on after-tax SIP XIRR (12.6% vs 13.0%), in 30–53% of rolling windows, and on
after-tax Martin. It was the leading candidate for two days and it does not survive contact
with tax and realistic rebalancing.

### 4. What evidence is still missing before committing real money?

Four things, in order of how much they should delay deployment:

**a) Real fills. Blocking for size, not for starting.** The edge is mid-cap concentrated
(Sharpe 0.51 in the liquid 100 vs 0.94 in the liquid 300, §11i) and the slippage model has
never met a real order. P2 says there is 4× headroom, which is why this does not block
starting — but it must be measured at ₹25k before scaling. **This is what Phase 0d exists
for.**

**b) A bear market. Cannot be obtained; must be sized around.** One bear year in fourteen
(§11i). Momentum's global worst case is a bear-market *recovery* — US momentum lost ~80% in
2009 — and no such episode exists in this data. **Size for a drawdown worse than −53%,
because the sample cannot bound it.**

**c) Shadow-portfolio agreement.** No signal has ever been generated live and compared to a
backtest fill. §9's zero-unexplained-discrepancy gate is unmet. Non-negotiable before real
capital, and cheap.

**d) A fourth audit.** Three audits, ~42 defects, and the rate is not falling — the third
found that "monthly rebalance" was trading 92.9% of sessions. Two further measurement errors
surfaced during these diagnostics. The base rate says the next audit finds something
material, and it should run against the *deployment* path specifically.

**What is NOT missing: more research.** The factor question is answered, the cost question
is answered, the tax question is answered, and the behavioural question is answered as well
as backtesting can answer it. Everything remaining requires real orders.

### 🔴 One thing that must be recorded honestly

**This hypothesis fails its own pre-registered gates.** §8.1 sets `max_drawdown_max = 0.20`;
momentum's is −53.1% and the 50/50 allocation's is −41.6%. Both fail outright. It is being
carried forward on the judgement that the gate was written for a market-neutral book that
§11g proved unavailable at ₹2L — **but rewriting a gate after seeing the result is precisely
what §8 exists to prevent.** The defensible course is to record the original gate as failed,
state the new one explicitly (a drawdown tolerance chosen deliberately, not fitted), and note
that this hypothesis was never formally passed.

---

## 11p. The Cost of Quitting — pricing the behavioural assumption (2026-08-23)

`scripts/cost_of_quitting.py`. Diagnostic only, 0 configs, no tuning, no holdout test.

§11o found that a >3-year underperformance streak is a **certainty** over any ten-year hold,
and that every favourable number in this project depends on the ₹20,000 still arriving in
month 30 of it. This puts a number on that assumption.

### Method

₹2L initial + ₹20k/month. Four quit behaviours, because stopping contributions is the mildest
thing a real investor does:

| Policy | Behaviour |
|---|---|
| **continue** | always contribute (the baseline) |
| **pause** | stop while behind, resume once back ahead of the index |
| **stop** | stop contributing permanently, keep holding |
| **switch** | redirect all *future* contributions to NIFTY |
| **capitulate** | **sell the momentum book into NIFTY** and contribute there |

`capitulate` pays 20% STCG on the realised gain at the moment of selling — a real cost of
panicking that this kind of comparison usually omits.

**Un-contributed money is credited back at 6.5%.** A quitter who stops contributing still
*has* the cash. Counting skipped contributions as destroyed wealth conflates "not invested"
with "lost" and roughly doubled the apparent cost of stopping in the first run of this
analysis. Everything below is net of that correction.

### A. Cost of quitting inside each historical streak

Investor starts three years before the streak (so there is a real balance to abandon), quits
N months in, measured at 2026-08-20.

**The 2018-01 streak — 39.5 months, −39.7% relative — the worst in the archive.**
*Continue ends at ₹1.14 crore.*

| Quit after | Policy | Final | Shortfall | % of wealth |
|---|---|---|---|---|
| 12 months | pause | ₹89.3L | −₹24.7L | **−21.6%** |
| 12 months | stop | ₹68.5L | −₹45.5L | **−39.9%** |
| 12 months | switch | ₹96.8L | −₹17.2L | −15.1% |
| 12 months | **capitulate** | ₹60.0L | −₹54.0L | **−47.3%** |
| 24 months | pause | ₹100.9L | −₹13.0L | −11.4% |
| 24 months | stop | ₹80.1L | −₹33.9L | −29.7% |
| 24 months | **capitulate** | ₹58.5L | −₹55.5L | **−48.7%** |
| 36 months | pause | ₹111.6L | −₹2.4L | −2.1% |
| 36 months | stop | ₹90.7L | −₹23.2L | −20.4% |
| 36 months | **capitulate** | ₹63.4L | −₹50.6L | **−44.4%** |

The other three long streaks show the same shape at smaller scale: quitting at 12 months cost
20–49%, and capitulating cost 23–52% **whenever it happened**.

### B. Across all 54 ten-year start months

**The trigger fires for 100% of investors at every threshold** — 12, 24 and 36 months. There
is no start date that avoids it.

*Continue: median ₹82.2L on ₹26.2L contributed.*

| Policy | Trigger | Median final | Median loss | **% of wealth** | Worst loss | % of starts hurt |
|---|---|---|---|---|---|---|
| pause | 12 mo | ₹60.3L | −₹22.4L | **−27.2%** | −₹37.5L | 100% |
| stop | 12 mo | ₹51.7L | −₹30.7L | **−37.3%** | −₹71.2L | 100% |
| switch | 12 mo | ₹63.9L | −₹20.6L | −25.0% | −₹49.6L | 100% |
| capitulate | 12 mo | ₹53.4L | −₹31.7L | **−38.5%** | −₹61.5L | 100% |
| pause | 24 mo | ₹74.9L | −₹9.4L | −11.4% | −₹20.9L | 100% |
| stop | 24 mo | ₹61.2L | −₹16.2L | −19.7% | −₹71.2L | 100% |
| switch | 24 mo | ₹71.5L | −₹10.5L | −12.8% | −₹49.6L | 100% |
| capitulate | 24 mo | ₹51.0L | −₹33.0L | **−40.2%** | −₹61.5L | 100% |
| pause | 36 mo | ₹79.6L | −₹3.0L | −3.7% | −₹7.9L | 89% |
| stop | 36 mo | ₹79.6L | −₹2.9L | −3.6% | −₹8.2L | 89% |
| switch | 36 mo | ₹80.6L | −₹1.3L | −1.6% | −₹5.1L | 89% |
| capitulate | 36 mo | ₹56.2L | −₹27.5L | **−33.4%** | −₹57.7L | 100% |

### 🔴 The two findings

**1. Quitting gets CHEAPER the longer you hold on — except selling.** Pause costs 27.2% at
12 months, 11.4% at 24, and 3.7% at 36. Stop: 37.3% → 19.7% → 3.6%. The logic is
uncomfortable but simple: **an investor who holds out for three years has already absorbed
most of the damage and is close to the recovery.** The early quitter pays the most because
they miss the most of what follows.

**2. Capitulating costs ~33–40% no matter when you do it.** 38.5% at 12 months, 40.2% at 24,
33.4% at 36. Selling is the one decision that does not get cheaper with patience, because it
crystallises the tax *and* forfeits the entire recovery permanently. **Selling after three
years of patience is almost exactly as expensive as selling after one.**

The practical reading: if you are going to lose your nerve, the *stopping contributions*
version of losing your nerve is survivable — and increasingly so the longer you last. The
*selling* version destroys a third to a half of terminal wealth regardless of timing.

### C. What it looked like at the moment of quitting

| Streak start | Behind at +12mo | at +24mo | at +36mo | Trough | Length |
|---|---|---|---|---|---|
| 2012-06-01 | −15.2% | −21.5% | ended | −27.4% | 27.3 mo |
| 2016-01-14 | −5.7% | ended | ended | −21.3% | 19.8 mo |
| 2018-01-16 | **−32.1%** | **−35.9%** | −20.6% | **−39.7%** | 39.5 mo |
| 2022-04-13 | −22.8% | ended | ended | −27.4% | 16.8 mo |
| 2024-07-16 | −16.9% | −3.3% | ended | −30.2% | 24.9 mo |

Note 2024-07: at 24 months in, the investor was only 3.3% behind and might reasonably have
relaxed — and the streak still had months to run and had been 30.2% deep at its trough.
**The path does not announce itself.**

### D. Quitting at the point where it actually felt most rational

The trigger dates above are arbitrary calendar marks. **The moment quitting feels most
rational is the trough** — maximum shortfall, no visible reason to expect it to stop. This
fires exactly there, with timing no real investor could achieve.

| Streak | Trough | Behind by | Months in | Policy | Final | **Cost** |
|---|---|---|---|---|---|---|
| 2016-01 | 2016-11-22 | −21.3% | 10.3 | pause | ₹134.1L | −21.8% |
| | | | | switch | ₹142.5L | −16.9% |
| | | | | **capitulate** | ₹82.7L | **−51.8%** |
| 2018-01 | 2019-08-06 | **−39.7%** | 18.6 | pause | ₹96.6L | −15.2% |
| | | | | switch | ₹100.5L | −11.8% |
| | | | | **capitulate** | ₹57.1L | **−49.9%** |
| 2022-04 | 2023-03-29 | −27.4% | 11.5 | pause | ₹50.2L | −3.8% |
| | | | | **capitulate** | ₹31.8L | **−39.1%** |
| 2024-07 | 2025-04-07 | −30.2% | 8.7 | pause | ₹24.1L | −1.8% |
| | | | | **capitulate** | ₹17.0L | **−30.8%** |

**Median cost of quitting at the trough: −15.2% of terminal wealth. Worst: −51.8%.**

---

## "How expensive was quitting at the point where it felt most rational?"

**Between 15% and 52% of final wealth, depending on how completely you quit — and the
distinction between those two numbers is entirely about whether you sold.**

The precise answer has three parts:

**If you stopped contributing at the trough: a median of 15.2%, and as little as 1.8%.**
Surprisingly survivable. By the time the pain peaks you are usually two-thirds of the way
through the streak, so you forgo relatively little of the recovery. On the worst streak in
the archive — 39.5 months, 39.7% behind — stopping contributions at the trough cost 15.2%.

**If you sold: a median of ~45%, and up to 51.8%.** Capitulation is the expensive mistake,
and uniquely it does not get cheaper with patience: 38.5% at 12 months, 40.2% at 24, 33.4%
at 36. Three years of white-knuckled discipline followed by selling produces almost the same
outcome as selling immediately. **All of the value of holding on is destroyed at the moment
of sale, not gradually while waiting.**

**And the earliest quitters paid the most.** Quitting at 12 months cost 25–39% of terminal
wealth; at 36 months, 2–4% (unless you sold). The most expensive moment to lose faith is the
*first* year of underperformance — which is also the year when the evidence for quitting is
weakest and the shortfall is smallest.

### What this means for deployment

**The behavioural assumption is worth roughly a third of the strategy's value, and it is not
optional — 100% of ten-year start months trigger it.** But the sprint splits the risk into
one part that is tolerable and one that is not:

* **Pausing contributions is a recoverable error.** Median 3.7–27.2% depending on timing, and
  it shrinks the longer you hold. An investor who stops contributing but does not sell keeps
  most of the outcome.
* **Selling is close to unrecoverable.** A third to a half of terminal wealth, whenever it
  happens.

**The single rule that captures nearly all of the value is therefore: never sell the book —
pausing contributions is permitted.** That is a far easier commitment to keep than "keep
contributing through 39 months of trailing the index," and §11p says it preserves most of the
edge. It should be written down before any capital is deployed, because it cannot be decided
in month 30.

This also strengthens §11o's recommendation of **50/50 momentum/NIFTY**. Halving the
allocation halves the depth of the relative shortfall — the thing that triggers selling —
while §11o showed it costs nothing on a return-per-unit-of-drawdown basis. **It does not
shorten the streak (1,203 → 1,198 days), but it makes the streak survivable, which is the
binding constraint.**

---

## 11q. Fourth Audit — the Sharpe ratio was not a Sharpe ratio (2026-08-23)

Commissioned because the §11j `trade_dates` fix was *recommended* by the third audit but
*implemented* afterwards and never checked, and because everything in §11k–§11p was new
unaudited code carrying a deployment decision. **13 findings. The two material ones point in
opposite directions, and the 50/50 allocation survives both — but three published numbers
were wrong and a second pre-registered gate turns out to be failed.**

### 🔴 F1. Sharpe was reported as return/vol on 100%-long books

`metrics.compute`'s own comment states the rule: rf must be 0 **for a dollar-neutral
long/short book**, because its return is already a self-financing spread. §11f made that
correction on such a book. **Every diagnostic since then carried `risk_free=0.0` onto
`buffered_long_only`, which is 100% long at beta 1.07** — where deducting rf is mandatory.

| Book | CAGR | Vol | Reported | **True Sharpe** |
|---|---|---|---|---|
| 100% momentum | 22.4% | 27.2% | 0.82 | **0.58** |
| 75/25 | 19.9% | 23.6% | 0.84 | **0.57** |
| **50/50** | 17.2% | 20.5% | **0.84** | **0.53** |
| 25/75 | 14.5% | 18.2% | 0.80 | **0.44** |
| NIFTY | 11.6% | 17.1% | 0.68 | **0.30** |

**The bias is `rf/σ`, so it is largest for the lowest-volatility book — precisely the axis
the allocation decision moves along.** Three consequences:

1. **§0's claim "Sharpe actually improves, 0.82 → 0.85" is reversed.** It *deteriorates*,
   0.58 → 0.53, monotonically with dilution. That argument for 50/50 is withdrawn.
2. **§1's target table is wrong.** It recorded "Sharpe 0.7–1.0 → 0.82 → met". True 0.58 →
   **missed**. That is **four of five targets missed**, not three.
3. **§8.1's `oos_sharpe_min = 0.8` is a SECOND failed pre-registered gate.** §0 recorded only
   the drawdown gate as failed. Momentum at 0.58 fails both.

`registry.gate_inputs` uses rf correctly, so the *formal* gate path was never wrong — but no
trial has a recorded result, and every human-facing table since §11g showed the inflated
number. **This is the finding with real epistemic weight: the instrument reported a
return/vol ratio as a Sharpe ratio for two days, and the error grew as volatility fell.**

### 🟢 F3. The blend's index sleeve was taxed as if actively traded

`deployment_readiness.py` gave every mixed allocation `deferred=False` — 20% STCG annually on
**100% of the blend's gain, including the NIFTYBEES half**. In the real deployment the
monthly correction almost always sells momentum (the outperformer) and buys the index, so
that sleeve is near buy-and-hold and defers to one 12.5% LTCG.

Recomputed with sleeve-level accounting, both rebalance legs charged (F6), and rf deducted:

| Allocation | After-tax (published) | **After-tax (correct)** | After-tax Martin | **AT per unit maxDD** |
|---|---|---|---|---|
| 100% momentum | 18.3% | 18.3% | 0.69 | 0.34 |
| 75/25 | 16.2% | **17.0%** | 0.79 | 0.36 |
| **50/50** | **14.1%** | **15.3%** | **0.91** | **0.37** |
| 25/75 | 11.8% | **13.3%** | 0.90 | 0.35 |
| 100% NIFTY | 10.9% | 10.8% | 0.61 | 0.29 |

**§0's pillar — "return per unit of max drawdown is flat at 0.34, so the first half of the
dilution is free" — is wrong in the FAVOURABLE direction.** It is not flat and dilution is
not free: it **peaks at 50/50 (0.37)**. So does after-tax Martin (0.91, against the index's
0.61 — 50/50 is *above* buy-and-hold, not below it, once rf is deducted).

### F2. `capitulate` double-taxed, overstating the cost of selling by up to 6.3pp

`cost_of_quitting.py` used lifetime contributions as the cost basis, but annual
mark-to-market taxation had already stepped the basis up to the post-tax value. Hand case:
₹2L, +50% in year one, sell mid-year-two — charged **36% on a 20% gain**.

| | Published | **Corrected** |
|---|---|---|
| capitulate @12 months | −38.5% | −37.5% |
| capitulate @24 months | −40.2% | −39.7% |
| capitulate @36 months | **−33.4%** | **−27.0%** |
| quitting at the trough, median | −15.2% | −14.6% |
| worst | −51.8% | −49.8% |

**The never-sell rule holds** — selling is still the worst policy at every trigger, and still
costs 27–40% — but §0's "33–40% whenever you do it" becomes **27–40%**, and "three years of
discipline followed by a sale is nearly as bad as selling immediately" weakens: 27.0% against
37.5%. Patience is worth more than §11p claimed.

### Minor findings, all fixed or recorded

* **F5.** Neither SIP simulator taxed the **final partial year**, while NIFTY paid LTCG on
  its entire gain including the stub — a mean 1.48% of terminal wealth in momentum's favour.
  `metrics.after_tax_cagr` did *not* have this hole, so two tax machines disagreed. Fixed.
* **F6.** The blend rebalance charged **one leg**, not two. Worth ~0.1pp. Fixed.
* **F8.** Quitters' cash accrued at 6.23%, not 6.5% — a 1/250 exponent against an archive
  averaging 239.9 sessions/year. Fixed.
* **F4.** The two SIP simulators disagree by **1.455%**: one makes 121 contributions in a
  120-month window, the other 120. Arithmetic is right in both; the published medians
  (₹82.8L vs ₹82.2L) are not comparable quantities.
* **F13.** `implied_trade_dates` yields 225 dates against the true cadence's 182. All 43
  extras are `buffering.py:125` dropping a name mid-month — but **~30 are transient panel
  holes, not delistings** (ASIANPAINT returns one session later with 3,209 rows to go). The
  true cadence gives CAGR **21.09% vs 20.74%**, so the published figure is 0.35pp
  conservative. Safe direction; the docstring's stated mechanism is wrong.
* **F7.** P3 charges the ₹2L cost rate to an account growing to ₹26L. Measured across six
  capital levels: drag 2.13% @₹2L → 1.58% @₹2.6cr, DP's share 33.6% → 0.3%. The SIP result is
  **~0.55pp of CAGR conservative**, and market impact does not offset it.
* **F11.** Cost is subtracted from `running` but not from `held`, so the book is valued
  **+0.109% above capital on average** between rebalances — ~1.001× implicit leverage, ≈
  +0.03pp CAGR.
* **F9.** §0's SIP-XIRR row cited §11n's ₹10k/month figures while the prose beneath cited
  §11o's ₹2L+₹20k figures, so the table showed the blend *beating* NIFTY while the text said
  the opposite. Corrected to one consistent simulation.

### Verified clean — tested, not assumed

* **`after_tax_cagr` carry-forward is correct.** Six hand-computed cases exact to 1e-6:
  consecutive losses, loss→partial recovery, a 13-month window (correctly taxes two years), a
  total-loss year. Two non-bugs recorded: it uses the calendar year rather than India's
  April–March FY, and the ₹1.25L LTCG exemption is absent — which would help **NIFTY**, not
  momentum, so its omission is not a bias in the strategy's favour.
* **`drawdown.py` is clean** against 11 adversarial inputs — single bar, V recovering on the
  final bar, flat plateau at peak, equal troughs, new high on the final bar. No off-by-one;
  `duration == to_trough + to_recover` identically; ulcer and Martin exact against an
  independent implementation.
* **`implied_trade_dates` key formatting is sound** — order-insensitive, 1/3 vs 0.3333333333
  correctly equal, an 11th-decimal change correctly detected.
* **All four blend implementations agree to 4.4e-16.**
* **The delisted-symbol worst case does not occur.** Frozen positions exist (89 of 3,718
  sessions, max 13.2% of book, always the 2 sessions before eviction) but nothing survives to
  the end: the name leaves the book next session, which is itself an implied trade date.
* **§11j reproduces**: `trade_dates=None` → drag 4.71%; with → 2.13%.

### Verdict

**The 50/50 allocation survives, and is better supported than before — but for different
reasons than §11o gave.** The Sharpe argument is dead; the Martin and per-unit-drawdown
arguments are stronger than published, and both now **peak exactly at 50/50**. Max drawdown,
ulcer and the entire P4 behavioural case are untouched.

What is genuinely lost is the claim that the choice rests on five independent statistics.
One of the five was an artefact. **The honest case for 50/50 is drawdown shape, after-tax
Martin, and the behavioural argument — which was always the deciding reason.**

---

## 11r. Corporate actions reached the backtest but never the book (2026-09-16)

**A held name split 2:1 and the book did not notice for three weeks.** Every
reconciliation passed the whole time.

TDPOWERSYS went ex on 2026-08-24 (face value ₹2 → ₹1). `paper_momentum_v1_001`
had bought 4 shares the session before at ₹1,507.50. The book still said 4
shares at ₹1,507.50 against a market near ₹725, so the position reported
**−51.9%** on something roughly flat, and NAV, weight, drawdown and the signal's
view of its own holding were all wrong with it.

### Why four checks saw nothing

§10.6 and §10.11 built complete corporate-action machinery — subject parsing,
back-adjustment on read, reconciliation of feed actions against observed price
moves. **All of it serves the research path.** The live book
(`executions → holdings → valuation`) never consulted the action feed at all.
Two paths, built at different times for different purposes, and the gap between
them was never named.

The reconciliation checks could not have caught it. All four compare our data
against *our own data*: the book agreed with its executions, the figures agreed
with each other, the snapshot was internally consistent. **A split nobody
recorded is consistent with everything.** That is a class of fault the existing
design was structurally blind to, not an oversight in any one check.

The fix is therefore as much about the check as the arithmetic: a fifth check
whose authority is **external** — every feed action on a held symbol must be
applied or explicitly refused.

### The book is a fold over two event streams

A corporate action is a second kind of event alongside `executions`, and
holdings are a fold over both in time order. That shape was forced, not chosen:

* **editing the execution** breaks append-only and records a purchase nobody made
* **patching `holdings`** does not survive — the daily job re-derives the book
  every session, so a hand-edited lot is reverted within a day and the
  reconstruction check then flags the patched book as corrupt

An action sorts **before every execution on its ex-date**, which is precisely
what an ex-date means. Ordering alone makes a buy-on-the-ex-date correct, with
no special case for anyone to forget.

Three details carry real money:

| | |
|---|---|
| `acquired_on` **unchanged** | a split is not an acquisition; resetting it reprices long-term gains at 20% instead of 12.5% |
| entitlement on the **whole holding** | the registrar computes on the demat position — lots of 2+2 under a bonus 1:3 give 5 shares, per-lot flooring gives 4 |
| lot price at **4 decimals** | ₹1,507.50 over 7 shares does not land on a paisa, and rounding drifts the basis past the ₹0.01 the reconstruction check compares at |

### The near-miss worth recording

Mid-fix, the pre-split snapshots looked halved — the panel showed TDPOWERSYS at
767.40 on 2026-08-21 where the bhavcopy says 1,534.80 — and switching valuation
to raw closes looked like the obvious second half of the repair.

**It was wrong and it doubles historical NAV.** Backfill marks the *current*
book at a past date's prices, and a current quantity already contains every
split since. Back-adjustment divides by the same factor, so they cancel:

    current_qty x adjusted(D)  ==  qty_at(D) x raw(D)
    8           x 767.40       ==  4         x 1,534.80     == 6,139.20

The back-adjusted panel was right all along, and right *because* the book became
split-aware. Caught by reading the restated snapshots rather than the reasoning:
a row showed 8 shares on a date whose price already assumed them. The
cancellation is now asserted in a test, because the mistake is more attractive
than the correct behaviour.

### What is refused, and why that is not laziness

Only share-count multipliers are applied: splits, bonuses, and their product.
The rest are recorded with a reason and left for a human. Counting the
2015–2026 feed against the liquid-300 of each year:

| | market-wide | in-universe | decision |
|---|---|---|---|
| split / bonus | ~50–100/yr | frequent | **applied**, corroborated against the ex-date price move |
| demerger | ~11/yr | **~3.1/yr** | **manual entry** — see below |
| rights | ~20/yr | occasional | refused; the factor needs `P_cum` *and* whether you subscribed |
| buyback | ~25/yr | occasional | refused; a tender is a decision, not an entitlement |
| reverse split | ~1.2/yr | **0 in 11 years** | refused |

**Reverse splits are not worth building.** Zero in-universe events in eleven
years, and structurally so — momentum ranks by trailing return, and a company
consolidating its shares is the opposite of a momentum leader. The parsing is
also treacherous: MONNETISPA 2018-08-29 reads 3.03× *in the wrong direction* on
its first clause alone.

**Demergers split into a manual half and a mechanical one.** ~3/yr in-universe
on exactly the names this book holds — RELIANCE, ITC, HINDUNILVR, SIEMENS, VEDL,
NMDC — so a 30-of-300 book meets one roughly every three years. But NSE's feed
says the word "Demerger" and nothing else (verified across every one that hit a
liquid name 2019–2026), and the cost apportionment is set by the scheme under
s.49(2C)/(2D) and published by the company weeks later. *Discovery* cannot be
automated; *application* can, and is: a `spinoff` event takes the two numbers a
human read off the filing and does the rest — parent basis reduced, child
position created with `acquired_on` **inherited** (s.2(42A)(g), so a long-term
parent cannot yield a short-term child), entitlement allocated on the whole
holding, charges left wholly with the parent.

Because the apportionment always arrives after the ex-date, the event is always
entered retroactively — which a fold over stored events handles natively, and
which is also why the child has normally listed by then and can be priced.

### Nothing is applied on one source

A parsed factor is never trusted alone. The ex-date bhavcopy must agree with it
within 15% before the book is touched: TDPOWERSYS printed 1,534.80 → 775.00, an
implied 1.98 against a feed factor of exactly 2. A mis-parsed subject therefore
cannot corrupt the book unless the price happened to move the same wrong way on
the same day. The tolerance is loose for the reason §10.6 already gives — a 1:10
split implies 9.56, not 10, because the stock also moved.

### Open

**Cash in lieu is not modelled.** A fractional entitlement is sold by the
registrar and paid out; here it is reported and dropped. For a 30-stock book the
amounts are trivial, but it is a known omission rather than a solved problem.

**A demerged child blocks the monthly rebalance until it lists**, via the
existing `CANNOT PRICE` guard. That guard is correct — an unlisted security has
no honest reference price — and the wait for the apportionment usually outlasts
the listing anyway.

---

## 12. Decisions Log & Open Items

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

### Open — data layer

~~A. Cache the corporate-action history~~ · ~~B. Full reconciliation~~ ·
~~C. Parquet panel~~ — **all DONE 2026-08-21, see §11.**

**D. Gap-rename bridging — PARTIALLY done, and the approach was wrong.** ISIN from the
action feed added only 12 chains, because the feed carries *today's* symbols only, so a
historical name never appears in it (§11). The correct source is the **`cm*bhav` mirror
(2010 → Jun 2021)**, which is cloned but unused for ISIN. That closes gap-renames to
mid-2021; after that no ISIN source exists and the limitation is permanent.

~~Registry / pre-registration~~ — **DONE.** `backtest/registry.py`: append-only trial log,
enforced holdout budget, deflated Sharpe. Caught an arithmetic error in §8.2's own table.

**E. Rights-issue adjustment.** 320 identified, none adjusted — the factor needs
`P_cum` from the panel, not just the subject (§11). Implementable, but it is a real
calculation rather than a parse. **On the live book this is now an explicit
refusal rather than an omission** (§11r): a rights issue is recorded with its
reason and surfaced until a human clears it, because the factor also depends on
whether the holder actually subscribed — which no feed knows.

**G. Corporate actions on the live book — DONE 2026-09-16, see §11r.** The
research path had them since §10.11; the book never did, and a 2:1 split showed
as a −51.9% phantom loss for three weeks past four clean reconciliations.
Splits and bonuses now apply automatically against a price-corroborated factor;
demergers take two human-supplied numbers; reverse splits stay refused (zero
in-universe events in eleven years).

**H. Cash in lieu of fractional entitlements.** Reported and dropped. A
registrar sells the fraction and pays out; that payment is not recorded. Trivial
in rupees for a 30-stock book, but a known omission rather than a solved
problem (§11r).

**F. Dividend adjustment.** Amounts are cached. Whether to build a total-return series is a
*strategy* decision: unadjusted dividends bias a cross-sectional ranking against
high-yield names by ~1–1.5%/yr.

### Open — research position (2026-08-22)

### Superseded 2026-08-23 — the research question is closed

*The paragraph below framed drawdown as the blocker to be solved. Six sprints later the
honest conclusion is that it is **not solvable at ₹2L and is not a defect** — it is the price
of the return. Kept for the record; the live position follows it.*

> ~~**The live question is no longer "is there a factor".** Momentum is established at gross
> Sharpe 0.97–1.24 over 11 years. Turnover is solved by rank buffering. What blocks a
> production candidate is **momentum-crash drawdown** (−54%), which §11g shows is
> factor-specific rather than inherited market beta, and which no tested lever fixes without
> pushing returns below the index.~~

**Every lever was tried and the drawdown did not move without taking the return with it:**
volatility targeting, market and stock trend filters, inverse-vol and ERC weighting, slower
cadences, four blend weights against three different streams (§11g, §11h, §11k). The one
combination that reduced drawdown materially — 25/75 momentum/low-vol — delivers **identical
after-tax return per unit of drawdown** (0.36 vs 0.36, §11l). It is a smaller position, not
a better strategy.

### ~~The live question (2026-08-23): deploy or don't~~ — ANSWERED, same day

**There is nothing left to research that would change the decision.** Momentum nets 18.3%
after tax against the index's 10.9%, wins 100% of 5-year windows and every one of 54
10-year SIP start months, and costs a −53% drawdown and 3.3 years of trailing the index at
the worst. That trade is now measured from several angles and they agree. **What remains is
a risk-tolerance decision, and it is not a technical one.**

**Decision taken: 50% momentum / 50% NIFTYBEES, subject to a written never-sell rule.**
See §0 for the table and §11o/§11p for the derivation. The three questions that stood open
this morning are now closed:

~~**D1. Can the drawdown actually be held through?**~~ **Reframed and priced (§11p).** The
question was never answerable as "yes/no" — it is a commitment, not a fact. What §11p adds
is that the commitment splits in two, and only one half is expensive:

* **Pausing contributions is recoverable** — 27.2% / 11.4% / 3.7% of terminal wealth if you
  stop at 12 / 24 / 36 months. It gets *cheaper* the longer you hold on.
* **Selling is close to unrecoverable** — 33–40% whenever it happens, because the tax is
  crystallised and the recovery forfeited.

So the deployable commitment is **"never sell", not "never waver"** — which is a far easier
promise to keep and preserves most of the edge. The 50/50 allocation halves the depth of the
shortfall that provokes selling.

~~**D2. Validate slippage before committing capital.**~~ **Still required, but downgraded
from blocker to scaling gate.** §11o measured the headroom: the after-tax edge survives
**+100 bps/side against a realised 31.1 bps** — roughly 4× the modelled friction — and dies
between +100 and +150. That margin is large enough to start at ₹25k and measure, rather than
requiring certainty first. It remains the highest-value technical task and must gate any
increase in size.

~~**D3. Size for a drawdown worse than any observed.**~~ **Partly addressed by the 50/50
choice, and it is the reason for it.** §11i's caveat is unchanged — one bear year in
fourteen, and momentum's global worst case is a bear-market recovery absent from this data —
so the measured −53.1% is the benign-sample figure. Halving the allocation to a −41.6%
measured drawdown is the deliberate response, and the drawdown tolerance replacing §8.1's
failed 20% gate must be set with that unmeasurable tail in mind, not fitted to −41.6%.

**D4 (new). The never-sell rule must be written down before capital moves.** §11p is
explicit that it cannot be decided in month 30. This is a prerequisite, not a nicety.

~~**R1. Is momentum-crash risk hedgeable at ₹2L?**~~ **ANSWERED: no.** Not by diversification
(§11g, R1b), not by blending (§11k), not by regime timing (§11h), and not by shorting — the
short leg does not exist at this capital. ₹10L+ does not fix it either; it changes what is
*tradeable*, not whether momentum crashes.

~~**R2. The regime filter is under audit.**~~ **RESOLVED 2026-08-22 — it failed.** The
0.70 → 1.07 lift was an artefact of deleting sessions instead of going to cash (§11h). The
honest implementation is 0.91 → **0.80**: the filter costs 0.11 Sharpe and trebles turnover.
§11g's combined result collapses with it.

~~**R2b. The look-ahead detector is never called outside tests.**~~ **RESOLVED 2026-08-22,
then hardened the same day** — the first version shipped with a `settle=25` blindfold that
made it report CLEAN on a literal one-day forward peek. See §11j.2.
`lookahead.check_pipeline` truncates the *raw* panel at several cutoffs, re-runs the entire
pipeline — universe construction, adjustment, feature, buffering, weights — and compares the
weights produced on the shared prefix. It catches leaks that live outside `feature_fn` and
are structurally invisible to the feature-level check, including the class of bug that
`exclusions.yaml` introduced. `run_hypothesis.py` runs it as a **pre-flight gate**, exits 3
before evaluating if the pipeline is not causal, and — since §11j.3 — does so *before*
registering the trial, so an abort costs no holdout budget. Cutoffs default to calendar month
ends and `settle` defaults to 0, so the comparison is exact rather than blindfolded.

**R3. 🔴 The holdout has been inspected (§11m, 2026-08-22).** Five *formal* looks remain and
no trial was registered against it, but momentum's 2022–2026 drawdown behaviour has been
seen: two further long episodes, the worse of them −38.3% over 25 months, recovering seven
days before the archive ends. Any future confirmation on this window is no longer clean for
drawdown shape, and §8.3's protection is correspondingly weaker. The remaining looks are
worth less than they were this morning. Original reasoning follows. Three reasons not to spend one yet, in order of weight:

1. ~~**The instrument changed today and is not yet re-verified.**~~ **AUDITED — and it
   found a bigger defect than the one it was sent to check (§11j).** The 7.2% drag was
   arithmetically correct; what it exposed was that the book re-equalised weights *daily*
   between monthly rebalances. Fixed, and the frozen book is now Sharpe **0.85** / CAGR
   **21.8%**. The engine is materially more trustworthy than it was this morning — but it
   also changed again today, and three audits have now averaged ~14 defects each.
2. **The candidate needs re-deriving before it can be confirmed.** The blend sweep ran on
   the pre-§11j engine, so every net number in it is understated — plausibly enough to
   change which blend wins. Re-run `sprint_blends.py` and `sprint_diversify.py` before
   deciding what, if anything, is worth a look.
3. **The deflation bar is high.** 69 configs on the books (corrected from 77, §11j) puts
   the multiple-testing threshold near the candidates' own Sharpe.

And §11i adds a fourth: **the holdout is the same regime as dev** (four bull/sideways years
plus one −6.7% year), so a pass would confirm the strategy in the conditions it is already
known to suit. *A pass would tell us very little, and a fail would end the hypothesis on a
measurement we do not yet trust.*

**R1b. Diversification was tested and does not solve it.** Momentum blended with low
volatility, long-term reversal, NIFTY and cash: correlations barely fall during crash windows
(0.77 → 0.76) because **every available stream is a long-only Indian equity book sharing beta
~1**. Reversal is an *amplifier*, worsening drawdown monotonically to −71%. No blend gets
below NIFTY's own −37.8%, which is the floor. The one genuine finding: **25% momentum / 75%
low-volatility matches the index drawdown with ~4pp more CAGR and Sharpe 1.11 vs 0.82** — a
modest improvement on buy-and-hold, not a solution, and ~1pp after 20% STCG.

**R4. Diversification is untested.** §7 argues three uncorrelated strategies at Sharpe 0.6
combine to ~1.0. Momentum alone is 0.80. A second *uncorrelated* signal is the untried
route to the target, and it is cheaper than more implementation work on one factor.

**R5. 🔴 The sample contains no bear market, so tail risk is unmeasurable (§11i).** One bear
year in fourteen (2026, −6.7%), and it sits in the holdout. Momentum's worst global episodes
are bear-market *recoveries* — US momentum lost roughly 80% in 2009 — and no such episode
exists anywhere in this archive. The −55% drawdown three sprints have failed to fix is
momentum's behaviour in **benign** conditions. This cannot be closed with more backtesting;
it needs either older data (pre-2010, covering 2008) or an explicit decision to size for a
drawdown worse than any observed.

**R6. The edge is concentrated in mid-caps (§11i).** Gross Sharpe 0.50 in the liquid 100 vs
0.95 in the liquid 300. Reassuring for robustness (stable 300 → 500) but it places the edge
exactly where the slippage model is least trustworthy and market impact is worst. Before any
capital is committed, the mid-tier slippage assumption needs validating against real fills —
this is now the highest-value calibration task.

### Open — carried forward

1. ~~Data source audit~~ — **RESOLVED 2026-08-21.** `eod2` rejected, `getbhavcopy`
   rejected, **`NSE-Data-bank` adopted** and delisted-name retention empirically verified.
   See §10.3.
2. ~~Zerodha rate card~~ — **RESOLVED 2026-08-21.** Fetched, worked examples computed,
   §3 corrected. Delivery ≈ 30 bps round trip, not the 8–15 bps assumed earlier.
3. **Corporate actions — REOPENED.** Coverage and the two traps were verified against
   `nsefin`'s **0.1.1** API (§10.6). This environment resolves the same pin to **1.0.0**
   with a different API (§10.9), so that validation must be **redone against 1.0.0** and the
   call wrapped behind a thin adapter in `io/actions.py`. Given §10.9, also evaluate
   fetching NSE's corporate-action endpoint directly, as `io/nse.py` does for bhavcopy.
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

## 13. Honest Probability of Success

| Stage | Odds (set 2026-08-21) | **Status 2026-08-23** |
|---|---|---|
| Find something that backtests beautifully | ~90% *(the danger, not the good news)* | ✅ happened, repeatedly, and three times it was a bug |
| Survives walk-forward + OOS + real cost modelling | ~25% | 🟡 **partly cleared** — survived real cost modelling and three audits; walk-forward and a *clean* OOS look are outstanding |
| Survives 6 months of live paper trading | ~50% of those | ⬜ not started |
| **Net: a genuinely tradeable edge after 12 months** | **~10–15%** | **revised to ~25–30%** — see below |

**Accept this before starting.** If a >50% chance of "there is no edge I can capture at
retail cost structure" is unacceptable, the project shouldn't start — because the
pressure to make it work anyway is exactly what produces overfit backtests and blown
accounts.

### Revised estimate (2026-08-23): ~25–30%, and the reasons cut both ways

**Raised because:** the edge survived the thing that kills most retail backtests — honest
cost modelling at ₹2L, where a flat ₹15.34 DP charge is 300+ bps on a small trade (§11j).
Momentum nets 18.3% after 20% STCG against the index's 10.9%. It cleared three adversarial
audits, ~42 defects, and every fix that mattered moved the number *in the same direction as
the economics predicted*. It is a documented, widely-replicated factor rather than something
found by search — and only 69 configs were explored, so the multiple-testing haircut is
small.

**Not raised further because:**

* **The gates in §8.1 have still not been formally cleared.** Max drawdown −53.1% fails the
  <20% gate outright. On the pre-registered criteria this hypothesis **does not pass**; it is
  being carried forward on a judgement that the gate was set for a market-neutral book that
  turned out to be unavailable (§1). That judgement may be wrong, and rewriting a gate after
  seeing the result is the exact move §8 exists to prevent.
* **~1.4 independent 10-year windows** behind the strongest evidence (§11n).
* **One bear year in fourteen** (§11i) — the tail is unmeasured *and* unmeasurable here.
* **The holdout is compromised** for drawdown and rolling-window statistics (§11m).
* **Three audits, ~42 defects, no decline in the rate.** The base rate for "the next audit
  finds something material" is high.

### The failure mode has changed

§13 originally assumed the project dies from *no edge*. On the evidence it will more likely
die from one of:

1. **Abandonment.** Month 30 of a 3.3-year losing streak against an index visible daily.
   §11o makes this a **certainty** — 100% of ten-year start months contain a >3-year streak —
   and §11p prices it: **stopping contributions costs 3.7–27.2% of terminal wealth depending
   on when, but selling costs 33–40% whenever it happens.** The mitigation is a written
   never-sell rule plus the 50/50 allocation, not willpower.
2. **A defect not yet found.** Three audits in and still finding load-bearing ones.
3. **The tail arriving.** A 2008- or 2009-style bear-market recovery, which this sample
   cannot price.

None of these is "there was no edge." That is genuine progress, and it is also a warning:
**the remaining risks are the ones a backtest cannot reduce.**

### Where these projects actually die

**Month five.** Novelty gone, six hypotheses dead, nothing working. A scope that fits
real weekly hours is the main thing that gets you through it.

> **Did not happen — for an instructive reason.** The research phase ran ~1 month, not the
> estimated 2–3. What consumed the time was not hypothesis generation but **auditing**: three
> reviews finding ~42 defects, of which the consequential ones each invalidated a published
> result. The plan budgeted for the search and not for the verification, and the verification
> was the larger job. Every worthwhile finding in §11g–§11n came from re-examining a number
> that looked wrong, never from testing another idea.

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

## 14. References

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
