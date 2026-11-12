#!/usr/bin/env python
"""Swing-strategy sweep (research only) — 8 strategies + EMA-crossover benchmark.

Same harness, universe, window, capital and costs as research/swing-ema-crossover:
- Universe: repo liquid-300 (top 300 by trailing 20-session median turnover,
  >= Rs 5 cr/day, coverage >= 0.80, monthly rebalance, EQ only, ex-ETF).
- Data: NSE bhavdata data/sessions, 20 Sep 2024 – 11 Nov 2026 (526 sessions).
  Closes UNADJUSTED for splits/bonus. Overnight gap > 20% = corporate-action
  break: indicators restart, open trade closed at last clean close, flagged.
- Fill: signal on close of D, trade at open of D+1 (no look-ahead).
  Equal weight across open positions, cash when flat. Capital Rs 2,00,000,
  delivery charges + mid-tier slippage from config/costs.yaml, min trade Rs 500.
- In-sample Oct 2024-Dec 2025 | out-of-sample Jan-Nov 2026.
- Gates: max drawdown <= 20%, OOS Sharpe >= 0.8.

Strategies (exact rules documented in STRATEGIES below; every stop/target/max-hold
is an assumption I made to make a vague source rule testable — it is NOT
presented as the source's rule). OHLCV is used: high/low from HIGH/LOW_PRICE,
volume from TTL_TRD_QNTY. NIFTY regime uses NIFTYBEES (NIFTY50 ETF) close vs
its SMA200 as a proxy for NIFTY50 close vs SMA200, because the index itself
is not in the bhavdata panel.
"""
from __future__ import annotations
import glob, json, os, sys
from collections import defaultdict
from datetime import date
from pathlib import Path
import polars as pl

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
from algotrade.backtest import engine, metrics
from algotrade.core import costs, universe as uv

CAPITAL = float(os.environ.get("SWING_CAPITAL", "200000"))
SPLIT = date(2026, 1, 1)

def load_panel() -> pl.DataFrame:
    files = sorted(glob.glob(str(REPO / "data/sessions" / "*" / "*.csv")))
    print(f"loading {len(files)} bhavdata files ...", flush=True)
    frames = []
    for f in files:
        df = pl.read_csv(f, schema_overrides={"DATE1": pl.String})
        df = df.rename({c: c.strip() for c in df.columns})
        frames.append(df.select([
            pl.col("SYMBOL").str.strip_chars(),
            pl.col("SERIES").str.strip_chars(),
            pl.col("DATE1").str.strip_chars(),
            pl.col("OPEN_PRICE").str.strip_chars().cast(pl.Float64).alias("open"),
            pl.col("HIGH_PRICE").str.strip_chars().cast(pl.Float64).alias("high"),
            pl.col("LOW_PRICE").str.strip_chars().cast(pl.Float64).alias("low"),
            pl.col("CLOSE_PRICE").str.strip_chars().cast(pl.Float64).alias("close"),
            pl.col("TTL_TRD_QNTY").str.strip_chars().cast(pl.Float64).alias("volume"),
            (pl.col("TURNOVER_LACS").str.strip_chars().cast(pl.Float64) * 1e5).alias("turnover"),
        ]))
    raw = pl.concat(frames, how="vertical")
    panel = (
        raw.filter(pl.col("SERIES") == "EQ")
        .select(
            pl.col("DATE1").str.to_date("%d-%b-%Y").alias("date"),
            pl.col("SYMBOL").alias("symbol"),
            pl.col("open"), pl.col("high"), pl.col("low"), pl.col("close"),
            pl.col("volume"), pl.col("turnover"),
        )
        .filter(pl.col("open") > 0, pl.col("close") > 0, pl.col("high") > 0,
                pl.col("low") > 0, pl.col("volume") > 0)
        .unique(subset=["date", "symbol"], keep="first", maintain_order=True)
        .sort(["symbol", "date"])
    )
    panel = uv.with_session_index(panel)
    print(f"panel: {panel.height:,} rows, {panel['date'].min()} .. {panel['date'].max()}, "
          f"{panel['symbol'].n_unique():,} EQ symbols", flush=True)
    return panel

