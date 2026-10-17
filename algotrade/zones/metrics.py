from __future__ import annotations

import polars as pl


def candle_metrics(candles: pl.DataFrame) -> pl.DataFrame:
    return candles.with_columns([
        (pl.col("high") - pl.col("low")).alias("range"),
        (pl.col("close") - pl.col("open")).abs().alias("body"),
        ((pl.col("close") - pl.col("open")).abs() / (pl.col("high") - pl.col("low")).replace(0, None)).fill_null(0).alias("body_ratio"),
        (pl.col("high") - pl.max_horizontal("open", "close")).alias("upper_wick"),
        (pl.min_horizontal("open", "close") - pl.col("low")).alias("lower_wick"),
    ])


def atr(candles: pl.DataFrame, period: int) -> pl.DataFrame:
    if period < 1:
        raise ValueError("ATR period must be >= 1")
    out = candles.sort(["symbol", "date"]).with_columns(
        pl.max_horizontal("high", "low", (pl.col("high") - pl.col("low"))).alias("_range_raw")
    )
    out = out.with_columns(
        pl.max_horizontal(
            pl.col("high") - pl.col("low"),
            (pl.col("high") - pl.col("close").shift(1).over("symbol")).abs(),
            (pl.col("low") - pl.col("close").shift(1).over("symbol")).abs(),
        ).alias("_tr")
    )
    # Wilder ATR: seed with the simple mean of the first `period` true ranges,
    # then recursively smooth. The recurrence is expressed with map_groups so
    # each symbol has an independent state.
    def calc(group: pl.DataFrame) -> pl.DataFrame:
        vals = group["_tr"].to_list()
        result = [None] * len(vals)
        if len(vals) >= period:
            seed = sum(float(x) for x in vals[:period]) / period
            result[period - 1] = seed
            prev = seed
            for i in range(period, len(vals)):
                prev = ((period - 1) * prev + float(vals[i])) / period
                result[i] = prev
        return group.with_columns(pl.Series("atr", result))
    return out.group_by("symbol", maintain_order=True).map_groups(calc).drop(["_range_raw", "_tr"])
