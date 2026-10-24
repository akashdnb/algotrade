#!/usr/bin/env python
"""Compare real zone outcomes against a matched no-skill baseline.

The question `scripts/zone_validation_report.py` cannot answer on its own:
is a zone's forward reaction any better than an arbitrary price band of the
same shape placed on a random day for the same symbol? Without that
comparison, "89% failure rate, +26% MFE at 20 sessions" has no reference
point.

For every detected zone this builds one matched control: the same symbol,
the same zone width and the same offset from the day's close that the real
zone had from its own displacement-day close, but anchored on a randomly
chosen day (deterministic by zone id) instead of the day the detector
picked. The same `event_study_metrics` function is then run on both sets so
the two summaries are directly comparable.

This is a diagnostic, not a strategy backtest: no trading costs, no position
sizing, and the random day is drawn uniformly rather than matched for
volatility regime. It answers one question only -- does the detector's day
selection beat an arbitrary day -- and should be read alongside that
limitation.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from algotrade.config import settings as settings_mod  # noqa: E402
from algotrade.zones.backtest import event_study_metrics  # noqa: E402
from algotrade.zones.config import load_configs  # noqa: E402
from algotrade.zones.pipeline import ZONES_MIN_SESSIONS, run_for_universe  # noqa: E402
from algotrade.services import panel as panel_service  # noqa: E402


def _build_pseudo_zone(zone, *, pseudo_date: date, pseudo_close: Decimal, creation_close: Decimal):
    # Percentage offsets, not absolute rupee offsets: the same symbol's price
    # level can differ enormously between the real creation date and the
    # random date over a multi-year history, and a fixed rupee offset
    # transplanted across that gap turns into a wildly different relative
    # width (or a near-zero zone_low), which blows up the %-based mfe/mae.
    offset_high_pct = (zone.zone_high - creation_close) / creation_close
    offset_low_pct = (zone.zone_low - creation_close) / creation_close
    pseudo_high = (pseudo_close * (1 + offset_high_pct)).quantize(Decimal("0.01"))
    pseudo_low = (pseudo_close * (1 + offset_low_pct)).quantize(Decimal("0.01"))
    if pseudo_low <= 0 or pseudo_high <= pseudo_low:
        return None
    return replace(
        zone,
        zone_low=pseudo_low,
        zone_high=pseudo_high,
        created_at=pseudo_date,
        base_start_date=pseudo_date,
        base_end_date=pseudo_date,
        displacement_date=pseudo_date,
    )


def build_baseline(zones, panel: pl.DataFrame, *, as_of_date: date, seed: int = 0):
    eligible_through = as_of_date  # same look-ahead boundary the real zones used
    dates_by_symbol: dict[str, list[date]] = {}
    close_by_symbol_date: dict[tuple[str, date], Decimal] = {}
    trimmed = panel.filter(pl.col("date") <= eligible_through).select("symbol", "date", "close")
    for row in trimmed.iter_rows():
        symbol, day, close = row
        dates_by_symbol.setdefault(symbol, []).append(day)
        close_by_symbol_date[(symbol, day)] = Decimal(str(close))
    for symbol in dates_by_symbol:
        dates_by_symbol[symbol].sort()

    creation_close_by_zone = {
        zone.zone_id: close_by_symbol_date.get((zone.symbol, zone.created_at))
        for zone in zones
    }

    pseudo_zones = []
    for zone in zones:
        creation_close = creation_close_by_zone.get(zone.zone_id)
        if creation_close is None:
            continue
        candidates = [
            d for d in dates_by_symbol.get(zone.symbol, [])
            if d <= as_of_date  # same information boundary as detection
        ]
        if len(candidates) < 2:
            continue
        rng = random.Random(f"{seed}:{zone.zone_id}")
        pseudo_date = rng.choice(candidates)
        pseudo_close = close_by_symbol_date[(zone.symbol, pseudo_date)]
        pseudo = _build_pseudo_zone(
            zone, pseudo_date=pseudo_date, pseudo_close=pseudo_close, creation_close=creation_close
        )
        if pseudo is not None:
            pseudo_zones.append(pseudo)
    return pseudo_zones


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date(2023, 12, 29))
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--output", type=Path, default=Path("/tmp/zone_baseline_comparison.json"))
    args = parser.parse_args()

    settings = settings_mod.load()
    configs = load_configs(Path("config/zones/zones_v1.yaml"))
    loaded = panel_service.load(
        curated_dir=settings.curated_dir,
        actions_dir=settings.data_dir / "raw" / "actions",
        through=None, sessions=None, min_sessions=ZONES_MIN_SESSIONS,
    )

    print(f"detecting real zones as of {args.as_of} ...")
    results = run_for_universe(loaded.panel, configs=configs, as_of_date=args.as_of)
    real_zones = [z for r in results for z in r.all_zones]
    print(f"real zones: {len(real_zones)}")

    print("building matched random-day baseline ...")
    pseudo_zones = build_baseline(real_zones, loaded.panel, as_of_date=args.as_of, seed=args.seed)
    print(f"baseline zones: {len(pseudo_zones)}")

    real_metrics = event_study_metrics(real_zones, loaded.panel, evaluation_end=loaded.last_session)
    baseline_metrics = event_study_metrics(pseudo_zones, loaded.panel, evaluation_end=loaded.last_session)

    payload = {"as_of": args.as_of.isoformat(), "real": real_metrics["overall"], "baseline": baseline_metrics["overall"]}
    args.output.write_text(json.dumps(payload, indent=2, default=str))
    print(json.dumps(payload, indent=2, default=str))
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
