"""Apply corporate-action factors to produce an adjusted panel.

Adjustment happens **on read** and never mutates the raw panel (PROJECT.md §5).
A bad factor is then a bug you can fix by re-deriving, not permanent corruption
of the archive -- which is exactly how eod2 destroys history (§10.3).

Back-adjustment convention: a row's prices are divided by the product of every
factor whose ex-date is *strictly after* that row. On the ex-date itself the
market price is already post-split, so that action's own factor is excluded.

Field handling is asymmetric (§10.6), and getting it wrong is silent:

    open/high/low/close/vwap   divide   by the cumulative factor
    volume, deliv_qty          multiply by the cumulative factor
    turnover                   unchanged -- rupee value is invariant
    deliv_pct                  unchanged -- it is a ratio

`prev_close` is deliberately **recomputed**, not scaled. NSE leaves it
unadjusted on the ex-date -- that is the discontinuity the integrity checks rely
on -- so scaling it by the row's own divisor would leave it wrong by exactly the
factor. After adjustment the only self-consistent definition is the previous
adjusted close, so that is what it becomes.

Dividends are out of scope here. The feed carries the amounts, but whether to
build a total-return series is a strategy decision rather than a data one, and
it changes what every signal measures.

Pure module: DataFrames in, DataFrames out. No I/O.
"""

from __future__ import annotations

from typing import Final

import polars as pl

PRICE_COLS: Final[tuple[str, ...]] = ("open", "high", "low", "close", "vwap")
QUANTITY_COLS: Final[tuple[str, ...]] = ("volume", "deliv_qty")
# Left alone on purpose; listed so the intent is explicit rather than implied.
INVARIANT_COLS: Final[tuple[str, ...]] = ("turnover", "deliv_pct")


def factors_from_reconciliation(
    result: dict[str, pl.DataFrame],
    *,
    include_mismatched: bool = False,
) -> pl.DataFrame:
    """Build an adjustment table from `reconcile.reconcile` output.

    Only actions corroborated by an observed price move are applied by default.
    `factor_mismatch` rows are excluded unless asked for: the feed and the
    prices disagree by more than tolerance there, so one of them is wrong and
    applying either silently is worse than leaving the discontinuity visible.

    Note that `action_no_move` rows are never applied. A feed action with no
    corresponding price move usually means a wrong ex-date, and adjusting on the
    wrong date introduces a discontinuity rather than removing one.
    """
    frames = [result["matched"]]
    if include_mismatched:
        frames.append(result["factor_mismatch"])
    keep = [f for f in frames if not f.is_empty()]
    if not keep:
        return pl.DataFrame(
            schema={"symbol": pl.String, "ex_date": pl.Date, "factor": pl.Float64}
        )
    return (
        pl.concat(keep, how="diagonal")
        .select(
            pl.col("symbol"),
            pl.col("date").alias("ex_date"),
            pl.col("price_factor").alias("factor"),
        )
        .drop_nulls()
        .unique(subset=["symbol", "ex_date"], keep="first")
        .sort(["symbol", "ex_date"])
    )


def cumulative_divisors(panel: pl.DataFrame, factors: pl.DataFrame) -> pl.DataFrame:
    """Attach each row's `divisor`: the product of all later factors.

    Computed as `total / prefix`, where `prefix` is the running product of
    factors up to and including the row's date. That makes the ex-date row
    exclude its own factor, which is correct because the market price on that
    day is already adjusted.
    """
    if factors.is_empty():
        return panel.with_columns(pl.lit(1.0).alias("divisor"))

    ordered = factors.sort(["symbol", "ex_date"]).with_columns(
        pl.col("factor").cum_prod().over("symbol").alias("prefix")
    )
    totals = ordered.group_by("symbol").agg(
        pl.col("prefix").last().alias("total")
    )

    # Sorted by (symbol, date) so each `by` group is ordered on the asof key.
    # Polars cannot verify that when `by` is given and warns unconditionally,
    # so the warning is silenced rather than left to alarm callers.
    import warnings

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Sortedness of columns")
        joined = (
            panel.sort(["symbol", "date"])
            .join_asof(
                ordered.select("symbol", "ex_date", "prefix").sort(
                    ["symbol", "ex_date"]
                ),
                left_on="date",
                right_on="ex_date",
                by="symbol",
                strategy="backward",
            )
            .join(totals, on="symbol", how="left")
        )

    return joined.with_columns(
        (
            pl.col("total").fill_null(1.0)
            # No prior action means nothing has been applied yet, so prefix = 1.
            / pl.col("prefix").fill_null(1.0)
        ).alias("divisor")
    ).drop("ex_date", "prefix", "total", strict=False)


