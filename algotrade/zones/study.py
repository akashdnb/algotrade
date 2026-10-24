"""Entry-based event study for demand/supply zones.

A trader does not ask "did price drift after the zone formed". They ask: when
price first comes back to a *fresh* zone and I buy it, do I make more than I
would have buying an equally-shaped level on an arbitrary day? Everything here
answers that and nothing else:

* **Event** -- the first session that reaches the zone after it formed. If that
  session already closes through the far edge the zone is broken and there is no
  trade. Otherwise entry is the next session's open (signal on the close of D,
  fill at the open of D+1, as everywhere else in this repository).
* **Outcome** -- return from that open over fixed horizons, market-adjusted by
  the equal-weight return of the liquid universe over the same window, with and
  without a stop at the far edge of the zone.
* **Control** -- the same symbol, the same calendar year, the same zone geometry
  (percentage offsets from the close), anchored on a random day. Same event
  logic, so the pull-back-to-a-level mechanic cancels and only the detector's
  choice of *where* and *when* is left.

Pure module: arrays and DataFrames in, numbers out.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date
from typing import Any, Sequence

import numpy as np
import polars as pl

HORIZONS: tuple[int, ...] = (5, 10, 20)
TREND_WINDOW = 200
_EPOCH = date(1970, 1, 1)


def epoch_day(day: date) -> int:
    return (day - _EPOCH).days


@dataclass(frozen=True, slots=True)
class Bars:
    """One symbol's daily bars as aligned arrays, ascending by date."""

    dates: np.ndarray  # epoch days
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    eligible: np.ndarray  # point-in-time tradeable flag on that session's close
    years: np.ndarray
    trend_up: np.ndarray  # close above its trailing TREND_WINDOW-session mean, as of that close


@dataclass(frozen=True, slots=True)
class Entry:
    reach: int  # index of the first session that reached the zone
    entry: int | None  # index of the entry session (None when there is no trade)
    age: int  # sessions from creation to reach (1 = the very next session)
    status: str  # ok | broken_on_touch | gap_through


def _trend_up(close: np.ndarray, window: int = TREND_WINDOW) -> np.ndarray:
    """True where the close is above the mean of the last `window` closes (inclusive); False until then."""
    out = np.zeros(close.size, dtype=bool)
    if close.size >= window:
        csum = np.concatenate([[0.0], np.cumsum(close)])
        mean = (csum[window:] - csum[:-window]) / window
        out[window - 1:] = close[window - 1:] > mean
    return out


def build_bars(panel: pl.DataFrame) -> dict[str, Bars]:
    """Split a panel into per-symbol arrays. Needs an `eligible` boolean column."""
    required = {"symbol", "date", "open", "high", "low", "close", "eligible"}
    missing = required - set(panel.columns)
    if missing:
        raise ValueError(f"panel is missing columns: {sorted(missing)}")
    clean = panel.filter((pl.col("open") > 0) & (pl.col("close") > 0) & (pl.col("high") > 0) & (pl.col("low") > 0))
    out: dict[str, Bars] = {}
    for key, group in clean.sort(["symbol", "date"]).partition_by("symbol", as_dict=True).items():
        symbol = key[0] if isinstance(key, tuple) else key
        days = group["date"].cast(pl.Date).to_physical().to_numpy().astype(np.int64)
        out[symbol] = Bars(
            dates=days,
            open=group["open"].to_numpy().astype(float),
            high=group["high"].to_numpy().astype(float),
            low=group["low"].to_numpy().astype(float),
            close=group["close"].to_numpy().astype(float),
            eligible=group["eligible"].to_numpy().astype(bool),
            years=group["date"].dt.year().to_numpy().astype(np.int64),
            trend_up=_trend_up(group["close"].to_numpy().astype(float)),
        )
    return out


def find_entry(bars: Bars, *, demand: bool, low: float, high: float, after_day: int) -> Entry | None:
    """First reach of the zone strictly after `after_day`, and the resulting entry."""
    n = len(bars.dates)
    start = int(np.searchsorted(bars.dates, after_day, side="right"))
    if start >= n:
        return None
    hit = np.flatnonzero((bars.low[start:] <= high) if demand else (bars.high[start:] >= low))
    if hit.size == 0:
        return None
    reach = start + int(hit[0])
    age = reach - start + 1
    far_edge = low if demand else high
    if (demand and bars.close[reach] < far_edge) or (not demand and bars.close[reach] > far_edge):
        return Entry(reach, None, age, "broken_on_touch")
    entry = reach + 1
    if entry >= n:
        return None
    if (demand and bars.open[entry] < far_edge) or (not demand and bars.open[entry] > far_edge):
        return Entry(reach, None, age, "gap_through")
    return Entry(reach, entry, age, "ok")


