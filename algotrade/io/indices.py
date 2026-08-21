"""Fetch and parse NSE index closing levels (PROJECT.md §11d).

Needed for two things the equity panel cannot provide:

**A benchmark that cannot be corrupted by a corporate action.** §11d records the
near-miss: `NIFTYBEES` computes to −10% CAGR because it split 1:10 in 2019 and
ETF actions are absent from the equities feed. An index is a *computed level*,
not a tradeable security, so it is never split-adjusted and cannot carry that
error at all.

**Beta.** A −38% drawdown on a supposedly market-neutral book implies real
residual market exposure, which cannot even be measured without a market series.

NSE's `indicesHistory` JSON API returns 503, so the daily
`ind_close_all_DDMMYYYY.csv` archive is used instead. One file per session, but
each carries *every* index — Nifty 50, Bank Nifty, and the sector indices — so
sector-relative features come free later.

Note this is the index *level*, not a total-return series: it excludes dividends
(~1–1.5%/yr for Indian equities). A price-index benchmark therefore slightly
*understates* what buy-and-hold delivers, which is the conservative direction for
a `beats_benchmark` gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Final, Iterable

import polars as pl
import requests

from algotrade.io import nse

ARCHIVE: Final[str] = (
    "https://nsearchives.nseindia.com/content/indices/ind_close_all_{stamp}.csv"
)
DEFAULT_DIR: Final[Path] = Path("data/raw/indices")

# The benchmark for §1's comparison, as NSE names it in these files.
NIFTY_50: Final[str] = "Nifty 50"

SCHEMA: Final[dict[str, pl.DataType]] = {
    "date": pl.Date,
    "index_name": pl.String,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Float64,
    "turnover": pl.Float64,
    "pe": pl.Float64,
    "pb": pl.Float64,
    "div_yield": pl.Float64,
}

_RENAME: Final[dict[str, str]] = {
    "Index Name": "index_name",
    "Index Date": "_date_raw",
    "Open Index Value": "open",
    "High Index Value": "high",
    "Low Index Value": "low",
    "Closing Index Value": "close",
    "Volume": "volume",
    "Turnover": "turnover",
    "Turnover (Rs. Cr.)": "turnover",
    "P/E": "pe",
    "P/B": "pb",
    "Div Yield": "div_yield",
}


def filename_for(day: date) -> str:
    return f"ind_close_all_{day:%d%m%Y}.csv"


@dataclass
class SyncReport:
    fetched: list[date] = field(default_factory=list)
    reused: list[date] = field(default_factory=list)
    missing: list[date] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)


def download_to(
    day: date,
    directory: Path | str = DEFAULT_DIR,
    *,
    session: requests.Session | None = None,
    overwrite: bool = False,
) -> Path | None:
    """Fetch one session's index closes. Returns None on a 404 (non-session)."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / filename_for(day)
    if target.exists() and not overwrite:
        return target

    own = session or nse._session()
    response = own.get(ARCHIVE.format(stamp=f"{day:%d%m%Y}"), timeout=45)
    if response.status_code == 404:
        return None
    if not response.ok or not response.content:
        raise nse.NseFetchError(f"{day}: HTTP {response.status_code}")

    staging = target.with_suffix(".csv.part")
    staging.write_bytes(response.content)
    staging.replace(target)
    return target


def read_file(path: str | Path) -> pl.DataFrame:
    """Parse one `ind_close_all` file into the canonical schema.

    Values arrive with thousands separators and stray whitespace, and a missing
    P/E is written as `-`; all of that becomes null rather than zero, since a
    zero P/E would be a real number and a wrong one.
    """
    path = Path(path)
    raw = pl.read_csv(path, infer_schema=False, truncate_ragged_lines=True)
    raw = raw.rename({c: c.strip() for c in raw.columns})
    raw = raw.rename({k: v for k, v in _RENAME.items() if k in raw.columns})
    raw = raw.with_columns(pl.col(pl.String).str.strip_chars())

    for col, dtype in SCHEMA.items():
        if col not in raw.columns and col != "date":
            raw = raw.with_columns(pl.lit(None).cast(dtype).alias(col))

    numeric = [c for c, d in SCHEMA.items() if d == pl.Float64]
    return (
        raw.with_columns(
            pl.col("_date_raw")
            .str.to_date(format="%d-%m-%Y", strict=False)
            .alias("date"),
            *[
                pl.col(c)
                .str.replace_all(",", "")
                .str.replace_all("^-$", "")
                .cast(pl.Float64, strict=False)
                .alias(c)
                for c in numeric
            ],
        )
        .drop_nulls("date")
        .select(list(SCHEMA))
    )


def load(
    directory: Path | str = DEFAULT_DIR,
    *,
    names: Iterable[str] | None = None,
    start: date | None = None,
    end: date | None = None,
) -> pl.DataFrame:
    """Read every cached index file into one panel."""
    directory = Path(directory)
    frames = []
    for path in sorted(directory.glob("ind_close_all_*.csv")):
        try:
            frames.append(read_file(path))
        except Exception:  # a truncated download should not kill the load
            continue
    if not frames:
        return pl.DataFrame(schema=SCHEMA)

    panel = pl.concat(frames)
    if names is not None:
        panel = panel.filter(pl.col("index_name").is_in(list(names)))
    if start is not None:
        panel = panel.filter(pl.col("date") >= start)
    if end is not None:
        panel = panel.filter(pl.col("date") <= end)
    return panel.unique(subset=["date", "index_name"]).sort(["index_name", "date"])


def benchmark_series(
    directory: Path | str = DEFAULT_DIR,
    *,
    name: str = NIFTY_50,
    start: date | None = None,
    end: date | None = None,
) -> pl.DataFrame:
    """(date, close, ret) for one index -- the input to benchmark comparison."""
    panel = load(directory, names=[name], start=start, end=end)
    if panel.is_empty():
        return pl.DataFrame(schema={"date": pl.Date, "close": pl.Float64, "ret": pl.Float64})
    return panel.sort("date").select(
        "date",
        "close",
        (pl.col("close") / pl.col("close").shift(1) - 1).alias("ret"),
    )
