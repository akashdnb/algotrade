"""Point-in-time tradeable universe selection (PROJECT.md §10.4).

The rule is fixed and must not be tuned against results: **top 300 by trailing
20-session median turnover, monthly rebalance, with a floor of Rs 5 crore/day.**
Trying 200/300/500 and keeping whichever backtests best spends trials (§8.2)
and introduces a fresh overfit, so the numbers live in config and stay there.

Turnover is used rather than share volume for two reasons. Volume in shares is
meaningless across price levels, and turnover is invariant to splits -- so the
universe needs no corporate-action adjustment at all (§10.6).

Liquidity alone is not enough. A stock that trades 5 sessions out of 20 can
show a healthy median turnover on those 5, so `coverage` measures how densely a
symbol actually traded over the window and thin traders are excluded. Without
it, sporadic illiquid names enter the universe and their fills are fiction.

Pure module: DataFrames in, DataFrames out. No I/O.
"""

from __future__ import annotations

from datetime import date
from typing import Final, Iterable

import polars as pl

LOOKBACK: Final[int] = 20
UNIVERSE_SIZE: Final[int] = 300
MIN_TURNOVER: Final[float] = 5e7  # Rs 5 crore/day; below this, fills are fiction
MIN_COVERAGE: Final[float] = 0.80  # must have traded 80% of the window's sessions


def with_session_index(panel: pl.DataFrame) -> pl.DataFrame:
    """Add a dense session ordinal, so gaps in trading are measurable.

    Row position within a symbol is not enough: a symbol that skips sessions
    would look contiguous. The ordinal is over all sessions in the panel.
    """
    sessions = (
        panel.select("date")
        .unique()
        .sort("date")
        .with_row_index("session_idx")
    )
    return panel.join(sessions, on="date", how="left")


def liquidity(
    panel: pl.DataFrame,
    lookback: int = LOOKBACK,
) -> pl.DataFrame:
    """Add trailing median turnover and window coverage per symbol-date.

    `coverage` is `lookback` divided by the span of sessions the symbol's last
    `lookback` rows actually cover. A dense trader scores ~1.0; one that traded
    5 of the last 20 sessions scores ~0.25.

    Both are computed from data up to and including the row's own date, which
    is point-in-time correct for our timing: the universe is fixed on the close
    of D and traded at the open of D+1.
    """
    if "session_idx" not in panel.columns:
        panel = with_session_index(panel)

    # coverage is bounded by 1.0 given unique (date, symbol). A duplicate would
    # let it exceed 1.0 and admit a name on inflated density, so assert instead
    # of trusting upstream.
    if panel.height != panel.select("date", "symbol").unique().height:
        raise ValueError("panel has duplicate (date, symbol) rows")

    ordered = panel.sort(["symbol", "session_idx"])
    span = pl.col("session_idx") - pl.col("session_idx").shift(
        lookback - 1
    ).over("symbol") + 1

    return ordered.with_columns(
        pl.col("turnover")
        .rolling_median(window_size=lookback)
        .over("symbol")
        .alias("adtv"),
        (lookback / span).alias("coverage"),
    )


def select(
    panel: pl.DataFrame,
    as_of: date,
    *,
    size: int = UNIVERSE_SIZE,
    min_turnover: float = MIN_TURNOVER,
    min_coverage: float = MIN_COVERAGE,
    exclude: set[str] | None = None,
) -> pl.DataFrame:
    """The tradeable universe as of one date, ranked most liquid first.

    Args:
        panel: Must already carry `adtv` and `coverage` from `liquidity`.
        as_of: Selection date. Only this date's rows are considered, and their
            `adtv` looks back only at history, so there is no look-ahead.
        exclude: Symbols to drop -- ETFs belong here. They sit in
            `SERIES == 'EQ'` alongside ordinary shares (§10.8), so nothing in
            the bhavcopy distinguishes them.
    """
    if "adtv" not in panel.columns:
        raise ValueError("call liquidity() before select()")

    day = panel.filter(
        (pl.col("date") == as_of)
        & pl.col("adtv").is_not_null()
        & (pl.col("adtv") >= min_turnover)
        & (pl.col("coverage") >= min_coverage)
    )
    if exclude:
        day = day.filter(~pl.col("symbol").is_in(list(exclude)))
    return day.sort("adtv", descending=True).head(size)


def rebalance_dates(panel: pl.DataFrame) -> list[date]:
    """Last session of each calendar month -- the monthly rebalance schedule.

    Weekly rebalancing adds turnover for no benefit at this horizon, and every
    extra rebalance is a real cost: DP charges are flat per scrip per sell
    (§3), so churn is expensive at Rs 2L.
    """
    return (
        panel.select("date")
        .unique()
        .sort("date")
        .group_by_dynamic("date", every="1mo")
        .agg(pl.col("date").max().alias("last"))
        .get_column("last")
        .to_list()
    )