def broken_day(bars: Bars, *, demand: bool, low: float, high: float, after_day: int) -> int:
    """Epoch day of the first close through the zone's far edge after creation; a far-future day if never."""
    start = int(np.searchsorted(bars.dates, after_day, side="right"))
    closes = bars.close[start:]
    hit = np.flatnonzero(closes < low) if demand else np.flatnonzero(closes > high)
    return int(bars.dates[start + int(hit[0])]) if hit.size else 10**9


def has_confluence(others: Sequence[tuple[float, float, int, int]], *, low: float, high: float, reach_day: int) -> bool:
    """Whether any other-timeframe zone (low, high, created_day, broken_day) was alive and overlapping at the reach.

    A zone formed on `reach_day` or later is not known at the signal, and one
    already closed through is not a live level.
    """
    return any(o_low <= high and low <= o_high and created < reach_day <= broken for o_low, o_high, created, broken in others)


@dataclass(frozen=True, slots=True)
class Market:
    """Equal-weight market as cumulative log returns, split into open->close and close->open."""

    days: np.ndarray
    cum_oc: np.ndarray  # length len(days)+1, leading 0
    cum_co: np.ndarray

    def window_return(self, first_day: int, last_day: int) -> float:
        """Return from the open of `first_day` to the close of `last_day`."""
        i = int(np.searchsorted(self.days, first_day))
        j = int(np.searchsorted(self.days, last_day))
        oc = self.cum_oc[j + 1] - self.cum_oc[i]
        co = self.cum_co[j + 1] - self.cum_co[i + 1]
        return float(np.expm1(oc + co))


def build_market(panel: pl.DataFrame, *, clip: float = 0.3) -> Market:
    """Equal-weight return of the eligible universe, open->close and close->open per session."""
    frame = (
        panel.sort(["symbol", "date"])
        .with_columns(
            (pl.col("close") / pl.col("open") - 1).alias("r_oc"),
            (pl.col("open") / pl.col("close").shift(1).over("symbol") - 1).alias("r_co"),
        )
        .filter(pl.col("eligible") & (pl.col("open") > 0) & (pl.col("close") > 0))
        .group_by("date")
        .agg(
            pl.col("r_oc").clip(-clip, clip).mean().alias("r_oc"),
            pl.col("r_co").clip(-clip, clip).mean().alias("r_co"),
        )
        .sort("date")
    )
    days = frame["date"].cast(pl.Date).to_physical().to_numpy().astype(np.int64)
    oc = np.log1p(frame["r_oc"].fill_null(0.0).to_numpy())
    co = np.log1p(frame["r_co"].fill_null(0.0).to_numpy())
    return Market(days, np.concatenate([[0.0], np.cumsum(oc)]), np.concatenate([[0.0], np.cumsum(co)]))


def outcomes(bars: Bars, entry: int, *, demand: bool, stop: float, market: Market, horizons: Sequence[int] = HORIZONS) -> dict[str, float | None]:
    """Direction-signed outcomes from the entry open. A short's gain is the price fall."""
    n = len(bars.dates)
    sign = 1.0 if demand else -1.0
    px = float(bars.open[entry])
    out: dict[str, float | None] = {}
    for h in horizons:
        last = entry + h - 1
        if last >= n:
            out[f"ret_{h}"] = out[f"abn_{h}"] = out[f"ret_stop_{h}"] = out[f"abn_stop_{h}"] = None
            continue
        held = sign * (float(bars.close[last]) / px - 1.0)
        window = market.window_return(int(bars.dates[entry]), int(bars.dates[last]))
        breach = np.flatnonzero(bars.low[entry:last + 1] <= stop) if demand else np.flatnonzero(bars.high[entry:last + 1] >= stop)
        if breach.size:
            at = entry + int(breach[0])
            fill = min(float(bars.open[at]), stop) if demand else max(float(bars.open[at]), stop)
            stopped = sign * (fill / px - 1.0)
        else:
            stopped = held
        out[f"ret_{h}"] = held
        out[f"abn_{h}"] = held - sign * window
        out[f"ret_stop_{h}"] = stopped
        out[f"abn_stop_{h}"] = stopped - sign * window
    return out


def event_row(bars: Bars, found: Entry, *, demand: bool, low: float, high: float, market: Market, horizons: Sequence[int] = HORIZONS) -> dict[str, Any] | None:
    """Outcome row for one found entry; None when there is no tradeable entry."""
    if found.entry is None or not bars.eligible[found.reach]:
        return None
    stop = low if demand else high
    row: dict[str, Any] = {
        "reach_day": int(bars.dates[found.reach]),
        "entry_day": int(bars.dates[found.entry]),
        "age": found.age,
        "entry_open": float(bars.open[found.entry]),
        "risk_pct": abs(float(bars.open[found.entry]) - stop) / float(bars.open[found.entry]),
        "trend_up": bool(bars.trend_up[found.reach]),
    }
    row.update(outcomes(bars, found.entry, demand=demand, stop=stop, market=market, horizons=horizons))
    return row


