"""Feature library. All backward-looking, all verified by the look-ahead harness.

Every feature here is derivable from the bhavcopy panel alone. Two of §4's
candidates are **not** included, deliberately:

* **Quality / profitability** needs fundamentals (margins, leverage, earnings
  stability). We have none, and no price-only proxy is honest enough to call
  quality.
* **PEAD** needs earnings dates. The corporate-action feed carries AGM and
  dividend dates but not results dates, so the announcement window cannot be
  located. `volume_shock` is offered as a *related* feature, not as PEAD.

Naming a feature after an effect it cannot measure is how a research programme
starts lying to itself, so the gap is stated rather than papered over.

Every feature is registered in `FEATURES`, and `tests/test_features.py` runs the
truncation detector over **all** of them -- so a new feature cannot be added
without being checked for look-ahead.
"""

from __future__ import annotations

from typing import Callable, Final

import polars as pl

FeatureFn = Callable[..., pl.Expr]


def _ordered(panel: pl.DataFrame) -> pl.DataFrame:
    return panel.sort(["symbol", "date"])


# --- return-based ------------------------------------------------------------


def momentum(lookback: int = 252, skip: int = 21) -> pl.Expr:
    """Cumulative return over `lookback`, excluding the most recent `skip`.

    The skip is not cosmetic: 12-1 momentum omits the last month precisely
    because short-horizon reversal contaminates it, and the two effects have
    opposite sign. Including it mixes them and measures neither.
    """
    past = pl.col("close").shift(skip).over("symbol")
    older = pl.col("close").shift(skip + lookback).over("symbol")
    return (past / older - 1).alias("feature")


def reversal(lookback: int = 5) -> pl.Expr:
    """Return over the last `lookback` sessions.

    Kept for reference: §11a measured this as structurally dead at retail cost,
    because its alpha decays within days and therefore requires turnover it
    cannot afford.
    """
    return (pl.col("close") / pl.col("close").shift(lookback).over("symbol") - 1).alias(
        "feature"
    )


def realised_vol(lookback: int = 60) -> pl.Expr:
    """Trailing volatility of daily returns -- the low-volatility factor.

    Slow-moving, so turnover is naturally low, which is what makes it viable at
    ~30 bps (§11a).
    """
    daily = pl.col("close") / pl.col("close").shift(1).over("symbol") - 1
    return daily.rolling_std(window_size=lookback).over("symbol").alias("feature")


def distance_from_high(lookback: int = 252) -> pl.Expr:
    """How far below its trailing high the price sits, as a fraction."""
    high = pl.col("close").rolling_max(window_size=lookback).over("symbol")
    return (pl.col("close") / high - 1).alias("feature")


def overnight_gap() -> pl.Expr:
    """Open versus the previous close.

    Available to a signal only *after* the open, so a strategy using it fills at
    the next open -- one full session later than a close-based signal.
    """
    return (
        pl.col("open") / pl.col("close").shift(1).over("symbol") - 1
    ).alias("feature")


# --- microstructure, India-specific ------------------------------------------


def delivery_pct(lookback: int = 20) -> pl.Expr:
    """Trailing mean delivery percentage.

    No US analogue, so it is largely absent from the Western literature this
    project otherwise mines -- which makes it *less likely to be crowded* (§4).
    Low delivery suggests speculative churn, high suggests genuine accumulation.

    Null before ~2020 in this archive (§10.8), so any hypothesis using it has a
    shorter usable history and therefore a higher noise threshold (§8.2).
    """
    return (
        pl.col("deliv_pct").rolling_mean(window_size=lookback).over("symbol")
    ).alias("feature")


def delivery_shock(lookback: int = 20) -> pl.Expr:
    """Today's delivery percentage relative to its own trailing mean."""
    trailing = pl.col("deliv_pct").rolling_mean(window_size=lookback).over("symbol")
    return (pl.col("deliv_pct") / trailing - 1).alias("feature")


def volume_shock(lookback: int = 20) -> pl.Expr:
    """Turnover relative to its trailing median.

    **Not PEAD.** Without earnings dates this cannot isolate an announcement
    window; it only detects unusual activity, whatever the cause.
    """
    trailing = pl.col("turnover").rolling_median(window_size=lookback).over("symbol")
    return (pl.col("turnover") / trailing - 1).alias("feature")


def vwap_deviation() -> pl.Expr:
    """Close versus the session's own VWAP -- intraday buying or selling pressure."""
    return (pl.col("close") / pl.col("vwap") - 1).alias("feature")


FEATURES: Final[dict[str, FeatureFn]] = {
    "momentum": momentum,
    "reversal": reversal,
    "realised_vol": realised_vol,
    "distance_from_high": distance_from_high,
    "overnight_gap": overnight_gap,
    "delivery_pct": delivery_pct,
    "delivery_shock": delivery_shock,
    "volume_shock": volume_shock,
    "vwap_deviation": vwap_deviation,
}


def add(panel: pl.DataFrame, kind: str, **params) -> pl.DataFrame:
    """Add a named feature as a `feature` column.

    Raises:
        KeyError: unknown feature name -- listing the available ones, since a
            typo in a config file should not fail silently.
    """
    if kind not in FEATURES:
        raise KeyError(f"unknown feature {kind!r}; available: {sorted(FEATURES)}")
    return _ordered(panel).with_columns(FEATURES[kind](**params))
