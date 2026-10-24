#!/usr/bin/env python
"""Refresh the committed Nifty 500 constituent snapshot from NSE Indices."""

from __future__ import annotations

import io
import json
import sys
import urllib.request
from datetime import date
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from algotrade.zones.universes import NIFTY500_CSV, NIFTY500_META  # noqa: E402

URL = "https://niftyindices.com/IndexConstituent/ind_nifty500list.csv"


def main() -> int:
    request = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(request, timeout=30).read()
    frame = pl.read_csv(io.BytesIO(raw))
    missing = {"Company Name", "Industry", "Symbol", "Series", "ISIN Code"} - set(frame.columns)
    if missing or not 450 <= frame.height <= 550:
        raise SystemExit(f"unexpected constituent file: columns missing {sorted(missing)}, rows {frame.height}")
    NIFTY500_CSV.write_bytes(raw)
    NIFTY500_META.write_text(json.dumps({"fetched_on": date.today().isoformat(), "count": frame.height, "source": URL}, indent=2) + "\n")
    print(f"wrote {frame.height} constituents to {NIFTY500_CSV}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
