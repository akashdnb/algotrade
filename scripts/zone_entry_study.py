#!/usr/bin/env python
"""Entry-based zone study: do first-touch entries beat matched random-day controls?

Replaces the creation-anchored MFE comparison, which measured drift from a zone
edge whether or not price ever came back to the zone. See `algotrade.zones.study`
for the event definition.

Development window only by default (PROJECT.md §8). `--holdout` spends one of
the project's lifetime holdout looks and must be a deliberate, registered act.
"""

from __future__ import annotations

import argparse
import json
import pickle
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from pathlib import Path

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from algotrade.backtest import registry  # noqa: E402
from algotrade.config import settings as settings_mod  # noqa: E402
from algotrade.core.universe import DEFAULT_EXCLUSIONS, liquidity, looks_like_fund  # noqa: E402
from algotrade.services import panel as panel_service  # noqa: E402
from algotrade.zones import study  # noqa: E402
from algotrade.zones.config import DemandSupplyConfig, Timeframe, load_configs  # noqa: E402
from algotrade.zones.pipeline import ZONES_MIN_SESSIONS, detect_symbol_zones  # noqa: E402
from algotrade.zones.scoring import score_zone  # noqa: E402

DEV_END = date(2021, 12, 31)
CONTROLS = 5
#: Round-trip delivery cost assumption: STT 0.2% + stamp/exchange ~0.02% + ~8 bps
#: slippage each way for liquid names (config/costs.yaml). An assumption, not a measurement.
ROUND_TRIP_COST = 0.0035
ALL_HORIZONS = (5, 10, 20, 40, 60)


def metrics_for(horizons) -> tuple[str, ...]:
    return tuple(f"{kind}_{h}" for kind in ("abn", "abn_stop") for h in horizons)

def _detect(task):
    symbol, daily, configs, end = task
    return symbol, detect_symbol_zones(daily, symbol, configs=configs, as_of_date=end)


def load_panel(end: date) -> pl.DataFrame:
    settings = settings_mod.load()
    loaded = panel_service.load(
        curated_dir=settings.curated_dir,
        actions_dir=settings.data_dir / "raw" / "actions",
        through=end,
        sessions=None,
        min_sessions=ZONES_MIN_SESSIONS,
    )
    panel = liquidity(loaded.panel.sort(["symbol", "date"]))
    excluded = set(DEFAULT_EXCLUSIONS)
    funds = {s for s in panel["symbol"].unique().to_list() if looks_like_fund(s)}
    return panel.with_columns(
        (
            (pl.col("adtv") >= 5e7)
            & (pl.col("coverage") >= 0.8)
            & (pl.col("close") >= 5.0)
            & ~pl.col("symbol").is_in(excluded | funds)
        )
        .fill_null(False)
        .alias("eligible")
    )


def detect_all(panel: pl.DataFrame, configs, end: date, workers: int) -> dict:
    eligible = set(panel.filter(pl.col("eligible")).select("symbol").unique().to_series().to_list())
    tasks = (
        (key[0], group.sort("date"), configs, end)
        for key, group in panel.filter(pl.col("symbol").is_in(eligible)).partition_by("symbol", as_dict=True).items()
    )
    with ProcessPoolExecutor(max_workers=workers) as pool:
        return dict(pool.map(_detect, tasks, chunksize=8))


