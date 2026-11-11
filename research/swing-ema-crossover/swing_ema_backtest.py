#!/usr/bin/env python
"""Backtest the 20/50 DEMA swing claim from a Hindi reel (research only).

Claim (transcript saved at workspace goals/.../hidden_files/transcript.txt):
- Daily chart, any stock. 50 EMA + 20 EMA (exponential, NOT simple).
- Enter when 20 EMA crosses 50 EMA from below.
- Hold until 20 EMA tilts down.
- Optional: also exit if daily RSI >= 75.
- Claimed: minimum gain 5-6% per trade, without tension/stress.

Assumptions NOT in the video (documented here, not presented as the claim):
- Universe: repo's liquid-300 -- top 300 by trailing 20-session median
  turnover, >= Rs 5 crore/day, coverage >= 0.80, monthly rebalance, EQ
  series only, ETFs excluded. The video says "any stock"; an undefined
  universe cannot be backtested, so the repo's own universe is used.
- Fill: signal on close of D, trade at open of D+1 (repo engine convention,
  no look-ahead). Equal weight across open positions, cash when flat.
- Capital Rs 2,00,000, delivery charges + mid-tier slippage from
  config/costs.yaml, min trade Rs 500 (repo defaults).
- "Tilts down" = EMA20 falls vs the previous session (first down-tick).
- RSI = Wilder 14 on closes.
- Data: NSE bhavdata in data/sessions (this clone: Oct 2024 onwards).
  Closes are UNADJUSTED for splits/bonus (the adjusted panel needs
  data/curated + data/raw/actions, which are not in git). An overnight gap
  > 20% (open vs prev close) is treated as a corporate-action break:
  indicators restart and any open trade is closed at the last clean close
  and flagged/excluded from headline trade stats (counted separately).
"""
from __future__ import annotations

import glob
import json
import os
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import polars as pl

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from algotrade.backtest import engine, metrics  # noqa: E402
from algotrade.core import costs, universe as uv  # noqa: E402

CAPITAL = float(os.environ.get("SWING_CAPITAL", "200000"))
SPLIT = date(2026, 1, 1)  # in-sample Oct 2024-Dec 2025 | out-of-sample Jan 2026-


def load_panel() -> pl.DataFrame:
    files = sorted(glob.glob(str(REPO / "data/sessions" / "*" / "*.csv")))
    print(f"loading {len(files)} bhavdata files ...", flush=True)
    frames = []
    for f in files:
        df = pl.read_csv(f, schema_overrides={"DATE1": pl.String})
        df = df.rename({c: c.strip() for c in df.columns})
        df = df.with_columns(
            pl.col("SYMBOL").str.strip_chars(),
            pl.col("SERIES").str.strip_chars(),
            pl.col("DATE1").str.strip_chars(),
        )
        frames.append(df.select(["SYMBOL", "SERIES", "DATE1", "OPEN_PRICE",
                                 "CLOSE_PRICE", "TURNOVER_LACS"]))
    raw = pl.concat(frames, how="vertical")
    panel = (
        raw.filter(pl.col("SERIES") == "EQ")
        .select(
            pl.col("DATE1").str.to_date("%d-%b-%Y").alias("date"),
            pl.col("SYMBOL").alias("symbol"),
            pl.col("OPEN_PRICE").str.strip_chars().cast(pl.Float64).alias("open"),
            pl.col("CLOSE_PRICE").str.strip_chars().cast(pl.Float64).alias("close"),
            (pl.col("TURNOVER_LACS").str.strip_chars().cast(pl.Float64) * 1e5).alias("turnover"),
        )
        .filter(pl.col("open") > 0, pl.col("close") > 0)
        .unique(subset=["date", "symbol"], keep="first", maintain_order=True)
        .sort(["symbol", "date"])
    )
    panel = uv.with_session_index(panel)
    print(f"panel: {panel.height:,} rows, "
          f"{panel['date'].min()} .. {panel['date'].max()}, "
          f"{panel['symbol'].n_unique():,} EQ symbols", flush=True)
    return panel


