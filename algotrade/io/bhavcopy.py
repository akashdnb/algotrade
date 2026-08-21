"""Parse NSE bhavcopy files into one canonical schema.

NSE has published equity EOD data in two formats, and they differ in more than
column names. See PROJECT.md §10.3 for how these were discovered.

  historic_data/cmDDMMMYYYYbhav.csv   ~2011 -> Jun 2021.  Carries ISIN.
  data/sec_bhavdata_full_DDMMYYYY.csv ~2011 -> present.   Carries VWAP + delivery, no ISIN.

Known traps, all handled here:

  * Whitespace. Later files quote values with leading spaces (" EQ") and put
    spaces in the header ("SYMBOL, SERIES"). Earlier ones don't. Everything is
    stripped on read, or `series == "EQ"` silently matches nothing.
  * SERIES must be filtered. DHFL appears 8x on 2018-01-01 -- once as EQ and
    seven times as debt series (N6, N8, NC, NF, NL, NN, NP). Unfiltered, NCDs
    get treated as equity.
  * Zero-filled columns. AVG_PRICE / NO_OF_TRADES / DELIV_QTY / DELIV_PER are
    all 0 in backfilled older files -- meaning "unavailable", not "zero". We
    detect this per file rather than per row, so a genuinely-zero delivery day
    in a modern file survives.
  * Turnover units differ. TURNOVER_LACS is in lakhs; TOTTRDVAL is in rupees.
    Both are normalised to rupees.
  * Date case differs. "01-JAN-2018" vs "01-Jul-2024".
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Final, Iterable

import polars as pl

# Canonical output schema. Every reader returns exactly these columns, in order.
SCHEMA: Final[dict[str, pl.DataType]] = {
    "date": pl.Date,
    "symbol": pl.String,
    "series": pl.String,
    "isin": pl.String,  # null in the sec_bhavdata_full era
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "prev_close": pl.Float64,
    "last": pl.Float64,
    "vwap": pl.Float64,  # null in the cm era
    "volume": pl.Int64,
    "turnover": pl.Float64,  # rupees, always
    "trades": pl.Int64,
    "deliv_qty": pl.Int64,
    "deliv_pct": pl.Float64,
}

# Columns that are zero-filled rather than absent when NSE had no data for them.
_MAYBE_UNAVAILABLE: Final[tuple[str, ...]] = (
    "vwap",
    "trades",
    "deliv_qty",
    "deliv_pct",
)

_FULL_RE: Final[re.Pattern[str]] = re.compile(
    r"sec_bhavdata_full_(\d{2})(\d{2})(\d{4})\.csv$", re.I
)
_CM_RE: Final[re.Pattern[str]] = re.compile(
    r"cm(\d{2})([A-Z]{3})(\d{4})bhav\.csv$", re.I
)

_MONTHS: Final[dict[str, int]] = {
    m: i
    for i, m in enumerate(
        "jan feb mar apr may jun jul aug sep oct nov dec".split(), start=1
    )
}


class BhavcopyError(ValueError):
    """Raised when a file cannot be parsed as either known bhavcopy format."""


def date_from_filename(path: str | Path) -> date:
    """Recover the trading date from a bhavcopy filename.

    Deliberately does not trust the in-file date column: filename and content
    disagreeing is itself a corruption signal we want `read_file` to catch.
    """
    name = Path(path).name
    if m := _FULL_RE.search(name):
        dd, mm, yyyy = m.groups()
        return date(int(yyyy), int(mm), int(dd))
    if m := _CM_RE.search(name):
        dd, mon, yyyy = m.groups()
        return date(int(yyyy), _MONTHS[mon.lower()], int(dd))
    raise BhavcopyError(f"unrecognised bhavcopy filename: {name}")


def _read_raw(path: Path) -> pl.DataFrame:
    """Read every cell as a stripped string.

    Type inference is deliberately disabled: these files mix formats across
    eras, and letting polars guess produces silent nulls rather than errors.
    """
    df = pl.read_csv(path, infer_schema=False, truncate_ragged_lines=True)
    # The cm format's header has a trailing comma, producing an unnamed column.
    df = df.rename({c: c.strip() for c in df.columns})
    df = df.drop([c for c in df.columns if not c])
    return df.with_columns(pl.col(pl.String).str.strip_chars())


def _parse_dmy(col: str) -> pl.Expr:
    """Parse 'DD-MON-YYYY' with either casing ('01-JAN-2018', '01-Jul-2024')."""
    return (
        pl.col(col)
        .str.to_titlecase()
        .str.to_date(format="%d-%b-%Y", strict=True)
        .alias("date")
    )


def _blank_unavailable(df: pl.DataFrame) -> pl.DataFrame:
    """Null out columns NSE zero-filled for eras where it had no data.

    Decided per file, not per row: if every value in the file is 0 or null the
    field was never populated, so it becomes null. If any row is non-zero the
    column is real and genuine zeros are preserved.
    """
    exprs = []
    for col in _MAYBE_UNAVAILABLE:
        if col not in df.columns:
            continue
        if (df[col].fill_null(0) != 0).any():
            continue
        exprs.append(pl.lit(None).cast(SCHEMA[col]).alias(col))
    return df.with_columns(exprs) if exprs else df


def _finalise(df: pl.DataFrame, trading_date: date, path: Path) -> pl.DataFrame:
    """Cast to the canonical schema and assert the file's date is self-consistent."""
    if (bad := df.filter(pl.col("date") != trading_date)).height:
        got = sorted({str(d) for d in bad["date"].to_list()})[:3]
        raise BhavcopyError(
            f"{path.name}: filename says {trading_date} but rows carry {got}"
        )

    for col, dtype in SCHEMA.items():
        if col not in df.columns:
            df = df.with_columns(pl.lit(None).cast(dtype).alias(col))

    # Integer columns are cast via Float64 so that "1687369.0" parses. Excel
    # stores all numbers as floats, so sessions backfilled from NSE's XLSX
    # archive arrive that way; a direct Int64 cast would silently null them.
    casts = []
    for col, dtype in SCHEMA.items():
        if col in ("date", "symbol", "series", "isin"):
            continue
        expr = pl.col(col).cast(pl.Float64, strict=False)
        if dtype == pl.Int64:
            expr = expr.cast(pl.Int64, strict=False)
        casts.append(expr.alias(col))

    return _blank_unavailable(df.with_columns(casts).select(list(SCHEMA)))


