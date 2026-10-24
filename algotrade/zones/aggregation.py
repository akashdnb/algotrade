from __future__ import annotations

from datetime import date

import polars as pl

from .config import Timeframe


def _aggregate(daily: pl.DataFrame, period: str) -> pl.DataFrame:
    if daily.is_empty():
        return daily
    required = {"date", "symbol", "open", "high", "low", "close", "volume"}
    missing = required - set(daily.columns)
    if missing:
        raise ValueError(f"missing OHLCV columns: {sorted(missing)}")
    return (
        daily.sort(["symbol", "date"])
        .with_columns(pl.col("date").dt.truncate(period).alias("_period"))
        .group_by(["symbol", "_period"], maintain_order=True)
        .agg([
            pl.col("date").last().alias("date"),
            pl.col("open").first().alias("open"),
            pl.col("high").max().alias("high"),
            pl.col("low").min().alias("low"),
            pl.col("close").last().alias("close"),
            pl.col("volume").sum().alias("volume"),
            *([pl.col("turnover").sum().alias("turnover")] if "turnover" in daily.columns else []),
        ])
        .drop("_period")
        .sort(["symbol", "date"])
    )


def aggregate_to_weekly(daily: pl.DataFrame) -> pl.DataFrame:
    return _aggregate(daily, "1w")


def aggregate_to_monthly(daily: pl.DataFrame) -> pl.DataFrame:
    return _aggregate(daily, "1mo")


def split_completed_current(
    candles: pl.DataFrame, as_of_date: date, timeframe: Timeframe
) -> tuple[pl.DataFrame, pl.DataFrame | None]:
    if candles.is_empty():
        return candles, None
    visible = candles.filter(pl.col("date") <= pl.lit(as_of_date))
    if visible.is_empty():
        return visible, None
    if timeframe == Timeframe.DAILY:
        completed = visible.filter(pl.col("date") < pl.lit(as_of_date))
        current = visible.filter(pl.col("date") == pl.lit(as_of_date))
    else:
        period = "1w" if timeframe == Timeframe.WEEKLY else "1mo"
        visible = visible.with_columns(pl.col("date").dt.truncate(period).alias("_period"))
        current_period = visible.select(pl.col("_period").max()).item()
        if current_period is None:
            return visible.drop("_period"), None
        completed = visible.filter(pl.col("_period") < pl.lit(current_period)).drop("_period")
        current = visible.filter(pl.col("_period") == pl.lit(current_period)).drop("_period")
    return completed, current