def build_events(zones_by_symbol: dict, panel: pl.DataFrame, configs: dict[Timeframe, DemandSupplyConfig], horizons=study.HORIZONS) -> list[dict]:
    bars_by_symbol = study.build_bars(panel)
    market = study.build_market(panel)
    events: list[dict] = []
    for symbol, by_tf in zones_by_symbol.items():
        bars = bars_by_symbol.get(symbol)
        if bars is None:
            continue
        # Every zone's live span, so a first touch can ask which other-timeframe
        # levels were known and still unbroken at that moment.
        spans = [
            (tf, z.zone_type.value, float(z.zone_low), float(z.zone_high), study.epoch_day(z.created_at),
             study.broken_day(bars, demand=z.zone_type.value == "demand", low=float(z.zone_low), high=float(z.zone_high), after_day=study.epoch_day(z.created_at)))
            for tf, zs in by_tf.items() for z in zs
        ]
        for timeframe, zones in by_tf.items():
            for zone in zones:
                demand = zone.zone_type.value == "demand"
                low, high = float(zone.zone_low), float(zone.zone_high)
                created = study.epoch_day(zone.created_at)
                found = study.find_entry(bars, demand=demand, low=low, high=high, after_day=created)
                if found is None:
                    continue
                row = study.event_row(bars, found, demand=demand, low=low, high=high, market=market, horizons=horizons)
                if row is None:
                    continue
                others = [(o_low, o_high, o_created, o_broken) for tf, kind, o_low, o_high, o_created, o_broken in spans if kind == zone.zone_type.value and tf != timeframe]
                row["confluence"] = study.has_confluence(others, low=low, high=high, reach_day=row["reach_day"])
                at = int(np.searchsorted(bars.dates, created))
                if at >= len(bars.dates) or bars.dates[at] != created:
                    continue
                close = float(bars.close[at])
                row.update(
                    symbol=symbol,
                    zone_id=zone.zone_id,
                    timeframe=timeframe.value,
                    zone_type=zone.zone_type.value,
                    created_day=created,
                    displacement_atr=zone.displacement_atr,
                    volume_ratio=zone.volume_ratio,
                    base_quality=zone.base_quality,
                    score=score_zone(zone, config=configs[timeframe], as_of_date=zone.created_at),
                    width_pct=(high - low) / close,
                    controls=study.matched_controls(
                        bars, demand=demand, off_low=low / close - 1.0, off_high=high / close - 1.0,
                        created_day=created, k=CONTROLS, seed_key=zone.zone_id, market=market, horizons=horizons,
                    ),
                )
                events.append(row)
    return events


def _buckets(event: dict) -> dict[str, str]:
    d, v, a = event["displacement_atr"], event["volume_ratio"] or 0.0, event["age"]
    return {
        "timeframe": event["timeframe"],
        "score": "<50" if event["score"] < 50 else "50-59" if event["score"] < 60 else "60+",
        "displacement": "<2" if d < 2 else "2-3" if d < 3 else "3+",
        "volume": "<1.5x" if v < 1.5 else "1.5-5x" if v < 5 else "5x+",
        "age": "<=20" if a <= 20 else "21-60" if a <= 60 else "61-250" if a <= 250 else ">250",
    }


def analyse(events: list[dict], metrics) -> dict:
    out: dict = {}
    for zone_type in ("demand", "supply"):
        subset = [e for e in events if e["zone_type"] == zone_type]
        out[zone_type] = {"all": {m: study.paired_difference(study.collapse_events(subset, metrics), m) for m in metrics}}
        slices: dict[str, dict[str, dict]] = {}
        for dimension in ("timeframe", "score", "displacement", "volume", "age"):
            groups: dict[str, list[dict]] = {}
            for event in subset:
                groups.setdefault(_buckets(event)[dimension], []).append(event)
            slices[dimension] = {
                key: {m: study.paired_difference(study.collapse_events(items, metrics), m) for m in ("abn_10", "abn_stop_10")}
                for key, items in sorted(groups.items())
            }
        out[zone_type]["slices"] = slices
    return out


def _line(label: str, r: dict) -> str:
    if not r["n"]:
        return f"  {label:<34} n=0"
    return f"  {label:<34} n={r['n']:>6} real={r['real_mean']:+.4f} ctrl={r['control_mean']:+.4f} diff={r['mean']:+.4f} [{r['lo']:+.4f},{r['hi']:+.4f}] p={r['p']:.4f}"


def _conditioned(events: list[dict], key: str, value) -> list[dict]:
    """Keep events whose `key` equals `value`, and only the controls that match too."""
    return [
        {**e, "controls": [c for c in e["controls"] if c.get(key) == value]}
        for e in events if e.get(key) == value
    ]


