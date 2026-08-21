"""Reconcile the corporate-action feed against observed price discontinuities.

This is the mandatory cross-check from PROJECT.md §10.6. Neither source is
sufficient alone:

  * The **feed** gives the authoritative factor but uses *today's* symbol, so a
    naive join on symbol silently drops every action on a since-renamed stock
    (§10.6 Trap 2 -- observed for MINDAIND/UNOMINDA and SONAMCLOCK/SONAMLTD).
  * The **price move** is present in our own data under the historical symbol,
    but only implies the factor to within ~15%.

Joining them needs the rename chains as a bridge (§10.7). Four outcomes:

    matched          factor and observed move agree
    factor_mismatch  both present, but they disagree -- review
    action_no_move   feed says an action, prices show nothing -- bad row or date
    move_no_action   prices moved, feed says nothing -- MISSING ACTION

The last is the dangerous one. A missed action leaves a phantom crash of tens of
percent in the history, and because a cross-sectional strategy ranks on extremes
that phantom lands at the very top of a "buy the losers" screen. It is the
failure this whole module exists to catch, and it should halt ingestion.

Pure module: DataFrames in, DataFrames out. No I/O.
"""

from __future__ import annotations

from typing import Final

import polars as pl

# Calibrated against real events: GMMPFAUDLR's bonus ex-date moved ~6% by
# itself (factor 3.0 vs implied 2.830), so the observed ratio cannot pin the
# factor tightly. Loose enough for that, tight enough to catch a 2x error --
# which is what the faceVal trap (§10.6 Trap 1) would produce.
FACTOR_TOL: Final[float] = 0.15


def resolve_chains(renames: pl.DataFrame) -> pl.DataFrame:
    """Map every historical symbol to the name it trades under today.

    Renames compose -- a stock may be renamed more than once -- so old->new is
    followed to a fixed point. Cycles cannot occur in valid data but are
    guarded against anyway rather than looping forever.

    Returns (historical_symbol, current_symbol), including identity rows for
    symbols that were never renamed downstream.
    """
    if renames.is_empty():
        return pl.DataFrame(
            schema={"historical_symbol": pl.String, "current_symbol": pl.String}
        )

    successor = dict(
        zip(renames["old_symbol"].to_list(), renames["new_symbol"].to_list())
    )
    resolved: dict[str, str] = {}
    for start in successor:
        seen = {start}
        node = start
        while (nxt := successor.get(node)) is not None and nxt not in seen:
            seen.add(nxt)
            node = nxt
        resolved[start] = node

    return pl.DataFrame(
        {
            "historical_symbol": list(resolved),
            "current_symbol": [resolved[k] for k in resolved],
        }
    )


def to_current(
    frame: pl.DataFrame, chains: pl.DataFrame, symbol_col: str = "symbol"
) -> pl.DataFrame:
    """Add a `current_symbol` column, so historical rows join to today's names.

    Symbols absent from the chains were never renamed, so they map to
    themselves.
    """
    joined = frame.join(
        chains, left_on=symbol_col, right_on="historical_symbol", how="left"
    )
    return joined.with_columns(
        pl.coalesce(pl.col("current_symbol"), pl.col(symbol_col)).alias(
            "current_symbol"
        )
    ).drop("historical_symbol", strict=False)


def reconcile(
    discontinuities: pl.DataFrame,
    actions: pl.DataFrame,
    renames: pl.DataFrame,
    *,
    tol: float = FACTOR_TOL,
) -> dict[str, pl.DataFrame]:
    """Match detected price moves against feed actions.

    Args:
        discontinuities: From `integrity.find_actions` -- (date, symbol,
            implied_ratio), keyed by *historical* symbol.
        actions: Feed rows carrying `symbol` (today's), `ex_date`, and
            `price_factor` from `actions.classify`. Rows with a null
            `price_factor` imply no price adjustment and are ignored here;
            demergers land in `move_no_action` by design, since no ratio is
            derivable from their subject.
        renames: From `integrity.find_renames`, used to bridge the two.

    Returns:
        A dict of four frames: matched, factor_mismatch, action_no_move,
        move_no_action.
    """
    chains = resolve_chains(renames)

    moves = to_current(
        discontinuities.select("date", "symbol", "implied_ratio"), chains
    )
    feed = (
        actions.filter(pl.col("price_factor").is_not_null())
        .select(
            pl.col("symbol").alias("current_symbol"),
            pl.col("ex_date").alias("date"),
            "price_factor",
            pl.col("subject").alias("action_subject"),
        )
        .unique(subset=["current_symbol", "date"], keep="first")
    )

    paired = moves.join(feed, on=["current_symbol", "date"], how="full", coalesce=True)

    both = paired.filter(
        pl.col("implied_ratio").is_not_null() & pl.col("price_factor").is_not_null()
    ).with_columns(
        (pl.col("implied_ratio") / pl.col("price_factor") - 1).abs().alias("deviation")
    )

    return {
        "matched": both.filter(pl.col("deviation") <= tol).sort("date"),
        "factor_mismatch": both.filter(pl.col("deviation") > tol).sort(
            "deviation", descending=True
        ),
        "action_no_move": paired.filter(
            pl.col("implied_ratio").is_null() & pl.col("price_factor").is_not_null()
        ).sort("date"),
        "move_no_action": paired.filter(
            pl.col("price_factor").is_null() & pl.col("implied_ratio").is_not_null()
        ).sort("date"),
    }


def summarise(result: dict[str, pl.DataFrame]) -> pl.DataFrame:
    """One row per outcome with counts, for the data-quality report."""
    order = ["matched", "factor_mismatch", "action_no_move", "move_no_action"]
    return pl.DataFrame(
        {
            "outcome": order,
            "count": [result[k].height for k in order],
        }
    )
