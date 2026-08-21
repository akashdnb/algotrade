"""Data-integrity checks derived from the PREV_CLOSE invariant (PROJECT.md §10.7).

NSE's `prev_close` gives two independent signals, and conflating them loses
both. They are checked separately here.

**Within a row** -- `prev_close` vs the day's own `close`:
    A large gap means the share count changed overnight. This is the corporate
    action detector. Note that `prev_close` is *not* adjusted on the ex-date, so
    on TATASTEEL's 1:10 split (2022-07-28) the row reads prev_close 959.40
    against close 100.35.

**Across rows** -- `prev_close[i]` vs `close[i-1]` for one symbol:
    NSE reports the last *traded* close, so this holds through holidays and
    suspensions alike. A break therefore means our panel is missing the true
    previous session, not that anything happened to the stock. Crucially a
    corporate action does *not* break this chain: on 2022-07-28 TATASTEEL's
    prev_close of 959.40 equals 2022-07-27's close exactly.

That second property gives missing sessions for free. A missing archive file
breaks the chain for *nearly every symbol at once*, whereas a genuine holiday
breaks nothing -- so no external trading calendar is needed to tell them apart.

Symbol renames fall out of the same invariant: the old symbol's final close
equals the new symbol's first prev_close (MINDAIND 529.05 on 2022-08-04 ->
UNOMINDA prev_close 529.05 on 2022-08-05).

All functions here are pure: DataFrames in, DataFrames out. No I/O.
"""

from __future__ import annotations

from typing import Final

import polars as pl

# Prices are quoted to 2dp, so anything under half a paisa is float noise.
PRICE_TOL: Final[float] = 0.005

# A corporate action changes price by a simple ratio. 25% is comfortably above
# normal single-day moves (circuit limits cap most names at 20%) while staying
# below the smallest realistic action, a 1:2 bonus at 50%.
ACTION_MIN_DEV: Final[float] = 0.25

# Fraction of symbols that must break on the same date before we call the
# preceding session missing rather than blaming the individual stocks.
MISSING_SESSION_MIN_FRAC: Final[float] = 0.50

_REQUIRED: Final[frozenset[str]] = frozenset(
    {"date", "symbol", "close", "prev_close"}
)


def _require(panel: pl.DataFrame) -> None:
    if missing := _REQUIRED - set(panel.columns):
        raise ValueError(f"panel missing required columns: {sorted(missing)}")


def sessions(panel: pl.DataFrame) -> pl.Series:
    """Every distinct trading date present in the panel, ascending."""
    return panel["date"].unique().sort()


def find_actions(
    panel: pl.DataFrame, min_dev: float = ACTION_MIN_DEV
) -> pl.DataFrame:
    """Corporate-action candidates, from the within-row prev_close/close gap.

    Returns one row per candidate with `implied_ratio` -- the factor the price
    moved by. This *detects* reliably but does not *derive* the factor
    precisely: TATASTEEL's 1:10 split implies 9.56, and MINDAIND's 1:1 bonus
    implies 1.952. Take the authoritative factor from the corporate-action
    feed and use this only to confirm it.

    A symbol's first row is skipped. On a listing day `prev_close` holds the
    IPO issue price rather than a prior market close, so the "gap" is the
    listing pop and not a share-count change -- SYRMA listed on 2022-08-26 at
    an issue price of 220 and closed at 310.50, which is a 41% move and no
    corporate action at all.
    """
    _require(panel)
    ranked = panel.sort(["symbol", "date"]).with_columns(
        pl.int_range(pl.len()).over("symbol").alias("_seq")
    )
    return (
        ranked.filter(
            (pl.col("_seq") > 0)
            & (pl.col("close") > 0)
            & (pl.col("prev_close") > 0)
            & ((pl.col("prev_close") / pl.col("close") - 1).abs() > min_dev)
        )
        .select(
            "date",
            "symbol",
            "prev_close",
            "close",
            (pl.col("prev_close") / pl.col("close")).alias("implied_ratio"),
        )
        .sort(["date", "symbol"])
    )