def add_indicators(panel: pl.DataFrame) -> pl.DataFrame:
    df = panel.sort(["symbol", "session_idx"]).with_columns(
        ((pl.col("open") / pl.col("close").shift(1).over("symbol") - 1).abs() > 0.20)
        .fill_null(False).alias("gap"))
    df = df.with_columns(pl.col("gap").cast(pl.Int32).cum_sum().over("symbol").alias("seg"))
    keys = ["symbol", "seg"]
    chg = pl.col("close") - pl.col("close").shift(1).over(keys)
    gain = chg.clip(lower_bound=0); loss = (-chg).clip(lower_bound=0)
    df = df.with_columns(
        pl.col("close").ewm_mean(alpha=2/21, adjust=False, min_samples=20).over(keys).alias("ema20"),
        pl.col("close").ewm_mean(alpha=2/51, adjust=False, min_samples=50).over(keys).alias("ema50"),
        pl.col("close").ewm_mean(alpha=2/201, adjust=False, min_samples=100).over(keys).alias("ema200"),
        pl.col("close").rolling_mean(20, min_samples=20).over(keys).alias("sma20"),
        pl.col("close").rolling_mean(50, min_samples=50).over(keys).alias("sma50"),
        pl.col("close").rolling_mean(200, min_samples=100).over(keys).alias("sma200"),
        (100 - 100 / (1 + gain.ewm_mean(alpha=1/14, adjust=False, min_samples=14).over(keys)
                      / loss.ewm_mean(alpha=1/14, adjust=False, min_samples=14).over(keys))).alias("rsi14"),
        (pl.col("close") / pl.col("close").shift(10).over(keys) - 1).alias("roc10"),
        pl.col("volume").rolling_mean(20, min_samples=20).over(keys).alias("vol_sma20"),
        pl.col("close").rolling_std(20, min_samples=20).over(keys).alias("bb_std"),
        pl.col("high").rolling_max(15, min_samples=15).over(keys).alias("hh15"),
        pl.col("low").rolling_min(15, min_samples=15).over(keys).alias("ll15"),
        pl.col("high").rolling_max(20, min_samples=20).over(keys).alias("hh20"),
        pl.col("low").rolling_min(10, min_samples=10).over(keys).alias("ll10"),
        pl.col("high").rolling_max(10, min_samples=10).over(keys).alias("hh10"),
        pl.col("low").rolling_min(5, min_samples=5).over(keys).alias("ll5"),
        pl.col("low").rolling_min(3, min_samples=3).over(keys).alias("ll3"),
        pl.col("close").cum_count().over(keys).alias("seg_len"),
    )
    df = df.with_columns(
        (pl.col("sma20") + 2 * pl.col("bb_std")).alias("bb_upper"),
        (pl.col("sma20") - 2 * pl.col("bb_std")).alias("bb_lower"),
        ((pl.col("sma20") + 2 * pl.col("bb_std")) - (pl.col("sma20") - 2 * pl.col("bb_std"))).alias("bb_width_raw"),
    )
    df = df.with_columns(
        (pl.col("bb_width_raw") / pl.col("sma20")).alias("bb_width"),
        pl.col("ema20").shift(1).over(keys).alias("ema20_prev"),
        pl.col("ema50").shift(1).over(keys).alias("ema50_prev"),
        pl.col("close").shift(1).over(keys).alias("close_prev"),
        pl.col("rsi14").shift(1).over(keys).alias("rsi_prev"),
        pl.col("bb_lower").shift(1).over(keys).alias("bb_lower_prev"),
        pl.col("close").shift(1).over(keys).alias("close_prev2"),
        pl.col("hh15").shift(1).over(keys).alias("hh15_prev"),
        pl.col("ll15").shift(1).over(keys).alias("ll15_prev"),
        pl.col("hh20").shift(1).over(keys).alias("hh20_prev"),
        pl.col("ll10").shift(1).over(keys).alias("ll10_prev"),
        pl.col("hh10").shift(1).over(keys).alias("hh10_prev"),
        pl.col("ll5").shift(1).over(keys).alias("ll5_prev"),
        (pl.col("close").shift(21).over("symbol") ).alias("close_m21"),
        (pl.col("close").shift(252).over("symbol")).alias("close_m252"),
    )
    df = df.with_columns(
        (pl.col("close_m21") / pl.col("close_m252") - 1).alias("mom121"),
    )
    # bb_width only exists after the previous call, so its rolling min goes here.
    df = df.with_columns(
        pl.col("bb_width").rolling_min(20, min_samples=20).over(keys).shift(1).over(keys).alias("bbw_min20_prev"),
    )
    return df

