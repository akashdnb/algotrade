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

# The benchmark for §1's comparison. **The index has its own rename history**,
# exactly like the equity symbols in §10.10 -- and it is just as easy to miss,
# because filtering on the current name silently returns nothing for the older
# era rather than erroring.
#
#   "S&P CNX Nifty"  until ~2013 (while the S&P licensing lasted)
#   "CNX Nifty"      ~2013 - Nov 2015
#   "Nifty 50"       Nov 2015 onward
#
# Observed directly: the 2012-03-01 file carries "S&P CNX Nifty" and no
# "Nifty 50" at all.
NIFTY_50: Final[str] = "Nifty 50"
NIFTY_50_ALIASES: Final[tuple[str, ...]] = (
    "Nifty 50",
    "CNX Nifty",
    "S&P CNX Nifty",
)

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
    max_failures: int = 20,
) -> pl.DataFrame:
    """Read every cached index file into one panel.

    Parse failures are counted and raised past `max_failures` rather than
    swallowed. An earlier version skipped them silently, which would have hidden
    a systematic parsing problem behind a plausible-looking short series -- the
    same failure mode this project has hit five times in external sources.
    """
    directory = Path(directory)
    frames, failures = [], []
    for path in sorted(directory.glob("ind_close_all_*.csv")):
        try:
            frames.append(read_file(path))
        except Exception as exc:  # a truncated download is expected occasionally
            failures.append(f"{path.name}: {type(exc).__name__}")
    if len(failures) > max_failures:
        raise ValueError(
            f"{len(failures)} index files failed to parse (limit {max_failures}); "
            f"first few: {failures[:3]}"
        )
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
    aliases: Iterable[str] = NIFTY_50_ALIASES,
    start: date | None = None,
    end: date | None = None,
    max_gap_days: int = 4,
) -> pl.DataFrame:
    """(date, close, ret) for the benchmark index, across its renames.

    Matches any of `aliases`, because the index was called "S&P CNX Nifty", then
    "CNX Nifty", then "Nifty 50". Filtering on the current name alone returns
    nothing for the pre-2015 era and does so *silently*.

    Raises:
        ValueError: if two aliases report the same date, which would mean the
            series is being double-counted through a naming overlap rather than
            stitched across a rename.
    """
    panel = load(directory, names=list(aliases), start=start, end=end)
    if panel.is_empty():
        return pl.DataFrame(
            schema={"date": pl.Date, "close": pl.Float64, "ret": pl.Float64}
        )

    clashes = (
        panel.group_by("date").agg(pl.col("index_name").n_unique().alias("n"))
        .filter(pl.col("n") > 1)
    )
    if clashes.height:
        raise ValueError(
            f"{clashes.height} date(s) carry more than one benchmark alias -- "
            f"the series would be double-counted. First: {clashes['date'][0]}"
        )

    # Null the return across any gap in the index series. 436 sessions have no
    # index file (mostly pre-2012), and a return computed across such a hole
    # spans months -- one such gap was 33 calendar days. Left in, those returns
    # inflate the benchmark's CAGR and volatility and deflate measured beta by
    # the same factor, which is how a 43%-coverage series produced a nonsense
    # 23.6% "CAGR" (§11d).
    return (
        panel.sort("date")
        .with_columns(
            (pl.col("date") - pl.col("date").shift(1)).dt.total_days().alias("_gap")
        )
        .with_columns(
            pl.when(pl.col("_gap") <= max_gap_days)
            .then(pl.col("close") / pl.col("close").shift(1) - 1)
            .otherwise(None)
            .alias("ret")
        )
        .select("date", "close", "ret")
    )