def add_indicators(panel: pl.DataFrame) -> pl.DataFrame:
    # Corporate-action break: overnight gap > 20%. Indicators restart per
    # (symbol, segment) so a split cannot manufacture a crossover.
    df = panel.sort(["symbol", "session_idx"]).with_columns(
        (
            (pl.col("open") / pl.col("close").shift(1).over("symbol") - 1)
            .abs() > 0.20
        ).fill_null(False).alias("gap")
    )
    df = df.with_columns(
        pl.col("gap").cast(pl.Int32).cum_sum().over("symbol").alias("seg")
    )
    keys = ["symbol", "seg"]
    chg = pl.col("close") - pl.col("close").shift(1).over(keys)
    gain = chg.clip(lower_bound=0)
    loss = (-chg).clip(lower_bound=0)
    df = df.with_columns(
        pl.col("close").ewm_mean(alpha=2 / 21, adjust=False, min_samples=20)
        .over(keys).alias("ema20"),
        pl.col("close").ewm_mean(alpha=2 / 51, adjust=False, min_samples=50)
        .over(keys).alias("ema50"),
        (
            100 - 100 / (1 + gain.ewm_mean(alpha=1 / 14, adjust=False, min_samples=14).over(keys)
                         / loss.ewm_mean(alpha=1 / 14, adjust=False, min_samples=14).over(keys))
        ).alias("rsi14"),
        pl.col("close").cum_count().over(keys).alias("seg_len"),
    )
    # NOTE: columns created in one with_columns are not visible to other
    # expressions in the same call, so the lags go in a second call.
    df = df.with_columns(
        pl.col("ema20").shift(1).over(keys).alias("ema20_prev"),
        pl.col("ema50").shift(1).over(keys).alias("ema50_prev"),
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


def signals(df: pl.DataFrame) -> pl.DataFrame:
    return df.with_columns(
        (
            (pl.col("ema20_prev") <= pl.col("ema50_prev"))
            & (pl.col("ema20") > pl.col("ema50"))
            & (pl.col("seg_len") >= 60)
        ).alias("cross_up"),
        (
            (pl.col("ema20") < pl.col("ema20_prev"))
            & (pl.col("seg_len") >= 60)
        ).alias("tilt_down"),
        (pl.col("rsi14") >= 75).alias("rsi_hi"),
    )


def run_trades(rows: list[dict], use_rsi: bool) -> tuple[list[dict], dict]:
    """State machine per symbol. Returns (trades, counters)."""
    by_sym: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_sym[r["symbol"]].append(r)
    trades: list[dict] = []
    counters = {"ca_closed": 0, "entry_no_fill": 0, "exit_no_fill": 0, "open_at_end": 0}
    for sym, rs in by_sym.items():
        pos = None
        for i, r in enumerate(rs):
            if pos is None:
                if r.get("cross_up"):
                    nxt = rs[i + 1] if i + 1 < len(rs) else None
                    if nxt is None or nxt["session_idx"] != r["session_idx"] + 1:
                        counters["entry_no_fill"] += 1
                        continue
                    pos = {
                        "symbol": sym,
                        "entry_sig_date": r["date"],
                        "entry_sig_idx": r["session_idx"],
                        "entry_fill_date": nxt["date"],
                        "entry_open": nxt["open"],
                    }
                continue
            if r.get("gap"):
                prev = rs[i - 1]
                trades.append({**pos, "exit_sig_date": prev["date"],
                               "exit_sig_idx": prev["session_idx"],
                               "exit_fill_date": prev["date"],
                               "exit_open": prev["close"],
                               "gross_ret": prev["close"] / pos["entry_open"] - 1,
                               "hold_sessions": prev["session_idx"] - pos["entry_sig_idx"],
                               "ca": True})
                counters["ca_closed"] += 1
                pos = None
                continue
            tilt = bool(r.get("tilt_down")) if r.get("tilt_down") is not None else False
            rsi_hi = bool(r.get("rsi_hi")) if r.get("rsi_hi") is not None else False
            if tilt or (use_rsi and rsi_hi):
                nxt = rs[i + 1] if i + 1 < len(rs) else None
                if nxt is None or nxt["session_idx"] != r["session_idx"] + 1:
                    counters["exit_no_fill"] += 1
                    pos = None
                    continue
                trades.append({**pos, "exit_sig_date": r["date"],
                               "exit_sig_idx": r["session_idx"],
                               "exit_fill_date": nxt["date"],
                               "exit_open": nxt["open"],
                               "gross_ret": nxt["open"] / pos["entry_open"] - 1,
                               "hold_sessions": r["session_idx"] - pos["entry_sig_idx"],
                               "ca": False})
                pos = None
        if pos is not None:
            counters["open_at_end"] += 1
    return trades, counters


DATE_OF: dict[tuple[str, int], date] = {}
SYM_IDXS: dict[str, set] = {}


def build_weights(trades: list[dict]) -> tuple[pl.DataFrame, set]:
    """Equal-weight targets on signal dates E..X-1; trade only on E and X."""
    live = [t for t in trades if not t["ca"]]
    per_day: dict[int, list[dict]] = defaultdict(list)
    for t in live:
        # only the symbol's own traded sessions: it may skip sessions
        # inside a hold, and DATE_OF has no row for those.
        for idx in range(t["entry_sig_idx"], t["exit_sig_idx"]):
            if idx in SYM_IDXS.get(t["symbol"], set()):
                per_day[idx].append(t)
    rows = []
    for idx, ts in per_day.items():
        w = 1.0 / len(ts)
        for t in ts:
            rows.append({"date": DATE_OF[(t["symbol"], idx)], "symbol": t["symbol"], "weight": w})
    trade_dates = {t["entry_sig_date"] for t in live} | {t["exit_sig_date"] for t in live}
    return pl.DataFrame(rows, schema={"date": pl.Date, "symbol": pl.String, "weight": pl.Float64}), trade_dates


def longest_drawdown(equity: pl.DataFrame) -> tuple[float, int]:
    if equity.is_empty():
        return 0.0, 0
    cap = equity["capital"].to_list()
    peak = cap[0]
    peak_i = 0
    worst_dd = 0.0
    worst_len = 0
    for i, c in enumerate(cap):
        if c >= peak:
            peak = c
            peak_i = i
        else:
            dd = c / peak - 1
            if dd < worst_dd:
                worst_dd = dd
            if i - peak_i > worst_len:
                worst_len = i - peak_i
    return worst_dd, worst_len


def trade_stats(trades: list[dict]) -> dict:
    ts = [t for t in trades if not t["ca"]]
    if not ts:
        return {"n": 0}
    rets = sorted(t["gross_ret"] for t in ts)
    holds = sorted(t["hold_sessions"] for t in ts)
    n = len(ts)
    def med(xs):
        return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2
    return {
        "n": n,
        "win_rate": sum(1 for r in rets if r > 0) / n,
        "pct_ge_5pct": sum(1 for r in rets if r >= 0.05) / n,
        "pct_ge_6pct": sum(1 for r in rets if r >= 0.06) / n,
        "mean_ret": sum(rets) / n,
        "median_ret": med(rets),
        "best": rets[-1],
        "worst": rets[0],
        "median_hold_sessions": med(holds),
        "mean_hold_sessions": sum(holds) / n,
    }


def main() -> int:
    global DATE_OF, SYM_IDXS
    panel = load_panel()
    ind = add_indicators(panel)
    liq = uv.liquidity(ind)
    members = build_members(liq)
    elig = forward_fill_members(liq, members)
    elig = signals(elig).sort(["symbol", "session_idx"])
    print(f"eligible rows: {elig.height:,}", flush=True)
    DATE_OF = {(r["symbol"], r["session_idx"]): r["date"]
               for r in liq.select("symbol", "session_idx", "date").to_dicts()}
    for _sym, _idx in DATE_OF:
        SYM_IDXS.setdefault(_sym, set()).add(_idx)
    rows = elig.to_dicts()
    card = costs.load_rate_cards(REPO / "config/costs.yaml")["delivery"]
    slip = costs.load_slippage(REPO / "config/costs.yaml")
    engine_panel = liq.select("date", "symbol", "open", "adtv")
    top_syms = (
        panel.group_by("symbol").len().sort("len", descending=True).head(5)["symbol"].to_list()
    )
    results: dict = {"capital": CAPITAL, "universe": "liquid-300 (repo, monthly, EQ, ex-ETF)",
                     "window": [str(panel["date"].min()), str(panel["date"].max())]}
    for label, use_rsi in (("A_no_rsi", False), ("B_with_rsi75", True)):
        trades, counters = run_trades(rows, use_rsi)
        w, trade_dates = build_weights(trades)
        real_keys = set(zip(w["date"].to_list(), w["symbol"].to_list())) if w.height else set()
        sent_rows = []
        for s in top_syms:
            for d in panel.filter(pl.col("symbol") == s)["date"].to_list():
                if (d, s) not in real_keys:
                    sent_rows.append({"date": d, "symbol": s, "weight": 0.0})
        if sent_rows:
            w = pl.concat([w, pl.DataFrame(sent_rows, schema={"date": pl.Date, "symbol": pl.String, "weight": pl.Float64})])
        w = w.sort(["date", "symbol"])
        print(f"\n=== variant {label}: {len(trades)} trades, weights {w.height:,} rows ===", flush=True)
        res = engine.run(engine_panel, w, capital=CAPITAL, card=card, slippage=slip,
                         tier="mid", trade_dates=trade_dates)
        m = metrics.compute(res.equity)
        print(m.summary())
        print(f"gross CAGR    {m.cagr + m.cost_drag:>8.1%}")
        ldd, llen = longest_drawdown(res.equity)
        print(f"longest drawdown {llen} sessions (max DD {ldd:.1%})")
        try:
            bench = metrics.vs_benchmark(res.equity)
            print(f"vs benchmark: {bench}")
        except Exception as e:
            print(f"vs_benchmark unavailable: {e}")
            bench = {}
        ism = metrics.compute(res.equity.filter(pl.col("date") < SPLIT))
        oosm = metrics.compute(res.equity.filter(pl.col("date") >= SPLIT))
        print(f"in-sample  ..{SPLIT}: CAGR {ism.cagr:.1%} Sharpe {ism.sharpe:.2f} maxDD {ism.max_drawdown:.1%}")
        print(f"out-sample {SPLIT}..: CAGR {oosm.cagr:.1%} Sharpe {oosm.sharpe:.2f} maxDD {oosm.max_drawdown:.1%}")
        ts = trade_stats(trades)
        print(f"trades: {ts}")
        print(f"counters: {counters}")
        results[label] = {
            "use_rsi_exit": use_rsi,
            "trades": ts,
            "counters": counters,
            "portfolio": {"periods": m.periods, "total_return": m.total_return,
                          "cagr": m.cagr, "gross_cagr": m.cagr + m.cost_drag,
                          "volatility": m.volatility, "sharpe": m.sharpe,
                          "max_drawdown": m.max_drawdown, "hit_rate": m.hit_rate,
                          "avg_turnover": m.avg_turnover, "cost_drag": m.cost_drag,
                          "longest_drawdown_sessions": llen,
                          "final_capital": res.final_capital},
            "in_sample": {"cagr": ism.cagr, "sharpe": ism.sharpe, "max_drawdown": ism.max_drawdown},
            "out_of_sample": {"cagr": oosm.cagr, "sharpe": oosm.sharpe, "max_drawdown": oosm.max_drawdown},
            "benchmark": bench,
        }
    out = Path(__file__).resolve().parent
    (out / "results.json").write_text(json.dumps(results, indent=2, default=str))
    print(f"\nwrote {out / 'results.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
