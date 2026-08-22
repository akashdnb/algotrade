"""Signal parity: does tonight's recomputed weight history match last night's?

There is no separate "live" signal implementation to compare against the
backtester -- the nightly job calls the exact same functions the backtester
does (see `scripts/shadow_run.py`). So "signal parity" here means something
concrete and checkable: re-running the whole pipeline from scratch on an
extra day of data must not change the answer it already gave for any
*earlier* date. A change on an already-decided date is either a deliberate,
explained code fix or a silent regression -- the failure class the third and
fourth backtest audits (PROJECT.md §11n, §11q) both found by a human noticing
an implausible number, which this exists to catch automatically instead.
"""

from __future__ import annotations

from datetime import date

import polars as pl

DISCREPANCY_SCHEMA = {
    "date": pl.Date,
    "symbol": pl.String,
    "old_weight": pl.Float64,
    "new_weight": pl.Float64,
    "diff": pl.Float64,
}


def diff_weights(
    old: pl.DataFrame,
    new: pl.DataFrame,
    *,
    new_session: date,
    tol: float = 1e-9,
) -> pl.DataFrame:
    """Rows where `new` disagrees with `old` on a date before `new_session`.

    Args:
        old: (date, symbol, weight) persisted from the previous run.
        new: (date, symbol, weight) just recomputed, covering the same history
            plus (at least) `new_session`.
        new_session: The new tail session. Dates on or after this one are
            expected to be absent from `old` and are never flagged -- they are
            new decisions, not changed ones.
        tol: Weight differences at or below this are float noise, not a
            regression.

    Returns:
        (date, symbol, old_weight, new_weight, diff) -- one row per
        disagreement. `old_weight` is null for a symbol newly present on a
        past date; `new_weight` is null for one that vanished. `diff` is
        `new_weight - old_weight`, null when either side is null.
    """
    old_past = old.filter(pl.col("date") < new_session) if old.height else old
    new_past = new.filter(pl.col("date") < new_session) if new.height else new

    if old_past.is_empty() and new_past.is_empty():
        return pl.DataFrame(schema=DISCREPANCY_SCHEMA)

    joined = old_past.select(
        "date", "symbol", pl.col("weight").alias("old_weight")
    ).join(
        new_past.select("date", "symbol", pl.col("weight").alias("new_weight")),
        on=["date", "symbol"],
        how="full",
        coalesce=True,
    )

    mismatched = joined.filter(
        pl.col("old_weight").is_null()
        | pl.col("new_weight").is_null()
        | ((pl.col("new_weight") - pl.col("old_weight")).abs() > tol)
    )
    return mismatched.with_columns(
        (pl.col("new_weight") - pl.col("old_weight")).alias("diff")
    ).sort(["date", "symbol"])