def unexplained_chain_breaks(
    panel: pl.DataFrame,
    chain_breaks: pl.DataFrame | None = None,
    missing: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Chain breaks not attributable to a known missing session.

    A missing archive file breaks the chain for nearly every symbol on the
    next session, which is loud but benign once identified. What matters is
    the residue: breaks with no such explanation are genuine anomalies in a
    single symbol's history.

    This residue count is the panel's data-quality score. It should be small.
    """
    _require(panel)
    if chain_breaks is None:
        chain_breaks = find_chain_breaks(panel)
    if chain_breaks.is_empty():
        return chain_breaks
    if missing is None:
        missing = find_missing_sessions(panel, chain_breaks)
    if missing.is_empty():
        return chain_breaks
    return chain_breaks.join(
        missing.select("date"), on="date", how="anti"
    ).sort(["date", "symbol"])


def find_chain_breaks(panel: pl.DataFrame, tol: float = PRICE_TOL) -> pl.DataFrame:
    """Rows where prev_close disagrees with the symbol's own previous close.

    Because NSE reports the last *traded* close, this is expected to hold
    across holidays and suspensions. A break means the panel lacks the real
    previous session. Corporate actions do not appear here -- see the module
    docstring.
    """
    _require(panel)
    ordered = panel.sort(["symbol", "date"]).with_columns(
        [
            pl.col("close").shift(1).over("symbol").alias("prior_close"),
            pl.col("date").shift(1).over("symbol").alias("prior_date"),
        ]
    )
    return (
        ordered.filter(
            pl.col("prior_close").is_not_null()
            & ((pl.col("prev_close") - pl.col("prior_close")).abs() > tol)
        )
        .select(
            "date",
            "symbol",
            "prior_date",
            pl.col("prior_close").alias("expected_prev_close"),
            pl.col("prev_close").alias("actual_prev_close"),
        )
        .sort(["date", "symbol"])
    )


def find_missing_sessions(
    panel: pl.DataFrame,
    chain_breaks: pl.DataFrame | None = None,
    min_frac: float = MISSING_SESSION_MIN_FRAC,
) -> pl.DataFrame:
    """Dates whose preceding session is absent from the archive.

    A missing file breaks the prev_close chain for nearly every symbol on the
    following session; a genuine holiday breaks none. Comparing the break rate
    against the day's symbol count separates the two without a calendar.
    """
    _require(panel)
    if chain_breaks is None:
        chain_breaks = find_chain_breaks(panel)
    if chain_breaks.is_empty():
        return pl.DataFrame(
            schema={
                "date": pl.Date,
                "n_broken": pl.UInt32,
                "n_symbols": pl.UInt32,
                "frac_broken": pl.Float64,
                "gap_after": pl.Date,
            }
        )

    per_day = panel.group_by("date").len().rename({"len": "n_symbols"})
    return (
        chain_breaks.group_by("date")
        .agg(
            pl.len().alias("n_broken"),
            # The prior session each symbol *did* see; the gap follows it.
            pl.col("prior_date").max().alias("gap_after"),
        )
        .join(per_day, on="date", how="left")
        .with_columns(
            (pl.col("n_broken") / pl.col("n_symbols")).alias("frac_broken")
        )
        .filter(pl.col("frac_broken") >= min_frac)
        .select("date", "n_broken", "n_symbols", "frac_broken", "gap_after")
        .sort("date")
    )


def _symbol_spans(panel: pl.DataFrame) -> pl.DataFrame:
    """First and last session per symbol, with the prices at each boundary."""
    ordered = panel.sort(["symbol", "date"])
    return ordered.group_by("symbol").agg(
        pl.col("date").first().alias("first_date"),
        pl.col("date").last().alias("last_date"),
        pl.col("prev_close").first().alias("first_prev_close"),
        pl.col("close").last().alias("last_close"),
    )


def find_renames(panel: pl.DataFrame, tol: float = PRICE_TOL) -> pl.DataFrame:
    """Symbol renames, matched on price continuity across the changeover.

    A rename shows up as one symbol ending and another beginning on the very
    next session, with the new symbol's first `prev_close` equal to the old
    one's final `close`.

    Matches are only reported when unambiguous. If two symbols disappear and
    two appear on the same boundary at similar prices, all four are withheld
    rather than guessed at -- `ambiguous` records how many rivals a pair had.

    Renames that coincide with a corporate action are *not* found here: the
    price is adjusted on one side of the boundary but not the other, so exact
    continuity does not hold. Those surface via `find_actions` instead.
    """
    _require(panel)
    all_sessions = sessions(panel)
    # Map each session to the one after it, so "ended on D, began on D+1" is
    # expressible without assuming calendar adjacency.
    next_session = dict(
        zip(all_sessions[:-1].to_list(), all_sessions[1:].to_list())
    )

    spans = _symbol_spans(panel)
    last_seen = all_sessions[-1]
    first_seen = all_sessions[0]

    gone = spans.filter(pl.col("last_date") != last_seen).with_columns(
        pl.col("last_date")
        .replace_strict(next_session, default=None, return_dtype=pl.Date)
        .alias("resume_date")
    )
    arrived = spans.filter(pl.col("first_date") != first_seen)

    pairs = gone.join(
        arrived,
        left_on="resume_date",
        right_on="first_date",
        how="inner",
        suffix="_new",
    ).filter(
        (pl.col("last_close") - pl.col("first_prev_close_new")).abs() <= tol
    )
    if pairs.is_empty():
        return pl.DataFrame(
            schema={
                "old_symbol": pl.String,
                "new_symbol": pl.String,
                "last_date": pl.Date,
                "first_date": pl.Date,
                "price": pl.Float64,
                "ambiguous": pl.UInt32,
            }
        )

    # Withhold any pairing that is not one-to-one on both sides.
    counts = pairs.with_columns(
        pl.len().over(["last_date", "symbol"]).alias("n_for_old"),
        pl.len().over(["last_date", "symbol_new"]).alias("n_for_new"),
    )
    return (
        counts.select(
            pl.col("symbol").alias("old_symbol"),
            pl.col("symbol_new").alias("new_symbol"),
            "last_date",
            pl.col("resume_date").alias("first_date"),
            pl.col("last_close").alias("price"),
            (pl.max_horizontal("n_for_old", "n_for_new") - 1)
            .cast(pl.UInt32)
            .alias("ambiguous"),
        )
        .filter(pl.col("ambiguous") == 0)
        .sort("last_date")
    )


def find_delistings(panel: pl.DataFrame, renames: pl.DataFrame | None = None):
    """Symbols that stop trading and are not explained by a rename.

    These must be *kept* in history -- excluding them is exactly the
    survivorship bias this archive was chosen to avoid. Reported so the count
    can be sanity-checked, not so they can be removed.
    """
    _require(panel)
    if renames is None:
        renames = find_renames(panel)
    spans = _symbol_spans(panel)
    last_seen = sessions(panel)[-1]
    renamed = set(renames["old_symbol"].to_list()) if not renames.is_empty() else set()
    return (
        spans.filter(
            (pl.col("last_date") != last_seen)
            & ~pl.col("symbol").is_in(list(renamed))
        )
        .select("symbol", "first_date", "last_date", pl.col("last_close"))
        .sort("last_date")
    )