# ETFs and liquid funds sit in SERIES == 'EQ' with ordinary shares (§10.8), and
# nothing in the bhavcopy distinguishes them. LIQUIDBEES is a money-market fund
# and is therefore ALWAYS the lowest-volatility name in a liquidity-ranked
# universe -- it was the single most-held long in lowvol_v1, every session.
DEFAULT_EXCLUSIONS: frozenset[str] = frozenset(
    {
        "LIQUIDBEES", "NIFTYBEES", "BANKBEES", "JUNIORBEES", "GOLDBEES",
        "SILVERBEES", "ICICILIQ", "LIQUIDCASE", "LIQUID", "ABSLLIQUID",
        "HDFCLIQUID", "SETFNIF50", "SETFNIFBK", "SETFGOLD", "KOTAKGOLD",
        "KOTAKLIQUID", "AXISGOLD", "HDFCMFGETF", "LICMFGOLD", "IVZINGOLD",
        "QGOLDHALF", "GOLDSHARE", "MOLOWVOL", "MOMOMENTUM", "ICICIMOM30",
        "ICICIB22", "CPSEETF", "BSLNIFTY", "UTINIFTETF", "MON100",
        "MAFANG", "MASPTOP50", "HNGSNGBEES", "NIF100BEES", "NIFTYIETF",
        # Found live on 2026-08-20: three precious-metal ETFs had entered the
        # top 60, two of them inside the entry threshold of 30. A hand-kept
        # denylist cannot track new listings, and metal ETFs rank high on
        # momentum exactly when gold and silver run -- so the failure is
        # correlated with when it matters most. See `looks_like_fund` below.
        "SILVERIETF", "HDFCSILVER", "GOLDIETF", "HDFCGOLD", "AXISILVER",
        "SILVERETF", "GOLDETF", "SILVERADD", "GOLDCASE", "SILVERCASE",
        "TATAGOLD", "UTISILVER", "UTIGOLD", "MIRAEASSET", "BSLGOLDETF",
    }
)

#: Symbol suffixes that identify a fund structurally rather than by name.
#: Nothing in the bhavcopy distinguishes an ETF from a share -- both trade in
#: SERIES 'EQ' (§10.8) -- so this is the closest thing to a structural rule the
#: data supports, and unlike the denylist above it needs no maintenance.
_FUND_SUFFIXES: Final[tuple[str, ...]] = ("BEES", "ETF", "IETF")

#: Tokens that make a name SUSPECT but are not proof: "SKYGOLD" is Sky Gold
#: Ltd, a jewellery manufacturer and a legitimate equity. These are used to
#: FLAG for review, never to exclude automatically -- silently dropping a real
#: company is as wrong as silently buying a fund.
_FUND_HINTS: Final[tuple[str, ...]] = (
    "GOLD", "SILVER", "LIQUID", "NIFTY", "SENSEX", "BHARATBOND",
)

#: Real companies whose names trip the hints above. Confirmed by inspection,
#: listed explicitly so the flag stays worth reading -- a detector that cries
#: wolf is one that gets ignored, which is the failure mode this exists to
#: prevent.
KNOWN_EQUITIES: Final[frozenset[str]] = frozenset({
    "SKYGOLD",       # Sky Gold Ltd -- jewellery manufacturer
    "NAVINFLUOR",    # Navin Fluorine International
    "GOLDIAM",       # Goldiam International -- jewellery
    "GOLDTECH",      # Goldstone Technologies
    "VAIBHAVGBL",    # Vaibhav Global -- jewellery
})


def looks_like_fund(symbol: str) -> bool:
    """True when the symbol is structurally a fund (ETF suffix).

    High confidence and self-maintaining: a new `*ETF` or `*BEES` listing is
    caught without anyone editing a list.
    """
    return symbol.upper().endswith(_FUND_SUFFIXES)


def suspected_funds(symbols: Iterable[str], *, exclude: set[str] | None = None) -> list[str]:
    """Names that look like funds but are neither excluded nor suffix-matched.

    For alerting, not filtering. Turns "an ETF silently entered the book" into
    a message you can act on, which is the only defence available while the
    bhavcopy cannot tell a fund from a share.
    """
    known = (exclude if exclude is not None else set(DEFAULT_EXCLUSIONS))
    out = []
    for symbol in symbols:
        upper = symbol.upper()
        if upper in known or upper in KNOWN_EQUITIES or looks_like_fund(upper):
            continue
        if any(hint in upper for hint in _FUND_HINTS):
            out.append(symbol)
    return sorted(set(out))


def membership(
    panel: pl.DataFrame,
    *,
    size: int = UNIVERSE_SIZE,
    exclude: set[str] | None = None,
    lookback: int = LOOKBACK,
    **select_kwargs,
) -> pl.DataFrame:
    """Universe membership over time, as (rebalance_date, symbol, rank).

    `exclude` defaults to `DEFAULT_EXCLUSIONS` rather than to nothing. Both
    callers previously passed nothing, so the ETF exclusion §10.4 mandates was
    never applied and 523 ETF membership rows entered the top-300 universe.

    Delisted names are retained wherever they qualified historically -- that is
    the entire point of a date-organised archive (§10.2). A name leaves the
    universe only because its liquidity fell, never because it later died.
    """
    # `lookback` belongs to liquidity(); everything else to select(). Forwarding
    # one **kwargs to both raised TypeError for any select-only argument.
    if "adtv" not in panel.columns:
        panel = liquidity(panel, lookback=lookback)
    if exclude is None:
        exclude = set(DEFAULT_EXCLUSIONS)
    # The structural rule catches every *ETF / *BEES listing, including ones
    # nobody has added to the denylist yet.
    exclude = set(exclude) | {
        s for s in panel["symbol"].unique().to_list() if looks_like_fund(s)
    }

    frames = [
        select(panel, day, size=size, exclude=exclude, **select_kwargs)
        .select("date", "symbol", "adtv")
        .with_row_index("rank")
        for day in rebalance_dates(panel)
    ]
    if not frames:
        return pl.DataFrame(
            schema={
                "rank": pl.UInt32,
                "date": pl.Date,
                "symbol": pl.String,
                "adtv": pl.Float64,
            }
        )
    return pl.concat(frames)