def build_members(liq: pl.DataFrame) -> pl.DataFrame:
    exclude = {s for s in liq["symbol"].unique().to_list() if uv.looks_like_fund(s)}
    print(f"excluding {len(exclude)} fund/ETF-like symbols", flush=True)
    members = []
    for d in uv.rebalance_dates(liq):
        members.append(uv.select(liq, d, exclude=exclude).select("date", "symbol"))
    members = pl.concat(members).rename({"date": "rebal"})
    print(f"members: {members['rebal'].n_unique()} monthly rebalances, "
          f"{members['symbol'].n_unique():,} distinct names", flush=True)
    return members

def forward_fill_members(panel: pl.DataFrame, members: pl.DataFrame) -> pl.DataFrame:
    sessions = panel.select("date").unique().sort("date")
    span = sessions.join(members.select(pl.col("rebal")).unique(), how="cross").filter(
        pl.col("rebal") <= pl.col("date"))
    latest = span.group_by("date").agg(pl.col("rebal").max())
    eligible = latest.join(members, on="rebal").select("date", "symbol")
    return panel.join(eligible, on=["date", "symbol"], how="inner")

def nifty_regime(panel: pl.DataFrame) -> dict:
    """NIFTYBEES close > SMA200 by date (proxy for NIFTY50 > SMA200)."""
    nb = (panel.filter(pl.col("symbol") == "NIFTYBEES")
          .sort("session_idx")
          .with_columns(pl.col("close").rolling_mean(200, min_samples=100).alias("sma200_nb")))
    return {r["date"]: bool(r["close"] > r["sma200_nb"]) if r["sma200_nb"] is not None else False
            for r in nb.to_dicts()}

# --- Strategy signal definitions (all on close of D; fill open D+1) ---
# Each returns entry bool, exit bool, stop price (or None), target price (or None), max_hold.
# Stops/targets are fixed at entry from the signal day's levels.
STRATEGIES = {
 "1_ema_pullback": "20EMA>50EMA, pullback to within 2% above 20EMA, bullish close off it; stop 2% below 5-session swing low; trail exit close<20EMA.",
 "2_range_breakout": "15-session range <=8% wide, close above prior 15d high, volume>=1.5x 20d avg; stop at range midpoint; target = range height projected up.",
 "3_rsi_pullback": "Uptrend (20EMA>50EMA, close>50EMA), pullback to 20EMA/50EMA, RSI turns up from 40-50; stop below 3-session low; target prior 10d high.",
 "4_rsi_bb_reversion": "Prev close below lower BB (20,2) with RSI<30; enter on close back inside BB + RSI turning up; stop below 5-session low; target middle BB.",
 "5_dual_momentum": "Close>EMA50, RSI14 crosses above 60, AND (vol>1.5x SMA20 OR ROC10>4%), NIFTYBEES> SMA200; exit close<EMA50 or RSI<50.",
 "6_donchian20": "Close > highest high of prior 20 sessions, volume>=1.5x avg; exit close < lowest low of prior 10 sessions.",
 "7_momentum_pullback": "12-1 momentum rank <=30 in liquid-300, pullback 2-8% off 5d high, RSI 40-60, bullish reversal close; max hold 15 sessions, stop below pullback low.",
 "8_bb_squeeze": "BB width narrowest in 20 sessions, close above upper BB, volume>=1.5x; exit close<middle BB, stop at entry lower BB.",
}

