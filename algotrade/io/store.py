"""Persist the panel as Parquet and read it back (PROJECT.md §11 task C).

Re-parsing 3,993 CSVs costs ~21s per load, which is tolerable for diagnostics
and not for research iteration.

Partitioned by year so a date-ranged read touches only the files it needs.
Parquet keeps the canonical dtypes, so the schema no longer has to be
re-inferred and re-cast on every load.

A manifest is written alongside the data. Git versions the code, not the data,
and the mirror is updated daily -- so without recording which sessions went into
a build, an identical-code rerun after a backfill silently produces different
numbers (§12). The manifest closes that half of reproducibility.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Final, Iterable

import polars as pl

DEFAULT_DIR: Final[Path] = Path("data/curated")
MANIFEST: Final[str] = "manifest.json"


@dataclass(frozen=True)
class Manifest:
    """What went into a panel build, so a result can be traced to its inputs."""

    built_at: str
    sessions: int
    rows: int
    first_session: str
    last_session: str
    symbols: int
    rejected_sessions: list[str]
    source_dirs: list[str]

    def to_json(self) -> str:
        return json.dumps(self.__dict__, indent=2, sort_keys=True)


def write(
    panel: pl.DataFrame,
    *,
    directory: Path | str = DEFAULT_DIR,
    rejected: Iterable[date] = (),
    source_dirs: Iterable[str] = (),
    built_at: str | None = None,
) -> Manifest:
    """Write the panel partitioned by year, plus a manifest.

    Args:
        built_at: Injected rather than read from the clock so builds are
            reproducible in tests.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for stale in directory.glob("year=*"):
        for f in stale.glob("*.parquet"):
            f.unlink()
        stale.rmdir()

    with_year = panel.with_columns(pl.col("date").dt.year().alias("year"))
    for year in sorted(with_year["year"].unique().to_list()):
        part = directory / f"year={year}"
        part.mkdir(exist_ok=True)
        with_year.filter(pl.col("year") == year).drop("year").write_parquet(
            part / "panel.parquet", compression="zstd"
        )

    manifest = Manifest(
        built_at=built_at
        or datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        sessions=panel["date"].n_unique(),
        rows=panel.height,
        first_session=str(panel["date"].min()),
        last_session=str(panel["date"].max()),
        symbols=panel["symbol"].n_unique(),
        rejected_sessions=sorted(str(d) for d in rejected),
        source_dirs=[str(s) for s in source_dirs],
    )
    (directory / MANIFEST).write_text(manifest.to_json(), encoding="utf-8")
    return manifest


def read(
    directory: Path | str = DEFAULT_DIR,
    *,
    start: date | None = None,
    end: date | None = None,
    columns: list[str] | None = None,
) -> pl.DataFrame:
    """Read the curated panel, optionally for a date range only.

    Year partitioning is used to skip files outside the range before any are
    opened.
    """
    directory = Path(directory)
    parts = sorted(directory.glob("year=*/panel.parquet"))
    if not parts:
        raise FileNotFoundError(f"no curated panel under {directory}")

    if start or end:
        keep = []
        for part in parts:
            year = int(part.parent.name.split("=")[1])
            if start and year < start.year:
                continue
            if end and year > end.year:
                continue
            keep.append(part)
        parts = keep

    frames = [pl.read_parquet(p, columns=columns) for p in parts]
    panel = pl.concat(frames) if frames else pl.DataFrame()
    if panel.is_empty():
        return panel
    if start is not None:
        panel = panel.filter(pl.col("date") >= start)
    if end is not None:
        panel = panel.filter(pl.col("date") <= end)
    # Sort by whichever keys survived a column projection.
    keys = [c for c in ("date", "symbol") if c in panel.columns]
    return panel.sort(keys) if keys else panel


def read_manifest(directory: Path | str = DEFAULT_DIR) -> dict:
    path = Path(directory) / MANIFEST
    if not path.exists():
        raise FileNotFoundError(f"no manifest at {path}")
    return json.loads(path.read_text())


# --- adjusted panel: a cache, never a source of truth ------------------------
#
# The adjusted panel is derived from (curated panel, factor set). Persisting it
# is purely a speed optimisation, and it must not become authoritative: that is
# exactly how eod2 turns a bad factor into permanent corruption (§10.3), and why
# §5 requires adjustment on read.
#
# So it is keyed by a fingerprint of the factor set. Change any factor and the
# cache no longer matches, so it is rebuilt rather than silently reused. The
# fingerprint makes staleness detectable instead of invisible.

ADJUSTED_MANIFEST: Final[str] = "adjusted_manifest.json"


def factor_fingerprint(factors: pl.DataFrame) -> str:
    """Stable hash of a factor set, independent of row order."""
    import hashlib

    if factors.is_empty():
        return "empty"
    rows = (
        factors.select("symbol", "ex_date", "factor")
        .sort(["symbol", "ex_date"])
        .rows()
    )
    payload = "\n".join(f"{s}|{d}|{f:.10g}" for s, d, f in rows)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def write_adjusted(
    panel: pl.DataFrame,
    factors: pl.DataFrame,
    *,
    directory: Path | str = DEFAULT_DIR,
    excluded: Iterable[str] = (),
    built_at: str | None = None,
) -> dict:
    """Cache an adjusted panel, tagged with the factor set that produced it."""
    directory = Path(directory)
    target = directory / "adjusted"
    target.mkdir(parents=True, exist_ok=True)
    panel.write_parquet(target / "panel.parquet", compression="zstd")

    info = {
        "built_at": built_at
        or datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "factor_fingerprint": factor_fingerprint(factors),
        "factors_applied": factors.height,
        "rows": panel.height,
        "sessions": panel["date"].n_unique(),
        "symbols": panel["symbol"].n_unique(),
        "excluded_symbols": sorted(excluded),
    }
    (directory / ADJUSTED_MANIFEST).write_text(
        json.dumps(info, indent=2, sort_keys=True), encoding="utf-8"
    )
    return info


def read_adjusted(
    factors: pl.DataFrame,
    *,
    directory: Path | str = DEFAULT_DIR,
    start: date | None = None,
    end: date | None = None,
) -> pl.DataFrame | None:
    """Return the cached adjusted panel, or None if it is missing or stale.

    Returning None rather than raising lets callers treat a stale cache as a
    rebuild trigger, which is the only safe default -- silently serving a panel
    adjusted by different factors is the failure this guards against.
    """
    directory = Path(directory)
    data = directory / "adjusted" / "panel.parquet"
    info_path = directory / ADJUSTED_MANIFEST
    if not data.exists() or not info_path.exists():
        return None

    info = json.loads(info_path.read_text())
    if info.get("factor_fingerprint") != factor_fingerprint(factors):
        return None

    panel = pl.read_parquet(data)
    if start is not None:
        panel = panel.filter(pl.col("date") >= start)
    if end is not None:
        panel = panel.filter(pl.col("date") <= end)
    return panel.sort(["date", "symbol"])
