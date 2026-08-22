#!/usr/bin/env python
"""What did it cost to quit at the moment quitting felt most rational?

DIAGNOSTIC ONLY. No factors, no tuning, no holdout test, 0 configs.

§11o established that a >3-year underperformance streak against NIFTY is a
CERTAINTY over any ten-year hold, and that every favourable number depends on
the monthly contribution still arriving in month 30 of it. This prices that
assumption.

Four behaviours, triggered after 12 / 24 / 36 months of continuous
underperformance. Stopping contributions is the mildest thing a real investor
does, so the harsher ones are modelled too:

    continue     always contribute                             (the baseline)
    pause        stop while the streak runs, resume when it ends
    stop         stop contributing permanently, keep holding
    switch       redirect all FUTURE contributions to NIFTY
    capitulate   SELL the momentum book into NIFTY and contribute there

`capitulate` pays 20% STCG on the realised gain at the moment of switching --
which is part of the cost of panicking and is usually left out of this kind of
comparison.

Underperformance is measured exactly as §11o: the strategy is "in a streak" for
as long as it sits below its best-ever wealth ratio against NIFTY. The trigger
fires when the CURRENT streak has run N months, which is the information the
investor actually has at the time -- not knowing how much longer it will last is
the whole point.

    python scripts/cost_of_quitting.py
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import polars as pl

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from algotrade.backtest import drawdown as dd, engine  # noqa: E402
from algotrade.core import buffering as buf, costs, features  # noqa: E402
from algotrade.core import strategies as st, universe as uv  # noqa: E402
from algotrade.io import indices  # noqa: E402

import importlib  # noqa: E402

rh = importlib.import_module("scripts.run_hypothesis")

CAPITAL, MONTHLY = 200_000.0, 20_000.0
ENTRY, EXIT, CADENCE = 30, 60, "monthly"
CARD = costs.load_rate_cards(REPO / "config/costs.yaml")["delivery"]
SLIP = costs.load_slippage(REPO / "config/costs.yaml")
STCG, LTCG = 0.20, 0.125
# A quitter who stops contributing still HAS the money. Counting the skipped
# contributions as destroyed wealth would conflate "not invested" with "lost"
# and roughly doubled the apparent cost of stopping. Un-contributed cash is
# accrued at the project's risk-free rate (§ metrics.RISK_FREE) and added back.
CASH_RATE = 0.065
POLICIES = ("continue", "pause", "stop", "switch", "capitulate")


def pct(xs, p):
    s = sorted(xs)
    return s[min(int(p * len(s)), len(s) - 1)]


# ------------------------------------------------------------- streams -------
print("loading ...", flush=True)
PANEL = rh.load_adjusted_panel()
LIQ = uv.liquidity(PANEL)
MEMBERS = uv.membership(LIQ, exclude=set(uv.DEFAULT_EXCLUSIONS))
BENCH = indices.benchmark_series(REPO / "data/raw/indices")

elig = st.restrict_to_universe(
    features.add(LIQ, "momentum", lookback=252, skip=21), MEMBERS
)
w = buf.buffered_long_only(elig, entry=ENTRY, exit_=EXIT, cadence=CADENCE)
res = engine.run(weights=w, panel=PANEL, capital=CAPITAL, card=CARD,
                 slippage=SLIP, tier="mid",
                 trade_dates=engine.implied_trade_dates(w))
mom = res.equity.select(
    pl.col("exit_date").alias("date"), pl.col("net_return").alias("rm")
).drop_nulls("date").sort("date")
nif = BENCH.drop_nulls("ret").sort("date").select("date", pl.col("ret").alias("rn"))
J = mom.join(nif, on="date", how="inner").sort("date")

DATES = J["date"].to_list()
RM, RN = J["rm"].to_list(), J["rn"].to_list()
IDX = {d: i for i, d in enumerate(DATES)}
STARTS = sorted(
    pl.DataFrame({"date": DATES}).group_by_dynamic("date", every="1mo")
    .agg(pl.col("date").max().alias("d")).get_column("d").to_list()
)
MONTH_ENDS = set(STARTS)
SESSIONS_PER_YEAR = len(DATES) / ((DATES[-1] - DATES[0]).days / 365.25)
print(f"  {len(DATES):,} sessions, {DATES[0]} .. {DATES[-1]}")
print("  DIAGNOSTIC ONLY -- 0 configs, no tuning, no holdout test")

# --------------------------------------------------- underperformance state --
# Days elapsed in the streak the investor is CURRENTLY inside, per session.
wm, wn = [100.0], [100.0]
for i in range(1, len(DATES)):
    wm.append(wm[-1] * (1 + RM[i]))
    wn.append(wn[-1] * (1 + RN[i]))
REL = pl.DataFrame({"date": DATES, "capital": [a / b for a, b in zip(wm, wn)]})
EPISODES = [e for e in dd.episodes(REL) if abs(e.depth) >= 0.01]

STREAK_DAYS = [0] * len(DATES)
STREAK_ID: list[date | None] = [None] * len(DATES)
for e in EPISODES:
    end = e.recovery if e.recovered else DATES[-1]
    i0 = IDX[e.start]
    for i in range(i0, IDX[end] + 1):
        STREAK_DAYS[i] = (DATES[i] - e.start).days
        STREAK_ID[i] = e.start


# ------------------------------------------------------------ simulator ------
def simulate(start: date, end: date, policy: str, trigger_months: int,
             only_streak: date | None = None,
             trigger_on: date | None = None) -> dict:
    """One investor, one policy. Returns terminal wealth and what they saw.

    Args:
        only_streak: Restrict the trigger to the streak beginning on this date.
            Section A needs it: an investor who starts three years before a
            target streak usually sits inside an EARLIER one, so without this the
            trigger fires on the wrong episode -- the 2016 streak's "quit"
            actually fired in 2013, three years early, and the table measured
            something other than its own heading.
    """
    i0, i1 = IDX[start], IDX[end]
    trigger_days = trigger_months * 30.44
    cap_m, cap_n = CAPITAL, 0.0
    cash = 0.0               # contributions the quitter kept instead of investing
    basis_n = 0.0            # cost basis of the NIFTY sleeve, for terminal LTCG
    contributed = CAPITAL
    tax_paid = 0.0
    carried = 0.0
    fired: date | None = None
    first_fired: date | None = None   # `pause` resets `fired`; this does not.
    behind_at_trigger = None
    year = DATES[i0].year
    ystart, ycontrib = cap_m, 0.0

    # Sessions per year measured from the archive, not assumed to be 250 --
    # it is 239.9, so a 1/250 exponent accrued cash at 6.23% rather than 6.5%
    # and made quitting look marginally worse than it was.
    daily_cash = (1 + CASH_RATE) ** (1 / SESSIONS_PER_YEAR) - 1
    for i in range(i0 + 1, i1 + 1):
        d = DATES[i]
        cap_m *= 1 + RM[i]
        cap_n *= 1 + RN[i]
        cash *= 1 + daily_cash

        # Has the investor's patience run out? Only ever fires once.
        # Two DIFFERENT questions, previously conflated into one flag:
        #   should_fire  -- has patience run out (a one-time event)?
        #   still_behind -- is the book still below its best standing vs NIFTY?
        # `pause` resumes on the second, not the first. Using the trigger
        # condition for both meant pause never resumed in the trough case and
        # produced results identical to stopping forever.
        still_behind = STREAK_ID[i] is not None
        if trigger_on is not None:
            should_fire = d >= trigger_on
        else:
            should_fire = STREAK_DAYS[i] >= trigger_days and (
                only_streak is None or STREAK_ID[i] == only_streak
            )
        if policy != "continue" and fired is None and should_fire:
            fired = d
            first_fired = first_fired or d
            behind_at_trigger = REL["capital"][i] / REL["capital"][IDX[
                max((e.start for e in EPISODES if e.start <= d), default=DATES[i0])
            ]] - 1
            if policy == "capitulate":
                # Sell the book. Only the gain NOT ALREADY TAXED is realised.
                # Using lifetime contributions as the basis double-taxes: the
                # annual mark-to-market has already taxed every prior year's
                # gain out of cap_m, stepping the basis up to the post-tax
                # value. On a +50% year followed by a mid-year sale that
                # charged 36% on a 20% gain and overstated the cost of
                # capitulating by up to 6.3pp of terminal wealth.
                gain = cap_m - ystart - ycontrib
                if gain > 0:
                    tx = gain * STCG
                    tax_paid += tx
                    cap_m -= tx
                ystart, ycontrib = 0.0, 0.0
                basis_n += cap_m
                cap_n += cap_m
                cap_m = 0.0

        if d in MONTH_ENDS:
            resumed = policy == "pause" and fired is not None and not still_behind
            if policy == "continue" or fired is None or resumed:
                cap_m += MONTHLY
                contributed += MONTHLY
                ycontrib += MONTHLY
                if resumed:
                    fired = None          # patience restored until it goes again
            elif policy in ("switch", "capitulate"):
                cap_n += MONTHLY
                basis_n += MONTHLY
                contributed += MONTHLY
            else:
                # 'stop' and a paused 'pause' invest nothing -- the money is kept.
                cash += MONTHLY

        if d.year != year:
            gain = cap_m - ystart - ycontrib
            if gain > 0:
                taxable = max(0.0, gain - carried)
                carried = max(0.0, carried - gain)
                cap_m -= taxable * STCG
                tax_paid += taxable * STCG
            else:
                carried += -gain
            year, ystart, ycontrib = d.year, cap_m, 0.0

    # Tax the final PARTIAL year. Tax only fired on a year boundary, so a
    # window ending mid-year handed the investor its last months untaxed --
    # worth a mean 1.48% of terminal wealth, and asymmetric because the NIFTY
    # sleeve pays LTCG on its entire gain including the stub.
    stub = cap_m - ystart - ycontrib
    if stub > 0:
        taxable = max(0.0, stub - carried)
        cap_m -= taxable * STCG
        tax_paid += taxable * STCG

    if cap_n > 0:                       # one LTCG event on the index sleeve
        gain = cap_n - basis_n
        if gain > 0:
            cap_n -= gain * LTCG
            tax_paid += gain * LTCG

    return {
        "final": cap_m + cap_n + cash, "invested_only": cap_m + cap_n,
        "cash": cash, "contributed": contributed, "tax": tax_paid,
        "fired": first_fired, "behind": behind_at_trigger,
    }


def head(n, title):
    print(f"\n{'='*104}\n{n}. {title}\n{'='*104}")


# ============ A. PER STREAK =================================================
head("A", "EVERY LONG STREAK -- what quitting inside IT cost")
print("Investor starts contributing 3 years before the streak begins (so there is")
print("a real balance to abandon), quits N months in, and is measured at the")
print("archive end. 'Continue' is the same investor who kept going.\n")

LONG = sorted(
    [e for e in EPISODES
     if ((e.recovery - e.start).days if e.recovered
         else (DATES[-1] - e.start).days) >= 365],
    key=lambda e: e.start,
)
END = DATES[-1]
rows_a = []
for e in LONG:
    dur = ((e.recovery - e.start).days if e.recovered else (END - e.start).days)
    pre = [d for d in STARTS if d <= e.start - timedelta(days=3 * 365)]
    if not pre:
        continue
    s0 = pre[-1]
    base = simulate(s0, END, "continue", 12, only_streak=e.start)
    print(f"--- streak {e.start} -> "
          f"{e.recovery if e.recovered else 'ONGOING'}  "
          f"({dur} days, {dur/30.44:.1f} months, {e.depth:.1%} relative) ---")
    print(f"    investor starts {s0}; measured at {END}; "
          f"CONTINUE ends at Rs {base['final']:,.0f}")
    print(f"    {'quit after':<14}{'policy':<13}{'final':>14}{'of which cash':>15}"
          f"{'shortfall':>13}{'% of wealth':>13}{'triggered':>12}")
    for months in (12, 24, 36):
        if dur < months * 30.44:
            print(f"    {f'{months} months':<14}{'--':<13}"
                  f"{'streak ended first':>14}")
            continue
        for policy in ("pause", "stop", "switch", "capitulate"):
            r = simulate(s0, END, policy, months, only_streak=e.start)
            loss = r["final"] - base["final"]
            rows_a.append({
                "streak": str(e.start), "months": months, "policy": policy,
                "loss": loss, "rel": loss / base["final"],
            })
            print(f"    {f'{months} months':<14}{policy:<13}"
                  f"{r['final']:>14,.0f}{r['cash']:>15,.0f}{loss:>13,.0f}"
                  f"{loss/base['final']:>13.1%}"
                  f"{str(r['fired'] or '--'):>12}")
    print()


# ============ B. ACROSS ALL STARTS ==========================================
head("B", "ACROSS EVERY START MONTH -- the expected cost of quitting")
print("10-year holding period from every month end with a full window.\n")
for hold_y in (10,):
    base_all, out = [], {(p, m): [] for p in POLICIES[1:] for m in (12, 24, 36)}
    fired_count = {m: 0 for m in (12, 24, 36)}
    n_start = 0
    for s0 in STARTS:
        s1 = s0 + timedelta(days=int(hold_y * 365.25))
        if s1 > END:
            continue
        e1 = [d for d in DATES if d >= s1][0]
        b = simulate(s0, e1, "continue", 12)
        base_all.append(b["final"])
        n_start += 1
        for m in (12, 24, 36):
            for p in POLICIES[1:]:
                r = simulate(s0, e1, p, m)
                out[(p, m)].append(r["final"])
                if p == "stop" and r["fired"]:
                    fired_count[m] += 1

    print(f"{hold_y}-year hold, {n_start} start months, "
          f"Rs {CAPITAL + MONTHLY*hold_y*12:,.0f} contributed if never stopped")
    print(f"\n{'trigger':<12}{'% of investors it fires for':>30}")
    for m in (12, 24, 36):
        print(f"{f'{m} months':<12}{fired_count[m]/n_start:>30.0%}")

    print(f"\n{'policy':<13}{'trigger':<11}{'median final':>15}"
          f"{'median loss':>14}{'% of wealth':>13}{'worst loss':>14}"
          f"{'% of starts hurt':>18}")
    med_base = pct(base_all, 0.50)
    print(f"{'continue':<13}{'--':<11}{med_base:>15,.0f}{'--':>14}"
          f"{'--':>13}{'--':>14}{'--':>18}")
    for m in (12, 24, 36):
        for p in POLICIES[1:]:
            v = out[(p, m)]
            losses = [a - b for a, b in zip(v, base_all)]
            print(f"{p:<13}{f'{m} months':<11}{pct(v,0.50):>15,.0f}"
                  f"{pct(losses,0.50):>14,.0f}"
                  f"{pct(losses,0.50)/med_base:>13.1%}"
                  f"{min(losses):>14,.0f}"
                  f"{sum(x < -1 for x in losses)/len(losses):>18.0%}")
        print()


# ============ C. WHAT THEY SAW ==============================================
head("C", "WHAT IT LOOKED LIKE AT THE MOMENT OF QUITTING")
print("For the five long streaks: how far behind the index the strategy was at")
print("each trigger point, and what happened next.\n")
print(f"{'streak start':<14}{'at +12mo':>11}{'at +24mo':>11}{'at +36mo':>11}"
      f"{'trough':>10}{'recovered by':>14}{'streak length':>15}")
for e in LONG:
    end = e.recovery if e.recovered else END
    dur = (end - e.start).days
    i0 = IDX[e.start]
    cells = []
    for months in (12, 24, 36):
        target = e.start + timedelta(days=int(months * 30.44))
        if target > end:
            cells.append("     ended")
            continue
        i = IDX[[d for d in DATES if d >= target][0]]
        cells.append(f"{REL['capital'][i]/REL['capital'][i0] - 1:>10.1%}")
    print(f"{str(e.start):<14}" + "".join(f"{c:>11}" for c in cells)
          + f"{e.depth:>10.1%}"
          + f"{(str(e.recovery) if e.recovered else 'ONGOING'):>14}"
          + f"{f'{dur/30.44:.1f} mo':>15}")

# ============ D. QUITTING AT THE TROUGH =====================================
head("D", "QUITTING AT THE MOMENT OF MAXIMUM PAIN")
print("Sections A and B trigger on elapsed time. But the point where quitting")
print("FEELS most rational is not 12 or 24 months in -- it is the trough, when")
print("the shortfall against the index is at its worst and there is no visible")
print("reason to expect it to stop. This fires exactly there, with perfect")
print("timing that no real investor could have, which is the point: it is the")
print("worst-case behavioural outcome.\n")
print(f"{'streak':<13}{'trough':<13}{'behind by':>11}{'months in':>12}  "
      f"{'policy':<13}{'final':>14}{'shortfall':>13}{'% of wealth':>13}")
rows_d = []
for e in LONG:
    pre = [d for d in STARTS if d <= e.start - timedelta(days=3 * 365)]
    if not pre:
        continue
    s0 = pre[-1]
    base = simulate(s0, END, "continue", 12)
    months_in = (e.trough - e.start).days / 30.44
    first = True
    for policy in ("pause", "stop", "switch", "capitulate"):
        r = simulate(s0, END, policy, 0, trigger_on=e.trough)
        loss = r["final"] - base["final"]
        rows_d.append(loss / base["final"])
        lead = (f"{str(e.start):<13}{str(e.trough):<13}{e.depth:>11.1%}"
                f"{months_in:>12.1f}  ") if first else f"{'':<51}"
        print(f"{lead}{policy:<13}{r['final']:>14,.0f}"
              f"{loss:>13,.0f}{loss/base['final']:>13.1%}")
        first = False
    print()
print(f"median cost of quitting at the trough, across all policies and streaks: "
      f"{pct(rows_d, 0.50):.1%} of terminal wealth")
print(f"worst: {min(rows_d):.1%}")

with (REPO / "config" / "trials.jsonl").open("a", encoding="utf-8") as fh:
    fh.write(json.dumps({
        "kind": "exploration",
        "note": "cost-of-quitting simulation: behavioural failure priced across "
                "every underperformance streak. Existing config only.",
        "configs_explored": 0, "registered_as_trials": 0,
        "touched_holdout": True,
    }, sort_keys=True) + "\n")
print("\n0 new configs; diagnostic only, denominator unchanged")
