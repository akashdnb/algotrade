from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from typing import Callable, Sequence
import polars as pl

@dataclass(frozen=True, slots=True)
class LookaheadReport:
    clean: bool
    checked_cutoffs: tuple[date, ...]
    failures: list[dict]


def _shape(zones):
    return {z.zone_id: (z.zone_low, z.zone_high, z.created_at, z.status) for z in zones}


def check_zones_no_lookahead(panel: pl.DataFrame, pipeline_fn: Callable, *, cutoffs: Sequence[date] | None = None):
    if panel.is_empty() or panel.height < 4:
        raise ValueError("panel is too short for a look-ahead check")
    days = sorted(panel["date"].unique().to_list())
    cuts = list(cutoffs or days[1:-1])
    failures = []
    for cutoff in cuts:
        full = pipeline_fn(panel, cutoff)
        truncated = pipeline_fn(panel.filter(pl.col("date") <= cutoff), cutoff)
        a, b = _shape(full), _shape(truncated)
        if a != b:
            failures.append({"cutoff": cutoff, "columns": "zone_id/existence, zone_low, zone_high, created_at, status", "full": a, "truncated": b})
    return LookaheadReport(not failures, tuple(cuts), failures)