def matched_controls(bars: Bars, *, demand: bool, off_low: float, off_high: float, created_day: int, k: int, seed_key: str, market: Market, horizons: Sequence[int] = HORIZONS) -> list[dict[str, Any]]:
    """Events for `k` same-shaped zones anchored on random days of the creation year.

    `off_low`/`off_high` are the zone edges as fractions of the close on the
    creation day, so a control sits the same relative distance from price as the
    real zone did when it formed.
    """
    n = len(bars.dates)
    created_idx = int(np.searchsorted(bars.dates, created_day))
    year = int(bars.years[min(created_idx, n - 1)])
    pool = np.flatnonzero(bars.years == year)
    if pool.size == 0:
        return []
    rng = random.Random(seed_key)
    rows: list[dict[str, Any]] = []
    for _ in range(k):
        anchor = int(pool[rng.randrange(pool.size)])
        close = float(bars.close[anchor])
        low, high = close * (1.0 + off_low), close * (1.0 + off_high)
        if low <= 0 or high <= low:
            continue
        found = find_entry(bars, demand=demand, low=low, high=high, after_day=int(bars.dates[anchor]))
        if found is None:
            continue
        row = event_row(bars, found, demand=demand, low=low, high=high, market=market, horizons=horizons)
        if row is not None:
            rows.append(row)
    return rows


def winsorize(values: np.ndarray, q: float = 0.01) -> np.ndarray:
    if values.size == 0 or q <= 0:
        return values
    lo, hi = np.quantile(values, [q, 1.0 - q])
    return np.clip(values, lo, hi)


def cluster_bootstrap_mean(values: np.ndarray, clusters: np.ndarray, *, n_boot: int = 2000, seed: int = 0) -> dict[str, float]:
    """Mean with a percentile CI from resampling whole clusters (e.g. calendar months).

    Events cluster in time -- a market-wide drop hits hundreds of zones at once --
    so resampling single events would claim far more independence than exists.
    """
    if values.size == 0:
        return {"n": 0, "mean": float("nan"), "lo": float("nan"), "hi": float("nan"), "p": float("nan"), "clusters": 0}
    labels, inverse = np.unique(clusters, return_inverse=True)
    sums = np.bincount(inverse, weights=values)
    counts = np.bincount(inverse).astype(float)
    rng = np.random.default_rng(seed)
    pick = rng.integers(0, labels.size, size=(n_boot, labels.size))
    boot = sums[pick].sum(axis=1) / counts[pick].sum(axis=1)
    mean = float(values.mean())
    return {
        "n": int(values.size),
        "clusters": int(labels.size),
        "mean": mean,
        "lo": float(np.quantile(boot, 0.025)),
        "hi": float(np.quantile(boot, 0.975)),
        "p": float(min(1.0, 2.0 * min((boot <= 0).mean(), (boot >= 0).mean()))),
    }


def collapse_events(rows: list[dict[str, Any]], metrics: Sequence[str]) -> list[dict[str, Any]]:
    """One observation per (symbol, entry day, direction): nested zones share a trade.

    Several zones can first be reached by the same session; they are one
    purchase, so their outcomes are averaged rather than counted repeatedly.
    """
    groups: dict[tuple, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault((row["symbol"], row["entry_day"], row["zone_type"]), []).append(row)
    out = []
    for items in groups.values():
        merged = dict(items[0])
        merged["n_zones"] = len(items)
        for metric in metrics:
            values = [r[metric] for r in items if r.get(metric) is not None]
            merged[metric] = float(np.mean(values)) if values else None
        if "controls" in merged:
            merged["controls"] = [c for r in items for c in r["controls"]]
        out.append(merged)
    return out


def paired_difference(events: list[dict[str, Any]], metric: str, *, winsor: float = 0.01, n_boot: int = 2000, seed: int = 0) -> dict[str, float]:
    """Real minus mean-of-controls for `metric`, clustered by entry month.

    `events` are real events each carrying a `controls` list of control rows. An
    event with no valid control for the metric is dropped, which is reported via
    `n` rather than hidden.
    """
    diffs, months, real, ctrl = [], [], [], []
    for event in events:
        if event.get(metric) is None:
            continue
        baseline = [c[metric] for c in event["controls"] if c.get(metric) is not None]
        if not baseline:
            continue
        diffs.append(event[metric] - float(np.mean(baseline)))
        real.append(event[metric])
        ctrl.append(float(np.mean(baseline)))
        months.append(date.fromordinal(event["entry_day"] + _EPOCH.toordinal()).strftime("%Y-%m"))
    if not diffs:
        return cluster_bootstrap_mean(np.array([]), np.array([]))
    arr = winsorize(np.array(diffs), winsor)
    result = cluster_bootstrap_mean(arr, np.array(months), n_boot=n_boot, seed=seed)
    result["real_mean"] = float(winsorize(np.array(real), winsor).mean())
    result["control_mean"] = float(winsorize(np.array(ctrl), winsor).mean())
    result["real_median"] = float(np.median(real))
    return result
