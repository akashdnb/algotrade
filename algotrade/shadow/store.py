"""Persistence for the shadow portfolio: `data/shadow/*`.

Parquet + JSON manifest, mirroring `algotrade.io.store`'s convention, rather
than introducing a database for one nightly job. `weights_log` and `equity`
are each overwritten wholesale on every run -- both are cheap to recompute in
full and small enough that partitioning would add complexity with no payoff.
`discrepancies.jsonl` and `run_log.jsonl` are append-only: they are the
permanent record a discrepancy happened, independent of the baseline moving
forward past it (see the design spec's step 6).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

import polars as pl

DEFAULT_DIR: Final[Path] = Path("data/shadow")

WEIGHTS_LOG: Final[str] = "weights_log.parquet"
WEIGHTS_LOG_MANIFEST: Final[str] = "weights_log_manifest.json"
EQUITY: Final[str] = "equity.parquet"

WEIGHTS_SCHEMA: Final[dict] = {"date": pl.Date, "symbol": pl.String, "weight": pl.Float64}


def write_weights_log(
    weights: pl.DataFrame,
    *,
    directory: Path | str = DEFAULT_DIR,
    built_at: str | None = None,
    commit: str | None = None,
) -> dict:
    """Persist tonight's full recomputed weight history as the new baseline."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    weights.write_parquet(directory / WEIGHTS_LOG, compression="zstd")

    manifest = {
        "built_at": built_at
        or datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "commit": commit,
        "sessions": weights["date"].n_unique() if weights.height else 0,
        "rows": weights.height,
    }
    (directory / WEIGHTS_LOG_MANIFEST).write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    return manifest


def read_weights_log(directory: Path | str = DEFAULT_DIR) -> pl.DataFrame:
    """The persisted weight history, or an empty (correctly typed) frame.

    Empty rather than raising: the first-ever run has no prior baseline, and
    that is a normal starting state for `parity.diff_weights` to compare
    against, not an error.
    """
    path = Path(directory) / WEIGHTS_LOG
    if not path.exists():
        return pl.DataFrame(schema=WEIGHTS_SCHEMA)
    return pl.read_parquet(path)


def write_equity(equity: pl.DataFrame, *, directory: Path | str = DEFAULT_DIR) -> None:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    equity.write_parquet(directory / EQUITY, compression="zstd")


def read_equity(directory: Path | str = DEFAULT_DIR) -> pl.DataFrame:
    path = Path(directory) / EQUITY
    if not path.exists():
        return pl.DataFrame()
    return pl.read_parquet(path)


def append_jsonl(path: Path | str, record: dict) -> None:
    """Append one JSON record as a line. Creates parent directories as needed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True, default=str) + "\n")