def add_signals(df: pl.DataFrame, regime: dict) -> pl.DataFrame:
    df = df.with_columns(pl.col("date").map_elements(lambda d: regime.get(d, False),
                          return_dtype=pl.Boolean).alias("nifty_ok"))
    # momentum rank within date among eligible members
    df = df.with_columns(pl.col("mom121").rank("dense", descending=True).over("date").alias("mom_rank"))
    c, e20, e50 = pl.col("close"), pl.col("ema20"), pl.col("ema50")
    df = df.with_columns([
        # 1 EMA pullback
        ((e20 > e50) & (pl.col("low") <= e20 * 1.02) & (c > e20) & (c > pl.col("open"))
         & (pl.col("seg_len") >= 60)).alias("s1_entry"),
        (c < e20).alias("s1_exit"),
        (pl.col("ll5") * 0.98).alias("s1_stop"),
        # 2 range breakout
        ((((pl.col("hh15_prev") - pl.col("ll15_prev")) / pl.col("ll15_prev") <= 0.08)
          & (c > pl.col("hh15_prev")) & (pl.col("volume") >= 1.5 * pl.col("vol_sma20"))
          & (pl.col("seg_len") >= 60))).alias("s2_entry"),
        ((pl.col("hh15_prev") + pl.col("ll15_prev")) / 2).alias("s2_stop"),
        (pl.col("hh15_prev") + (pl.col("hh15_prev") - pl.col("ll15_prev"))).alias("s2_target"),
        # 3 RSI pullback turn-up
        ((e20 > e50) & (c > e50)
         & ((pl.col("low") <= e20 * 1.02) | (pl.col("low") <= pl.col("sma50") * 1.02))
         & (pl.col("rsi_prev") >= 40) & (pl.col("rsi_prev") <= 50)
         & (pl.col("rsi14") > pl.col("rsi_prev")) & (c > pl.col("open"))
         & (pl.col("seg_len") >= 60)).alias("s3_entry"),
        (pl.col("ll3") * 0.99).alias("s3_stop"),
        (pl.col("hh10_prev")).alias("s3_target"),
        # 4 RSI + BB mean reversion
        ((pl.col("close_prev") < pl.col("bb_lower_prev")) & (pl.col("rsi_prev") < 30)
         & (c > pl.col("bb_lower")) & (pl.col("rsi14") > pl.col("rsi_prev"))
         & (pl.col("seg_len") >= 60)).alias("s4_entry"),
        (pl.col("ll5") * 0.99).alias("s4_stop"),
        (pl.col("sma20")).alias("s4_target"),
        # 5 dual momentum
        ((c > e50) & (pl.col("rsi_prev") <= 60) & (pl.col("rsi14") > 60)
         & ((pl.col("volume") >= 1.5 * pl.col("vol_sma20")) | (pl.col("roc10") > 0.04))
         & (pl.col("nifty_ok")) & (pl.col("seg_len") >= 60)).alias("s5_entry"),
        ((c < e50) | (pl.col("rsi14") < 50)).alias("s5_exit"),
        # 6 Donchian 20
        ((c > pl.col("hh20_prev")) & (pl.col("volume") >= 1.5 * pl.col("vol_sma20"))
         & (pl.col("seg_len") >= 60)).alias("s6_entry"),
        (c < pl.col("ll10_prev")).alias("s6_exit"),
        # 7 momentum pullback
        ((pl.col("mom_rank") <= 30)
         & (((pl.col("hh10_prev") - c) / pl.col("hh10_prev") >= 0.02)
            & ((pl.col("hh10_prev") - c) / pl.col("hh10_prev") <= 0.08))
         & (pl.col("rsi14") >= 40) & (pl.col("rsi14") <= 60)
         & (c > pl.col("open")) & (c > pl.col("close_prev"))
         & (pl.col("seg_len") >= 260)).alias("s7_entry"),
        (pl.col("ll5") * 0.99).alias("s7_stop"),
        # 8 BB squeeze
        ((pl.col("bb_width") <= pl.col("bbw_min20_prev"))
         & (c > pl.col("bb_upper")) & (pl.col("volume") >= 1.5 * pl.col("vol_sma20"))
         & (pl.col("seg_len") >= 60)).alias("s8_entry"),
        (c < pl.col("sma20")).alias("s8_exit"),
        (pl.col("bb_lower")).alias("s8_stop"),
    ])
    return df

