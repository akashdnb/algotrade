"""Named symbol universes for zone detection.

`liquid` is the default: every symbol that passes the liquidity floor. `nifty500`
restricts to the index's constituents, from a committed NSE snapshot
(`config/universes/nifty500.csv`, refreshed by `scripts/refresh_nifty500.py`).

The snapshot is *current* membership. Applying it to a historical as-of date
reintroduces survivorship bias, so it is meant for the live job and offline
reports on the latest session, not for backtests.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path

import polars as pl

log = logging.getLogger(__name__)

UNIVERSES = ("liquid", "nifty500")
_DIR = Path(__file__).resolve().parents[2] / "config" / "universes"
NIFTY500_CSV = _DIR / "nifty500.csv"
NIFTY500_META = _DIR / "nifty500.meta.json"
#: NSE rebalances its indices semi-annually; a snapshot older than this is stale.
STALE_AFTER = timedelta(days=200)


def load_nifty500(csv: Path = NIFTY500_CSV, meta: Path = NIFTY500_META, *, today: date | None = None) -> frozenset[str]:
    """Constituent symbols. Warns when the snapshot is older than STALE_AFTER."""
    symbols = frozenset(pl.read_csv(csv)["Symbol"].str.strip_chars().to_list())
    if not symbols:
        raise ValueError(f"{csv} has no constituents")
    if meta.exists():
        fetched = date.fromisoformat(json.loads(meta.read_text())["fetched_on"])
        if (today or date.today()) - fetched > STALE_AFTER:
            log.warning("Nifty 500 snapshot is from %s; run scripts/refresh_nifty500.py", fetched)
    return symbols


def members_for(universe: str) -> frozenset[str] | None:
    """Symbols to restrict detection to, or None for the unrestricted liquid universe."""
    if universe == "liquid":
        return None
    if universe == "nifty500":
        return load_nifty500()
    raise ValueError(f"unknown universe {universe!r}; choose from {UNIVERSES}")
