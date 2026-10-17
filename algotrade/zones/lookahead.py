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


def _field(zone, name):
    return zone.get(name) if isinstance(zone, dict) else getattr(zone, name)


def _shape(zones):
    return {
        _field(z, "zone_id"): (
            _field(z, "zone_low"),
            _field(z, "zone_high"),
            _field(z, "created_at"),
            _field(z, "status"),
        )
        for z in zones
    }


def check_zones_no_lookahead(
    panel: pl.DataFrame,
    pipeline_fn: Callable,
    *,
    cutoffs: Sequence[date] | None = None,
) -> LookaheadReport:
    if panel.is_empty() or panel.height < 4:
        raise ValueError("panel is too short for a look-ahead check")
    days = sorted(panel["date"].unique().to_list())
    cuts = list(cutoffs or days[1:-1])
    failures = []
    for cutoff in cuts:
        full = pipeline_fn(panel, cutoff)
        truncated = pipeline_fn(panel.filter(pl.col("date") <= cutoff), cutoff)
        full_shape, truncated_shape = _shape(full), _shape(truncated)
        if full_shape != truncated_shape:
            failures.append(
                {
                    "cutoff": cutoff,
                    "columns": "zone_id/existence, zone_low, zone_high, created_at, status",
                    "full": full_shape,
                    "truncated": truncated_shape,
                }
            )
    return LookaheadReport(not failures, tuple(cuts), failures)
