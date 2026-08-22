#!/usr/bin/env python
"""Deployment readiness sprint. DIAGNOSTIC ONLY -- is momentum deployable?

Not "is there a better backtest". Five questions that decide whether real money
should go in:

    P1  rolling stability   -- is the edge stable, or a few lucky periods?
    P2  cost sensitivity    -- how much friction kills it?
    P3  real investor sim   -- Rs 2L + Rs 20k/month, taxes and costs paid
    P4  behavioural risk    -- how long would you be wrong in public?
    P5  position sizing     -- is dilution with NIFTY better than switching?

No factors searched, no parameters tuned, no trial registered, no holdout test.
Full archive (2012-02 .. 2026-08); the 2022+ window was already inspected (§11m).

    python scripts/deployment_readiness.py
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import polars as pl

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from algotrade.backtest import drawdown as dd, engine, metrics  # noqa: E402
from algotrade.core import buffering as buf, costs, features  # noqa: E402
from algotrade.core import strategies as st, universe as uv  # noqa: E402
from algotrade.io import indices  # noqa: E402

import importlib  # noqa: E402

rh = importlib.import_module("scripts.run_hypothesis")

CAPITAL = 200_000.0
MONTHLY = 20_000.0
ENTRY, EXIT, CADENCE = 30, 60, "monthly"
CARD = costs.load_rate_cards(REPO / "config/costs.yaml")["delivery"]
SLIP = costs.load_slippage(REPO / "config/costs.yaml")
STCG, LTCG = 0.20, 0.125
MOM, BLEND, NIFTY = "Momentum", "25/75 Mom+LowVol", "NIFTY"


def head(n, title: str) -> None:
    print(f"\n{'='*108}\n{n}. {title}\n{'='*108}")


def pct(xs, p: float):
    s = sorted(xs)
    return s[min(int(p * len(s)), len(s) - 1)]


# ------------------------------------------------------------- streams -------
print("loading ...", flush=True)
PANEL = rh.load_adjusted_panel()
LIQ = uv.liquidity(PANEL)
MEMBERS = uv.membership(LIQ, exclude=set(uv.DEFAULT_EXCLUSIONS))
BENCH = indices.benchmark_series(REPO / "data/raw/indices")


def weights_for(kind: str, *, long_side: str, **params) -> pl.DataFrame:
    elig = st.restrict_to_universe(features.add(LIQ, kind, **params), MEMBERS)
    return buf.buffered_long_only(
        elig, entry=ENTRY, exit_=EXIT, cadence=CADENCE,
        descending=(long_side == "high"),
    )


def run_book(w: pl.DataFrame, *, slippage=SLIP) -> tuple[pl.DataFrame, float]:
    r = engine.run(weights=w, panel=PANEL, capital=CAPITAL, card=CARD,
                   slippage=slippage, tier="mid",
                   trade_dates=engine.implied_trade_dates(w))
    traded = float(r.blotter["value"].sum())
    bps = (float((r.blotter["charges"] + r.blotter["slippage"]).sum())
           / traded * 10_000) if traded > 0 else 0.0
    # gross_return - net_return is the cost as a fraction of that day's opening
    # capital, which is what the SIP simulation needs to charge at its own scale.
    return r.equity.select(
        pl.col("exit_date").alias("date"),
        pl.col("net_return").alias("r"),
        (pl.col("gross_return") - pl.col("net_return")).alias("cost_r"),
        pl.col("turnover"),
    ).drop_nulls("date").sort("date"), bps


print("building books ...", flush=True)
W_MOM = weights_for("momentum", long_side="high", lookback=252, skip=21)
W_LV = weights_for("realised_vol", long_side="low", lookback=60)
mom_s, MOM_BPS = run_book(W_MOM)
lv_s, LV_BPS = run_book(W_LV)
nifty_s = BENCH.drop_nulls("ret").sort("date").select(
    "date", pl.col("ret").alias("r"), pl.lit(0.0).alias("cost_r"),
    pl.lit(0.0).alias("turnover"),
)


def mix(a: pl.DataFrame, b: pl.DataFrame, wa: float, *, cost_bps: float):
    """Two sleeves drifting between monthly rebalances; the correction charged."""
    j = a.join(b, on="date", how="inner", suffix="_b").sort("date")
    rebal = buf.rebalance_dates(j["date"].to_list(), CADENCE)
    ca, cb, rows = wa, 1 - wa, []
    for d, ra, rb, ca_c, cb_c, ta, tb in zip(
        j["date"].to_list(), j["r"].to_list(), j["r_b"].to_list(),
        j["cost_r"].to_list(), j["cost_r_b"].to_list(),
        j["turnover"].to_list(), j["turnover_b"].to_list(),
    ):
        opening = ca + cb
        sleeve_cost = (ca * ca_c + cb * cb_c) / opening
        sleeve_turn = (ca * ta + cb * tb) / opening
        ca *= 1 + ra
        cb *= 1 + rb
        total = ca + cb
        extra_cost, extra_turn = 0.0, 0.0
        if d in rebal:
            moved = abs(total * wa - ca)
            # Both legs trade: cost_bps was measured per rupee of one-sided
            # trade value, and a rebalance sells one sleeve to buy the other.
            extra_cost = 2 * moved * cost_bps / 10_000
            extra_turn = moved / opening
            total -= extra_cost
            ca, cb = total * wa, total * (1 - wa)
        rows.append({
            "date": d, "r": total / opening - 1,
            "cost_r": sleeve_cost + extra_cost / opening,
            "turnover": sleeve_turn + extra_turn,
        })
    return pl.DataFrame(rows)


STREAMS = {
    MOM: mom_s,
    BLEND: mix(mom_s, lv_s, 0.25, cost_bps=0.25 * MOM_BPS + 0.75 * LV_BPS),
    NIFTY: nifty_s,
}
common = None
for s in STREAMS.values():
    d = s.select("date")
    common = d if common is None else common.join(d, on="date", how="inner")
common = common.sort("date")
S = {n: common.join(s, on="date", how="left").sort("date") for n, s in STREAMS.items()}
for n in S:
    S[n] = S[n].with_columns((100.0 * (1 + pl.col("r")).cum_prod()).alias("w"))

DATES = common["date"].to_list()
SPAN = (DATES[-1] - DATES[0]).days / 365.25
STARTS = sorted(
    pl.DataFrame({"date": DATES}).group_by_dynamic("date", every="1mo")
    .agg(pl.col("date").max().alias("d")).get_column("d").to_list()
)
print(f"  {len(DATES):,} sessions, {DATES[0]} .. {DATES[-1]} ({SPAN:.2f} years)")
print("  DIAGNOSTIC ONLY -- no factors, no tuning, no holdout test, 0 configs")


def slice_between(f: pl.DataFrame, a: date, b: date) -> pl.DataFrame:
    return f.filter(pl.col("date").is_between(a, b))


def window_stats(f: pl.DataFrame, *, deferred: bool) -> dict:
    """Everything P1 asks for, computed on one window."""
    cap = (100.0 * (1 + f["r"]).cum_prod())
    yrs = (f["date"].max() - f["date"].min()).days / 365.25
    total = float(cap[-1]) / 100.0
    cagr = total ** (1 / yrs) - 1
    vol = float(f["r"].std() or 0.0) * (250**0.5)
    at = metrics.after_tax_cagr(
        f.select("date", pl.col("r").alias("net_return")),
        rate=STCG, deferred=deferred, deferred_rate=LTCG,
    )
    return {
        # LONG-ONLY books: the risk-free rate MUST be deducted. rf=0 is for a
        # self-financing long/short spread only (see metrics.compute).
        "cagr": cagr, "after_tax": at,
        "sharpe": (cagr - metrics.RISK_FREE) / vol if vol else 0.0,
        "maxdd": metrics.max_drawdown(cap), "ulcer": dd.ulcer_index(cap),
        "martin": dd.martin_ratio(at, cap, risk_free=metrics.RISK_FREE),
    }


# ============================== P1 ==========================================
head("P1", "ROLLING STABILITY -- is the edge stable, or a few lucky periods?")
P1 = {}
for label, yrs in (("5-year", 5), ("7-year", 7), ("10-year", 10)):
    rows = {n: [] for n in S}
    for s0 in STARTS:
        s1 = s0 + timedelta(days=int(yrs * 365.25))
        if s1 > DATES[-1]:
            continue
        cut = [d for d in DATES if d >= s1]
        s1 = cut[0]
        w = {n: slice_between(S[n], s0, s1) for n in S}
        if any(x.height < 100 for x in w.values()):
            continue
        for n in S:
            st_ = window_stats(w[n], deferred=(n == NIFTY))
            rows[n].append(st_)
    if not rows[MOM]:
        continue
    P1[label] = rows
    n_win = len(rows[MOM])
    print(f"\n--- {label} windows: {n_win} starts (~{SPAN/yrs:.1f} independent) ---")
    for metric, fmt, better_high in (
        ("cagr", "{:.1%}", True), ("after_tax", "{:.1%}", True),
        ("sharpe", "{:.2f}", True),
        # Drawdowns are NEGATIVE, so less-negative is better and higher-is-better
        # is the correct flag. Marking it lower-is-better printed -27.7% as the
        # "worst" 5-year drawdown and -53.1% as the "best".
        ("maxdd", "{:.1%}", True),
        ("ulcer", "{:.1f}", False), ("martin", "{:.2f}", True),
    ):
        print(f"\n  {metric:<12}{'median':>10}{'10th pct':>11}{'worst':>10}{'best':>10}")
        for n in S:
            v = [x[metric] for x in rows[n]]
            worst = min(v) if better_high else max(v)
            best = max(v) if better_high else min(v)
            tenth = pct(v, 0.10) if better_high else pct(v, 0.90)
            print(f"  {n:<12}" + "".join(
                fmt.format(x).rjust(w_) for x, w_ in
                ((pct(v, 0.50), 10), (tenth, 11), (worst, 10), (best, 10))))

    print(f"\n  {'excess vs NIFTY':<22}{'median':>10}{'10th pct':>11}"
          f"{'worst':>10}{'best':>10}{'windows below 0':>18}")
    for n in (MOM, BLEND):
        ex = [a["cagr"] - b["cagr"] for a, b in zip(rows[n], rows[NIFTY])]
        exa = [a["after_tax"] - b["after_tax"] for a, b in zip(rows[n], rows[NIFTY])]
        print(f"  {n + ' (pre-tax)':<22}{pct(ex,0.50):>10.1%}{pct(ex,0.10):>11.1%}"
              f"{min(ex):>10.1%}{max(ex):>10.1%}"
              f"{sum(x <= 0 for x in ex)/len(ex):>18.0%}")
        print(f"  {n + ' (after tax)':<22}{pct(exa,0.50):>10.1%}"
              f"{pct(exa,0.10):>11.1%}{min(exa):>10.1%}{max(exa):>10.1%}"
              f"{sum(x <= 0 for x in exa)/len(exa):>18.0%}")


# ============================== P2 ==========================================
head("P2", "COST SENSITIVITY -- at what friction does the edge disappear?")
print("Extra bps are added PER SIDE (a round trip costs twice the figure shown),")
print("by widening the spread assumption and re-running the engine, so trade")
print("sizing and compounding respond properly rather than being netted off.\n")
n_at = window_stats(S[NIFTY], deferred=True)
print(f"{'friction':<18}{'CAGR':>9}{'after-tax':>11}{'Sharpe':>9}"
      f"{'vs NIFTY':>11}{'after-tax vs NIFTY':>20}{'realised bps':>14}")
print(f"{'NIFTY baseline':<18}{n_at['cagr']:>9.1%}{n_at['after_tax']:>11.1%}"
      f"{n_at['sharpe']:>9.2f}{'--':>11}{'--':>20}{'--':>14}")
P2 = []
for extra in (0, 25, 50, 75, 100, 150):
    model = costs.SlippageModel(
        spread_bps_by_tier={k: v + 2 * extra
                            for k, v in SLIP.spread_bps_by_tier.items()},
        impact_coefficient=SLIP.impact_coefficient,
        max_participation=SLIP.max_participation,
    )
    s_, bps = run_book(W_MOM, slippage=model)
    f = common.join(s_, on="date", how="left").sort("date")
    stx = window_stats(f, deferred=False)
    P2.append((extra, stx, bps))
    label = "current model" if extra == 0 else f"+{extra} bps/side"
    print(f"{label:<18}{stx['cagr']:>9.1%}{stx['after_tax']:>11.1%}"
          f"{stx['sharpe']:>9.2f}{stx['cagr']-n_at['cagr']:>+11.1%}"
          f"{stx['after_tax']-n_at['after_tax']:>+20.1%}{bps:>14.1f}")

base = P2[0][1]
gone = [e for e, s_, _ in P2 if s_["after_tax"] <= n_at["after_tax"]]
print(f"\nafter-tax edge vs NIFTY is gone at: "
      f"{('+' + str(gone[0]) + ' bps/side' if gone else 'NONE of the levels tested')}")
print(f"cost of +150 bps/side vs current: "
      f"{base['after_tax'] - P2[-1][1]['after_tax']:.1%} of after-tax CAGR")


# ============================== P3 ==========================================
head("P3", "REAL INVESTOR SIMULATION -- Rs 2L initial + Rs 20k/month")
print("Taxes paid annually out of the account (STCG 20%, losses carried forward);")
print("NIFTY defers to a single 12.5% LTCG event on exit. Costs are charged at the")
print("simulated capital, which is CONSERVATIVE: the flat Rs 15.34 DP fee is a")
print("shrinking share as the account grows, so real costs would be lower.\n")

MONTH_ENDS = set(STARTS)


def simulate(name: str, start: date, end: date) -> dict:
    f = slice_between(S[name], start, end)
    if f.height < 100:
        return {}
    cap = CAPITAL
    contributed = CAPITAL
    tax_paid = cost_paid = 0.0
    carried = 0.0
    year = f["date"][0].year
    year_start_cap, year_contrib = cap, 0.0
    curve, contrib_curve = [], []
    for d, r, cr in zip(f["date"].to_list(), f["r"].to_list(),
                        f["cost_r"].to_list()):
        cost_paid += cap * cr
        cap *= 1 + r
        if d in MONTH_ENDS:
            cap += MONTHLY
            contributed += MONTHLY
            year_contrib += MONTHLY
        if d.year != year:
            if name != NIFTY:
                gain = cap - year_start_cap - year_contrib
                if gain > 0:
                    taxable = max(0.0, gain - carried)
                    carried = max(0.0, carried - gain)
                    cap -= taxable * STCG
                    tax_paid += taxable * STCG
                else:
                    carried += -gain
            year, year_start_cap, year_contrib = d.year, cap, 0.0
        curve.append(cap)
        contrib_curve.append(contributed)
    if name == NIFTY:
        gain = cap - contributed
        if gain > 0:
            tax_paid = gain * LTCG
            cap -= tax_paid
    ser = pl.Series(curve)
    return {
        "final": cap, "contributed": contributed, "tax": tax_paid,
        "cost": cost_paid, "n_months": int((contributed - CAPITAL) / MONTHLY) + 1,
        "maxdd": metrics.max_drawdown(ser),
        # What the investor sees: worst shortfall of the account against the
        # money actually put in. A -50% drawdown early, on a small balance,
        # is a different experience from the same figure late.
        "vs_contributed": min(c / k - 1 for c, k in zip(curve, contrib_curve)),
        "years": (f["date"].max() - f["date"].min()).days / 365.25,
    }


def xirr(months: int, initial: float, monthly: float, value: float) -> float:
    lo, hi = -0.95, 3.0
    for _ in range(160):
        mid = (lo + hi) / 2
        m = (1 + mid) ** (1 / 12) - 1
        fv = initial * (1 + m) ** months + sum(
            monthly * (1 + m) ** (months - 1 - i) for i in range(months))
        if fv < value:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


for label, yrs in (("5-year", 5), ("10-year", 10)):
    runs = {n: [] for n in S}
    for s0 in STARTS:
        s1 = s0 + timedelta(days=int(yrs * 365.25))
        if s1 > DATES[-1]:
            continue
        got = {n: simulate(n, s0, s1) for n in S}
        if any(not g for g in got.values()):
            continue
        for n in S:
            runs[n].append(got[n])
    if not runs[MOM]:
        continue
    print(f"--- {label} horizon, {len(runs[MOM])} start months ---")
    inv = runs[MOM][0]["contributed"]
    print(f"  invested: Rs {inv:,.0f}  (Rs {CAPITAL:,.0f} initial + "
          f"{runs[MOM][0]['n_months'] - 1} x Rs {MONTHLY:,.0f})")
    print(f"\n{'strategy':<18}{'median':>13}{'worst':>13}{'best':>13}"
          f"{'med XIRR':>10}{'worst XIRR':>12}{'P(beat NIFTY)':>15}")
    for n in S:
        fv = [x["final"] for x in runs[n]]
        nb = [x["final"] for x in runs[NIFTY]]
        m = runs[n][0]["n_months"] - 1
        print(f"{n:<18}{pct(fv,0.50):>13,.0f}{min(fv):>13,.0f}{max(fv):>13,.0f}"
              f"{xirr(m, CAPITAL, MONTHLY, pct(fv,0.50)):>10.1%}"
              f"{xirr(m, CAPITAL, MONTHLY, min(fv)):>12.1%}"
              f"{('--' if n == NIFTY else f'{sum(a>b for a,b in zip(fv,nb))/len(fv):.0%}'):>15}")
    print(f"\n{'strategy':<18}{'med tax paid':>14}{'med costs':>12}"
          f"{'costs % of final':>18}{'max DD of a/c':>15}{'worst vs invested':>19}")
    for n in S:
        tx = [x["tax"] for x in runs[n]]
        cs = [x["cost"] for x in runs[n]]
        ddv = [x["maxdd"] for x in runs[n]]
        vc = [x["vs_contributed"] for x in runs[n]]
        fv = [x["final"] for x in runs[n]]
        print(f"{n:<18}{pct(tx,0.50):>14,.0f}{pct(cs,0.50):>12,.0f}"
              f"{pct(cs,0.50)/pct(fv,0.50):>18.1%}{min(ddv):>15.1%}"
              f"{min(vc):>19.1%}")
    print()


# ============================== P4 ==========================================
head("P4", "BEHAVIOURAL FAILURE RISK -- how long would you be wrong in public?")
rel = pl.DataFrame({
    "date": DATES,
    "capital": [a / b for a, b in zip(S[MOM]["w"].to_list(), S[NIFTY]["w"].to_list())],
})
eps = [e for e in dd.episodes(rel) if abs(e.depth) >= 0.01]
durs = []
for e in eps:
    d_ = ((e.recovery - e.start).days if e.recovered else (DATES[-1] - e.start).days)
    durs.append(d_)
print(f"{len(eps)} underperformance episodes (>=1% shortfall vs NIFTY)\n")
print(f"{'statistic':<28}{'calendar days':>15}{'months':>9}")
for lab, v in (("median streak", pct(durs, 0.50)),
               ("mean streak", sum(durs) / len(durs)),
               ("90th percentile streak", pct(durs, 0.90)),
               ("longest streak", max(durs))):
    print(f"{lab:<28}{v:>15,.0f}{v/30.44:>9.1f}")

print(f"\n{'every episode > 6 months':<28}")
print(f"{'from':<12}{'trough':<12}{'to':<12}{'days':>7}{'months':>8}"
      f"{'rel drawdown':>14}{'cum. shortfall':>16}")
for e in sorted(eps, key=lambda x: -((x.recovery - x.start).days if x.recovered
                                     else (DATES[-1] - x.start).days)):
    d_ = ((e.recovery - e.start).days if e.recovered else (DATES[-1] - e.start).days)
    if d_ < 182:
        continue
    i0, i1 = DATES.index(e.start), DATES.index(e.trough)
    cum = (S[MOM]["w"][i1] / S[MOM]["w"][i0] - 1) - (
        S[NIFTY]["w"][i1] / S[NIFTY]["w"][i0] - 1)
    print(f"{str(e.start):<12}{str(e.trough):<12}"
          f"{(str(e.recovery) if e.recovered else 'ONGOING'):<12}{d_:>7}"
          f"{d_/30.44:>8.1f}{e.depth:>14.1%}{cum:>16.1%}")

# Probability an investor ENCOUNTERS a long streak during their holding period.
# The relevant question is not "what fraction of episodes are long" but "if I
# start today and hold ten years, do I hit one".
print(f"\nProbability of LIVING THROUGH a streak of at least N years,")
print(f"from a random month-end start:\n")
print(f"{'holding period':<18}{'>1 year':>10}{'>2 years':>11}{'>3 years':>11}"
      f"{'starts tested':>15}")
for hold_y in (3, 5, 10):
    hits = {1: 0, 2: 0, 3: 0}
    n_start = 0
    for s0 in STARTS:
        s1 = s0 + timedelta(days=int(hold_y * 365.25))
        if s1 > DATES[-1]:
            continue
        n_start += 1
        # Longest streak whose underwater stretch overlaps the holding window.
        longest = 0
        for e in eps:
            e_end = e.recovery if e.recovered else DATES[-1]
            if e_end < s0 or e.start > s1:
                continue
            overlap = (min(e_end, s1) - max(e.start, s0)).days
            longest = max(longest, overlap)
        for k in hits:
            if longest > k * 365:
                hits[k] += 1
    if n_start:
        print(f"{f'{hold_y} years':<18}{hits[1]/n_start:>10.0%}"
              f"{hits[2]/n_start:>11.0%}{hits[3]/n_start:>11.0%}{n_start:>15}")


# ============================== P5 ==========================================
head("P5", "POSITION SIZING -- momentum diluted with NIFTY, monthly rebalance")
print("The dilution is charged: the monthly correction trades at the blended")
print("rate. NIFTY is treated as an ETF sleeve with no internal turnover.\n")
print(f"{'allocation':<22}{'CAGR':>8}{'after-tax':>11}{'Sharpe':>8}{'ulcer':>7}"
      f"{'Martin(AT)':>12}{'max DD':>9}{'longest DD':>12}{'longest u/p':>13}")
P5 = {}
for wa in (1.00, 0.75, 0.50, 0.25, 0.00):
    if wa == 1.0:
        f = S[MOM]
    elif wa == 0.0:
        f = S[NIFTY]
    else:
        f = mix(S[MOM].select("date", "r", "cost_r", "turnover"),
                S[NIFTY].select("date", "r", "cost_r", "turnover"),
                wa, cost_bps=wa * MOM_BPS + (1 - wa) * 30.0)
        f = common.join(f, on="date", how="left").sort("date")
    cap = (100.0 * (1 + f["r"]).cum_prod())
    frame = f.select("date").with_columns(cap.alias("capital"))
    stx = window_stats(f, deferred=(wa == 0.0))
    e_all = [e for e in dd.episodes(frame) if abs(e.depth) >= 0.05]
    longest_dd = max(
        [(e.recovery - e.start).days if e.recovered else (DATES[-1] - e.start).days
         for e in e_all] or [0])
    r_ = pl.DataFrame({"date": DATES,
                       "capital": [a / b for a, b in
                                   zip(cap.to_list(), S[NIFTY]["w"].to_list())]})
    r_eps = [e for e in dd.episodes(r_) if abs(e.depth) >= 0.01]
    longest_up = max(
        [(e.recovery - e.start).days if e.recovered else (DATES[-1] - e.start).days
         for e in r_eps] or [0])
    P5[wa] = {**stx, "longest_dd": longest_dd, "longest_up": longest_up}
    label = f"{wa:.0%} mom / {1-wa:.0%} NIFTY"
    print(f"{label:<22}{stx['cagr']:>8.1%}{stx['after_tax']:>11.1%}"
          f"{stx['sharpe']:>8.2f}{stx['ulcer']:>7.1f}{stx['martin']:>12.2f}"
          f"{stx['maxdd']:>9.1%}{longest_dd:>12,}{longest_up:>13,}")

print(f"\n{'allocation':<22}{'after-tax CAGR':>16}{'per unit ulcer':>16}"
      f"{'per unit maxDD':>16}{'excess vs NIFTY':>17}")
n0 = P5[0.00]
for wa in (1.00, 0.75, 0.50, 0.25, 0.00):
    s_ = P5[wa]
    print(f"{f'{wa:.0%} mom / {1-wa:.0%} NIFTY':<22}{s_['after_tax']:>16.1%}"
          f"{s_['after_tax']/(s_['ulcer']/100):>16.2f}"
          f"{s_['after_tax']/abs(s_['maxdd']):>16.2f}"
          f"{s_['after_tax']-n0['after_tax']:>+17.1%}")

with (REPO / "config" / "trials.jsonl").open("a", encoding="utf-8") as fh:
    fh.write(json.dumps({
        "kind": "exploration",
        "note": "deployment readiness sprint: rolling stability, cost "
                "sensitivity, SIP simulation, behavioural risk, position "
                "sizing. Existing configs only, no search.",
        "configs_explored": 0, "registered_as_trials": 0,
        "touched_holdout": True,
    }, sort_keys=True) + "\n")
print("\n0 new configs; diagnostic only, denominator unchanged")
