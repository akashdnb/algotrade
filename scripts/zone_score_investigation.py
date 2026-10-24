#!/usr/bin/env python
"""Explain why the highest strength_score bucket underperformed in the event
study (score 80+: 95% failure rate vs. 85% for score 60-79).

Hypothesis under test: `score_zone`'s freshness term decays with raw
calendar age (`1 / (1 + 0.05*age_days + 0.25*touch_count)`), which halves in
about 14 days regardless of touch history. A zone only reaches 80+ if either
(a) it was created very recently relative to the as-of date (freshness still
near its max), or (b) displacement/base/volume/structure are all near-max
simultaneously. (a) selects for zones that have barely had time to prove
anything and, if displacement-driven, are exactly the ones prone to an
immediate post-breakout retracement -- which would explain the inversion.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from algotrade.config import settings as settings_mod  # noqa: E402
from algotrade.zones.config import load_configs  # noqa: E402
from algotrade.zones.pipeline import ZONES_MIN_SESSIONS, run_for_universe  # noqa: E402
from algotrade.services import panel as panel_service  # noqa: E402
from algotrade.zones.backtest import _broken_by_close, _touches  # noqa: E402

AS_OF = date(2023, 12, 29)


def main() -> int:
    settings = settings_mod.load()
    configs = load_configs(Path("config/zones/zones_v1.yaml"))
    loaded = panel_service.load(
        curated_dir=settings.curated_dir,
        actions_dir=settings.data_dir / "raw" / "actions",
        through=None, sessions=None, min_sessions=ZONES_MIN_SESSIONS,
    )

    print(f"detecting zones as of {AS_OF} ...")
    results = run_for_universe(loaded.panel, configs=configs, as_of_date=AS_OF)
    zones = [z for r in results for z in r.all_zones]
    print(f"total zones: {len(zones)}")

    high = [z for z in zones if z.strength_score >= 80]
    mid = [z for z in zones if 60 <= z.strength_score < 80]
    print(f"score>=80: {len(high)}   score 60-79: {len(mid)}")

    def describe(label, bucket):
        if not bucket:
            print(f"{label}: empty")
            return
        ages = [(AS_OF - z.created_at).days for z in bucket]
        disp = [z.displacement_atr for z in bucket]
        vol = [z.volume_ratio or 0.0 for z in bucket]
        touches = [z.touch_count for z in bucket]
        print(f"{label}: n={len(bucket)}")
        print(f"  age_days   min={min(ages)} median={sorted(ages)[len(ages)//2]} max={max(ages)}")
        print(f"  disp_atr   min={min(disp):.2f} median={sorted(disp)[len(disp)//2]:.2f} max={max(disp):.2f}")
        print(f"  vol_ratio  min={min(vol):.2f} median={sorted(vol)[len(vol)//2]:.2f} max={max(vol):.2f}")
        print(f"  touch_count at detection time: {sum(touches)} total, {sum(1 for t in touches if t) } zones with >=1")

    describe("score>=80", high)
    describe("score 60-79", mid)

    # Outcome check through the full remaining history, independent of the
    # lifecycle status computed only up to AS_OF.
    def failure_rate(bucket):
        failed = 0
        evaluated = 0
        for z in bucket:
            bars = loaded.panel.filter(
                (pl.col("symbol") == z.symbol) & (pl.col("date") > z.created_at) & (pl.col("date") <= loaded.last_session)
            ).sort("date").to_dicts()
            if not bars:
                continue
            evaluated += 1
            if any(_broken_by_close(z, bar) for bar in bars):
                failed += 1
        return failed, evaluated

    for label, bucket in (("score>=80", high), ("score 60-79", mid)):
        recent = [z for z in bucket if (AS_OF - z.created_at).days <= 30]
        older = [z for z in bucket if (AS_OF - z.created_at).days > 30]
        print(f"{label}: created within 30 days of as-of: {len(recent)}/{len(bucket)}")
        for sub_label, sub in (("<=30d old", recent), (">30d old", older)):
            f, n = failure_rate(sub)
            rate = f / n if n else None
            print(f"    {sub_label}: n={n} failure_rate={rate}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