CFG = {
 "1_ema_pullback": dict(entry="s1_entry", exit="s1_exit", stop="s1_stop", target=None, max_hold=None),
 "2_range_breakout": dict(entry="s2_entry", exit=None, stop="s2_stop", target="s2_target", max_hold=None),
 "3_rsi_pullback": dict(entry="s3_entry", exit=None, stop="s3_stop", target="s3_target", max_hold=None),
 "4_rsi_bb_reversion": dict(entry="s4_entry", exit=None, stop="s4_stop", target="s4_target", max_hold=None),
 "5_dual_momentum": dict(entry="s5_entry", exit="s5_exit", stop=None, target=None, max_hold=None),
 "6_donchian20": dict(entry="s6_entry", exit="s6_exit", stop=None, target=None, max_hold=None),
 "7_momentum_pullback": dict(entry="s7_entry", exit=None, stop="s7_stop", target=None, max_hold=15),
 "8_bb_squeeze": dict(entry="s8_entry", exit="s8_exit", stop="s8_stop", target=None, max_hold=None),
}

def run_trades(rows, cfg):
    by_sym = defaultdict(list)
    for r in rows: by_sym[r["symbol"]].append(r)
    trades, counters = [], {"ca_closed": 0, "entry_no_fill": 0, "exit_no_fill": 0, "open_at_end": 0,
                            "stop_hit": 0, "target_hit": 0, "max_hold_exit": 0, "signal_exit": 0}
    for sym, rs in by_sym.items():
        pos = None
        for i, r in enumerate(rs):
            if pos is None:
                if r.get(cfg["entry"]):
                    nxt = rs[i+1] if i+1 < len(rs) else None
                    if nxt is None or nxt["session_idx"] != r["session_idx"] + 1:
                        counters["entry_no_fill"] += 1; continue
                    pos = {"symbol": sym, "entry_sig_date": r["date"], "entry_sig_idx": r["session_idx"],
                           "entry_fill_date": nxt["date"], "entry_open": nxt["open"],
                           "stop": r.get(cfg["stop"]) if cfg["stop"] else None,
                           "target": r.get(cfg["target"]) if cfg["target"] else None}
                continue
            # in position: this row is a holding session (entry filled at its open if i == entry idx+1)
            if r.get("gap"):
                prev = rs[i-1]
                trades.append({**pos, "exit_sig_date": prev["date"], "exit_sig_idx": prev["session_idx"],
                               "exit_fill_date": prev["date"], "exit_open": prev["close"],
                               "gross_ret": prev["close"] / pos["entry_open"] - 1,
                               "hold_sessions": prev["session_idx"] - pos["entry_sig_idx"], "ca": True})
                counters["ca_closed"] += 1; pos = None; continue
            hold = r["session_idx"] - pos["entry_sig_idx"]
            # stop first (conservative), then target, then max-hold, then signal exit (next open)
            if pos["stop"] is not None and r["low"] <= pos["stop"]:
                trades.append({**pos, "exit_sig_date": r["date"], "exit_sig_idx": r["session_idx"],
                               "exit_fill_date": r["date"], "exit_open": pos["stop"],
                               "gross_ret": pos["stop"] / pos["entry_open"] - 1,
                               "hold_sessions": hold, "ca": False})
                counters["stop_hit"] += 1; pos = None; continue
            if pos["target"] is not None and r["high"] >= pos["target"]:
                trades.append({**pos, "exit_sig_date": r["date"], "exit_sig_idx": r["session_idx"],
                               "exit_fill_date": r["date"], "exit_open": pos["target"],
                               "gross_ret": pos["target"] / pos["entry_open"] - 1,
                               "hold_sessions": hold, "ca": False})
                counters["target_hit"] += 1; pos = None; continue
            if cfg["max_hold"] is not None and hold >= cfg["max_hold"]:
                nxt = rs[i+1] if i+1 < len(rs) else None
                if nxt is None or nxt["session_idx"] != r["session_idx"] + 1:
                    counters["exit_no_fill"] += 1; pos = None; continue
                trades.append({**pos, "exit_sig_date": r["date"], "exit_sig_idx": r["session_idx"],
                               "exit_fill_date": nxt["date"], "exit_open": nxt["open"],
                               "gross_ret": nxt["open"] / pos["entry_open"] - 1,
                               "hold_sessions": hold, "ca": False})
                counters["max_hold_exit"] += 1; pos = None; continue
            if cfg["exit"] and r.get(cfg["exit"]):
                nxt = rs[i+1] if i+1 < len(rs) else None
                if nxt is None or nxt["session_idx"] != r["session_idx"] + 1:
                    counters["exit_no_fill"] += 1; pos = None; continue
                trades.append({**pos, "exit_sig_date": r["date"], "exit_sig_idx": r["session_idx"],
                               "exit_fill_date": nxt["date"], "exit_open": nxt["open"],
                               "gross_ret": nxt["open"] / pos["entry_open"] - 1,
                               "hold_sessions": hold, "ca": False})
                counters["signal_exit"] += 1; pos = None
        if pos is not None: counters["open_at_end"] += 1
    return trades, counters