def _turnover_scale(raw: pl.DataFrame) -> float:
    """Whether `TURNOVER_LACS` needs the lakh multiplier, decided from the data.

    Compares the field against `volume x close` on the file's own rows. A median
    ratio near 1 means the values are already rupees; near 1e-5 means lakhs.

    Deliberately not keyed off a cutover date: the mirror's format switch is
    undocumented, and a date threshold would silently mis-scale any file that
    does not follow it.
    """
    probe = raw.select(
        pl.col("TURNOVER_LACS").cast(pl.Float64, strict=False).alias("t"),
        pl.col("TTL_TRD_QNTY").cast(pl.Float64, strict=False).alias("q"),
        pl.col("CLOSE_PRICE").cast(pl.Float64, strict=False).alias("c"),
    ).filter(
        (pl.col("t") > 0) & (pl.col("q") > 0) & (pl.col("c") > 0)
    )
    if probe.height < 20:  # too few rows to decide; assume the documented unit
        return 100_000.0

    ratio = float(
        probe.select((pl.col("t") / (pl.col("q") * pl.col("c"))).median()).item()
    )
    # Rupees: ratio ~1. Lakhs: ratio ~1e-5. Anything else is unrecognised.
    if 0.2 <= ratio <= 5.0:
        return 1.0
    if 2e-6 <= ratio <= 5e-5:
        return 100_000.0
    raise BhavcopyError(
        f"turnover units unrecognised: median(turnover / volume*close) = {ratio:.3g}, "
        "expected ~1 (rupees) or ~1e-5 (lakhs)"
    )


def _read_full(path: Path, raw: pl.DataFrame) -> pl.DataFrame:
    """sec_bhavdata_full_*: has VWAP and delivery data, no ISIN."""
    # TURNOVER_LACS lies about its own units, and the two eras disagree.
    #
    #   2010-06 .. 2021-06  the field is already RUPEES
    #   2021-06 onward      the field is LAKHS, as its name claims
    #
    # Multiplying unconditionally inflated turnover 10^5x for 68% of the archive,
    # which silently disabled the Rs 5 crore liquidity floor for 11 of 16 years
    # and collapsed modelled market impact to half-spread. Detected per file by
    # comparing the field against volume x close rather than trusting the name or
    # a hardcoded cutover date.
    scale = _turnover_scale(raw)
    df = raw.with_columns(
        [
            _parse_dmy("DATE1"),
            (pl.col("TURNOVER_LACS").cast(pl.Float64, strict=False) * scale).alias(
                "turnover"
            ),
        ]
    ).rename(
        {
            "SYMBOL": "symbol",
            "SERIES": "series",
            "PREV_CLOSE": "prev_close",
            "OPEN_PRICE": "open",
            "HIGH_PRICE": "high",
            "LOW_PRICE": "low",
            "LAST_PRICE": "last",
            "CLOSE_PRICE": "close",
            "AVG_PRICE": "vwap",
            "TTL_TRD_QNTY": "volume",
            "NO_OF_TRADES": "trades",
            "DELIV_QTY": "deliv_qty",
            "DELIV_PER": "deliv_pct",
        }
    )
    return _finalise(df, date_from_filename(path), path)


def _read_cm(path: Path, raw: pl.DataFrame) -> pl.DataFrame:
    """cm*bhav: has ISIN, no VWAP or delivery data. TOTTRDVAL is already rupees."""
    df = raw.with_columns(_parse_dmy("TIMESTAMP")).rename(
        {
            "SYMBOL": "symbol",
            "SERIES": "series",
            "ISIN": "isin",
            "OPEN": "open",
            "HIGH": "high",
            "LOW": "low",
            "CLOSE": "close",
            "LAST": "last",
            "PREVCLOSE": "prev_close",
            "TOTTRDQTY": "volume",
            "TOTTRDVAL": "turnover",
            "TOTALTRADES": "trades",
        }
    )
    return _finalise(df, date_from_filename(path), path)


def read_file(path: str | Path, series: Iterable[str] | None = ("EQ",)) -> pl.DataFrame:
    """Read one bhavcopy file of either era into the canonical schema.

    Args:
        path: File to read. The era is detected from its header.
        series: Series codes to keep. Defaults to EQ only -- see the module
            docstring on why this must not be skipped. Pass None to keep all.

    Raises:
        BhavcopyError: Unrecognised filename, unknown layout, or a file whose
            rows disagree with its filename's date.
    """
    path = Path(path)
    raw = _read_raw(path)
    cols = set(raw.columns)

    if "DATE1" in cols:
        df = _read_full(path, raw)
    elif "TIMESTAMP" in cols:
        df = _read_cm(path, raw)
    else:
        raise BhavcopyError(
            f"{path.name}: neither DATE1 nor TIMESTAMP present; got {sorted(cols)[:8]}"
        )

    if series is not None:
        df = df.filter(pl.col("series").is_in(list(series)))
    return df