def adjust(
    panel: pl.DataFrame,
    factors: pl.DataFrame,
    *,
    keep_raw: bool = False,
) -> pl.DataFrame:
    """Return the panel with prices and quantities back-adjusted.

    Args:
        panel: Raw canonical panel from `io.bhavcopy`.
        factors: (symbol, ex_date, factor). Symbols must be the *historical*
            names used in the panel -- `reconcile` resolves the feed's
            today-symbols, so pass its output through
            `factors_from_reconciliation`.
        keep_raw: Also emit `close_raw` and `divisor`, so an adjustment can be
            audited or undone without re-reading the archive.

    Raises:
        ValueError: if a factor is non-positive, which would flip prices.
    """
    if not factors.is_empty():
        bad = factors.filter(
            pl.col("factor").is_null() | (pl.col("factor") <= 0)
        )
        if bad.height:
            raise ValueError(f"{bad.height} non-positive factor(s): {bad.head(3)}")

    out = cumulative_divisors(panel, factors)

    scaled = [
        (pl.col(c) / pl.col("divisor")).alias(c)
        for c in PRICE_COLS
        if c in out.columns
    ] + [
        (pl.col(c) * pl.col("divisor")).alias(c)
        for c in QUANTITY_COLS
        if c in out.columns
    ]
    if keep_raw:
        scaled.append(pl.col("close").alias("close_raw"))

    out = out.with_columns(scaled).sort(["symbol", "date"])

    # prev_close must be recomputed rather than scaled -- see module docstring.
    # Note this spans any gap in a symbol's history, so a row following a long
    # suspension carries the pre-suspension close.
    out = out.with_columns(
        pl.col("close").shift(1).over("symbol").alias("prev_close")
    )

    return out if keep_raw else out.drop("divisor")


def unadjusted_windows(
    result: dict[str, pl.DataFrame],
    *,
    sessions: pl.Series | list | None = None,
    min_ratio: float = 1.75,
    before: int = 1,
    after: int = 1,
) -> pl.DataFrame:
    """(symbol, date) rows to drop around unexplained price moves (§10.6).

    **Prefer this to `unadjusted_symbols`.** Excluding a symbol's entire
    history because of one unexplained day removes 243 of 1,430 universe names
    (17%) -- which reintroduces exactly the selection bias this project exists
    to avoid. Dropping a few days around each event is just as safe and vastly
    less destructive.

    `min_ratio` defaults to 1.75 rather than 1.25 because most moves in the
    1.25-1.45 band are genuine: circuit limits reach 20%, and illiquid names
    move further. 1,093 of 1,800 unexplained moves sit in that band, and
    treating them as data errors would discard real returns. Above ~1.75 the
    moves cluster at bonus and split ratios, so those are the suspicious ones.

    Args:
        before / after: Sessions of padding either side. A day is dropped, not
            a calendar day, so `after=1` also covers the row whose recomputed
            `prev_close` straddles the event.
    """
    # BOTH unexplained moves and factor disagreements must be masked. A
    # `factor_mismatch` row is a discontinuity the feed and the prices disagree
    # about, so it is not adjusted -- and if it is not masked either it stays
    # fully live in the panel. That is how BAJFINANCE's -89.9% survived.
    parts = [
        f.select("date", "symbol", "implied_ratio")
        for f in (result.get("move_no_action"), result.get("factor_mismatch"))
        if f is not None and not f.is_empty()
    ]
    if not parts:
        return pl.DataFrame(schema={"symbol": pl.String, "date": pl.Date})
    moves = pl.concat(parts).unique()

    ratio = pl.col("implied_ratio")
    flagged = moves.filter((ratio >= min_ratio) | (ratio <= 1 / min_ratio))
    if flagged.is_empty():
        return pl.DataFrame(schema={"symbol": pl.String, "date": pl.Date})

    offsets = list(range(-before, after + 1))
    if sessions is None:
        # Calendar-day fallback. Documented as wrong: a Monday event drops the
        # Sunday (no row) and leaves Friday live, and a Friday event drops
        # Saturday and leaves Monday live -- so padding evaporated for ~1/5 of
        # events plus every holiday-adjacent one. Pass `sessions` to avoid it.
        return (
            flagged.select("symbol", "date")
            .with_columns(pl.lit(offsets).alias("offset"))
            .explode("offset", empty_as_null=True)
            .with_columns(
                (pl.col("date") + pl.duration(days=pl.col("offset"))).alias("date")
            )
            .drop("offset")
            .unique()
            .sort(["symbol", "date"])
        )

    grid = pl.DataFrame(
        {"date": pl.Series(sessions, dtype=pl.Date).unique().sort()}
    ).with_row_index("_idx")
    return (
        flagged.select("symbol", "date")
        .join(grid, on="date", how="inner")
        .with_columns(pl.lit(offsets).alias("offset"))
        .explode("offset", empty_as_null=True)
        .with_columns((pl.col("_idx") + pl.col("offset")).alias("_idx"))
        .drop("date", "offset")
        .join(grid, on="_idx", how="inner")
        .select("symbol", "date")
        .unique()
        .sort(["symbol", "date"])
    )


def drop_windows(panel: pl.DataFrame, windows: pl.DataFrame) -> pl.DataFrame:
    """Remove flagged (symbol, date) rows from a panel."""
    if windows.is_empty():
        return panel
    return panel.join(windows, on=["symbol", "date"], how="anti")


def unadjusted_symbols(
    result: dict[str, pl.DataFrame], min_ratio: float = 1.75
) -> list[str]:
    """Symbols with an unexplained price move.

    Kept for reporting -- knowing *which* names are affected is useful. For
    actually filtering data prefer `unadjusted_windows`, which does not discard
    a symbol's entire history over one bad day.
    """
    moves = result["move_no_action"]
    if moves.is_empty():
        return []
    ratio = pl.col("implied_ratio")
    return (
        moves.filter((ratio >= min_ratio) | (ratio <= 1 / min_ratio))
        .get_column("symbol")
        .unique()
        .sort()
        .to_list()
    )