DATE_OF = {}; SYM_IDXS = {}
def build_weights(trades):
    live = [t for t in trades if not t["ca"]]
    per_day = defaultdict(list)
    for t in live:
        for idx in range(t["entry_sig_idx"], t["exit_sig_idx"]):
            if idx in SYM_IDXS.get(t["symbol"], set()): per_day[idx].append(t)
    rows = []
    for idx, ts in per_day.items():
        w = 1.0 / len(ts)
        for t in ts: rows.append({"date": DATE_OF[(t["symbol"], idx)], "symbol": t["symbol"], "weight": w})
    trade_dates = {t["entry_sig_date"] for t in live} | {t["exit_sig_date"] for t in live}
    return pl.DataFrame(rows, schema={"date": pl.Date, "symbol": pl.String, "weight": pl.Float64}), trade_dates

def longest_drawdown(equity):
    if equity.is_empty(): return 0.0, 0
    cap = equity["capital"].to_list(); peak = cap[0]; peak_i = 0; worst_dd = 0.0; worst_len = 0
    for i, c in enumerate(cap):
        if c >= peak: peak = c; peak_i = i
        else:
            dd = c / peak - 1
            if dd < worst_dd: worst_dd = dd
            if i - peak_i > worst_len: worst_len = i - peak_i
    return worst_dd, worst_len