def variants(events: list[dict]) -> str:
    """The three pre-registered hypotheses (trials 10-12), demand zones only."""
    demand = [e for e in events if e["zone_type"] == "demand"]
    lines = [f"round-trip cost assumption: {ROUND_TRIP_COST:.2%}; Bonferroni threshold p < {0.05 / 3:.4f}", ""]

    def run(label, subset, metric):
        merged = study.collapse_events(subset, [metric])
        r = study.paired_difference(merged, metric)
        lines.append(_line(f"{label} [{metric}]", r))
        return r

    lines.append("H10 confluence (control = parent zone's matched controls)")
    for flag in (True, False):
        run(f"  confluence={flag}", [e for e in demand if e["confluence"] == flag], "abn_stop_10")
    lines.append("H11 trend-aligned (controls conditioned on the same trend state)")
    for flag in (True, False):
        run(f"  trend_up={flag}", _conditioned(demand, "trend_up", flag), "abn_stop_10")
    lines.append("H12 long horizon, weekly+monthly demand")
    higher = [e for e in demand if e["timeframe"] in ("weekly", "monthly")]
    for metric in ("abn_stop_40", "abn_stop_60", "abn_40", "abn_60"):
        run("  weekly+monthly", higher, metric)
    for tf in ("weekly", "monthly"):
        run(f"  {tf}", [e for e in higher if e["timeframe"] == tf], "abn_stop_40")
    return "\n".join(lines)


def render(result: dict, n_events: int) -> str:
    lines = [f"events (real, tradeable first touches): {n_events}", f"round-trip cost assumption: {ROUND_TRIP_COST:.2%}", ""]
    for zone_type, block in result.items():
        lines.append(f"== {zone_type.upper()} — real minus matched control (market-adjusted) ==")
        for metric, r in block["all"].items():
            if r["n"]:
                lines.append(f"  {metric:<12} n={r['n']:>6} real={r['real_mean']:+.4f} ctrl={r['control_mean']:+.4f} diff={r['mean']:+.4f} [{r['lo']:+.4f},{r['hi']:+.4f}] p={r['p']:.3f}")
        for dimension, buckets in block["slices"].items():
            lines.append(f"  by {dimension}:")
            for key, metrics in buckets.items():
                r = metrics["abn_10"]
                if r["n"]:
                    lines.append(f"    {key:<8} n={r['n']:>6} real={r['real_mean']:+.4f} ctrl={r['control_mean']:+.4f} diff={r['mean']:+.4f} [{r['lo']:+.4f},{r['hi']:+.4f}] p={r['p']:.3f}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--end-date", type=date.fromisoformat, default=DEV_END)
    parser.add_argument("--holdout", action="store_true", help="spend a registered holdout look (end-date beyond the dev window)")
    parser.add_argument("--config", type=Path, default=Path("config/zones/zones_v1.yaml"))
    parser.add_argument("--cache", type=Path, help="pickle of raw zones, reused when present")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--variants", action="store_true", help="run the pre-registered variant hypotheses (long horizons included)")
    parser.add_argument("--output", type=Path, default=Path("reports/zone_entry_study.md"))
    args = parser.parse_args()

    if args.end_date > DEV_END and not args.holdout:
        raise SystemExit(f"--end-date {args.end_date} is past the development window ({DEV_END}); pass --holdout to spend a look")
    configs = load_configs(args.config)
    if args.holdout:
        registry.register(
            name="zone_entry_study_holdout",
            hypothesis="First-touch entries into fresh zones beat matched random-day controls after market adjustment.",
            params={"config": str(args.config), "end_date": args.end_date.isoformat()},
            dataset="holdout",
        )

    panel = load_panel(args.end_date)
    print(f"panel: {panel.height:,} rows, {panel['symbol'].n_unique():,} symbols through {panel['date'].max()}", flush=True)
    if args.cache and args.cache.exists():
        zones = pickle.loads(args.cache.read_bytes())
    else:
        zones = detect_all(panel, configs, args.end_date, args.workers)
        if args.cache:
            args.cache.write_bytes(pickle.dumps(zones))
    print(f"zones: {sum(len(z) for by_tf in zones.values() for z in by_tf.values()):,} across {len(zones):,} symbols", flush=True)

    horizons = ALL_HORIZONS if args.variants else study.HORIZONS
    events = build_events(zones, panel, configs, horizons)
    if args.variants:
        text = f"events: {len(events)}\n" + variants(events)
        print(text)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text("```\n" + text + "\n```\n", encoding="utf-8")
        return 0
    result = analyse(events, metrics_for(horizons))
    text = render(result, len(events))
    print(text)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("```\n" + text + "\n```\n", encoding="utf-8")
    (args.output.with_suffix(".json")).write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