def trade_stats(trades):
    ts = [t for t in trades if not t["ca"]]
    if not ts: return {"n": 0}
    rets = sorted(t["gross_ret"] for t in ts); holds = sorted(t["hold_sessions"] for t in ts); n = len(ts)
    med = lambda xs: xs[n//2] if n % 2 else (xs[n//2-1] + xs[n//2]) / 2
    return {"n": n, "win_rate": sum(1 for r in rets if r > 0) / n,
            "pct_ge_5pct": sum(1 for r in rets if r >= 0.05) / n,
            "pct_ge_6pct": sum(1 for r in rets if r >= 0.06) / n,
            "mean_ret": sum(rets) / n, "median_ret": med(rets), "best": rets[-1], "worst": rets[0],
            "median_hold_sessions": med(holds), "mean_hold_sessions": sum(holds) / n}

def main() -> int:
    global DATE_OF, SYM_IDXS
    panel = load_panel()
    regime = nifty_regime(panel)
    ind = add_indicators(panel)
    liq = uv.liquidity(ind)
    members = build_members(liq)
    elig = forward_fill_members(liq, members)
    elig = add_signals(elig, regime).sort(["symbol", "session_idx"])
    print(f"eligible rows: {elig.height:,}", flush=True)
    DATE_OF = {(r["symbol"], r["session_idx"]): r["date"]
               for r in liq.select("symbol", "session_idx", "date").to_dicts()}
    for _sym, _idx in DATE_OF: SYM_IDXS.setdefault(_sym, set()).add(_idx)
    rows = elig.to_dicts()
    card = costs.load_rate_cards(REPO / "config/costs.yaml")["delivery"]
    slip = costs.load_slippage(REPO / "config/costs.yaml")
    engine_panel = liq.select("date", "symbol", "open", "adtv")
    top_syms = panel.group_by("symbol").len().sort("len", descending=True).head(5)["symbol"].to_list()
    results = {"capital": CAPITAL, "universe": "liquid-300 (repo, monthly, EQ, ex-ETF)",
               "window": [str(panel["date"].min()), str(panel["date"].max())],
               "strategies": STRATEGIES,
               "benchmark_ema_crossover": {
                   "source": "research/swing-ema-crossover commit 5199f35 (not re-run)",
                   "A_no_rsi": {"trades": 1066, "cagr": -0.2444, "sharpe": -1.55, "max_drawdown": -0.4455,
                                "win_rate": 0.3227, "pct_ge_5pct": 0.152, "median_ret": -0.0191},
                   "B_with_rsi75": {"trades": 1080, "cagr": -0.1227, "sharpe": -0.94, "max_drawdown": -0.3235,
                                    "win_rate": 0.3509, "pct_ge_5pct": 0.1917, "median_ret": -0.0166}}}
    for label, cfg in CFG.items():
        trades, counters = run_trades(rows, cfg)
        w, trade_dates = build_weights(trades)
        real_keys = set(zip(w["date"].to_list(), w["symbol"].to_list())) if w.height else set()
        sent_rows = []
        for s in top_syms:
            for d in panel.filter(pl.col("symbol") == s)["date"].to_list():
                if (d, s) not in real_keys: sent_rows.append({"date": d, "symbol": s, "weight": 0.0})
        if sent_rows: w = pl.concat([w, pl.DataFrame(sent_rows, schema={"date": pl.Date, "symbol": pl.String, "weight": pl.Float64})])
        w = w.sort(["date", "symbol"])
        print(f"\n=== {label}: {len(trades)} trades, weights {w.height:,} rows ===", flush=True)
        if w.height == 0 or not trades:
            print("NO TRADES — skipping engine", flush=True)
            results[label] = {"rules": STRATEGIES[label], "trades": trade_stats(trades), "counters": counters,
                              "portfolio": None, "in_sample": None, "out_of_sample": None, "benchmark": {}}
            continue
        res = engine.run(engine_panel, w, capital=CAPITAL, card=card, slippage=slip, tier="mid", trade_dates=trade_dates)
        m = metrics.compute(res.equity)
        print(m.summary()); print(f"gross CAGR    {m.cagr + m.cost_drag:>8.1%}")
        ldd, llen = longest_drawdown(res.equity)
        print(f"longest drawdown {llen} sessions (max DD {ldd:.1%})")
        try: bench = metrics.vs_benchmark(res.equity); print(f"vs benchmark: {bench}")
        except Exception as e: print(f"vs_benchmark unavailable: {e}"); bench = {}
        ism = metrics.compute(res.equity.filter(pl.col("date") < SPLIT))
        oosm = metrics.compute(res.equity.filter(pl.col("date") >= SPLIT))
        print(f"in-sample  ..{SPLIT}: CAGR {ism.cagr:.1%} Sharpe {ism.sharpe:.2f} maxDD {ism.max_drawdown:.1%}")
        print(f"out-sample {SPLIT}..: CAGR {oosm.cagr:.1%} Sharpe {oosm.sharpe:.2f} maxDD {oosm.max_drawdown:.1%}")
        ts = trade_stats(trades); print(f"trades: {ts}"); print(f"counters: {counters}")
        results[label] = {"rules": STRATEGIES[label], "trades": ts, "counters": counters,
            "portfolio": {"periods": m.periods, "total_return": m.total_return, "cagr": m.cagr,
                          "gross_cagr": m.cagr + m.cost_drag, "volatility": m.volatility, "sharpe": m.sharpe,
                          "max_drawdown": m.max_drawdown, "hit_rate": m.hit_rate, "avg_turnover": m.avg_turnover,
                          "cost_drag": m.cost_drag, "longest_drawdown_sessions": llen,
                          "final_capital": res.final_capital},
            "in_sample": {"cagr": ism.cagr, "sharpe": ism.sharpe, "max_drawdown": ism.max_drawdown},
            "out_of_sample": {"cagr": oosm.cagr, "sharpe": oosm.sharpe, "max_drawdown": oosm.max_drawdown},
            "benchmark": bench}
    out = Path(__file__).resolve().parent
    (out / "results.json").write_text(json.dumps(results, indent=2, default=str))
    print(f"\nwrote {out / 'results.json'}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
