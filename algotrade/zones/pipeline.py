from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal

import polars as pl

from algotrade.core.universe import DEFAULT_EXCLUSIONS, KNOWN_EQUITIES, looks_like_fund

from .aggregation import aggregate_to_monthly, aggregate_to_weekly
from .config import DemandSupplyConfig, Timeframe, ZoneStatus
from .confluence import Confluence, calculate_confluence
from .detect import detect_zones
from .lifecycle import update_zone_status
from .models import Zone
from .scoring import score_zone

ZONES_MIN_SESSIONS = 400


@dataclass(frozen=True, slots=True)
class SymbolZoneResult:
    symbol: str
    current_price: Decimal
    zones: dict[Timeframe, list[Zone]]
    confluence: list[Confluence] = ()

    @property
    def all_zones(self) -> list[Zone]:
        return [z for values in self.zones.values() for z in values]


def _frame(daily: pl.DataFrame, timeframe: Timeframe) -> pl.DataFrame:
    if timeframe == Timeframe.DAILY:
        return daily
    if timeframe == Timeframe.WEEKLY:
        return aggregate_to_weekly(daily)
    return aggregate_to_monthly(daily)


def run_for_symbol(daily_candles: pl.DataFrame, symbol: str, *, configs: dict[Timeframe, DemandSupplyConfig], as_of_date: date, current_price: Decimal | None = None) -> SymbolZoneResult | None:
    daily = daily_candles.filter((pl.col("symbol") == symbol) & (pl.col("date") <= as_of_date)).sort("date")
    if daily.is_empty():
        return None
    session_count = daily.select(pl.col("date").n_unique()).item()
    daily_config = configs.get(Timeframe.DAILY)
    if daily_config and session_count < daily_config.min_sessions_history:
        return None
    if current_price is None:
        current_price = Decimal(str(daily.tail(1)["close"][0]))
    current_price = current_price.quantize(Decimal("0.01"))

    output: dict[Timeframe, list[Zone]] = {}
    for timeframe, config in configs.items():
        frame = _frame(daily, timeframe)
        required_bars = max(2, config.min_sessions_history if timeframe == Timeframe.DAILY else config.atr_period + config.base_max_candles + 1)
        if frame.select(pl.col("date").n_unique()).item() < required_bars:
            output[timeframe] = []
            continue
        raw = detect_zones(frame, timeframe, config, as_of_date)
        zones: list[Zone] = []
        for item in raw:
            zone = Zone(
                symbol=symbol,
                timeframe=timeframe,
                zone_type=item["zone_type"],
                zone_low=Decimal(str(item["zone_low"])).quantize(Decimal("0.01")),
                zone_high=Decimal(str(item["zone_high"])).quantize(Decimal("0.01")),
                base_start_date=item["base_start_date"],
                base_end_date=item["base_end_date"],
                displacement_date=item["displacement_date"],
                atr_at_creation=Decimal(str(item["atr_at_creation"])).quantize(Decimal("0.01")),
                displacement_atr=float(item["displacement_atr"]),
                volume_ratio=item["volume_ratio"],
                strength_score=0,
                status=ZoneStatus.ACTIVE,
                touch_count=0,
                first_tested_at=None,
                last_tested_at=None,
                created_at=item["created_at"],
                invalidated_at=None,
                base_quality=float(item.get("base_quality", 0.5)),
                structure_score=float(item.get("structure", 1.0)),
            )
            history = frame.filter((pl.col("date") > zone.created_at) & (pl.col("date") <= as_of_date))
            zone = update_zone_status(zone, history, config)
            zone = replace(zone, strength_score=score_zone(zone, config=config, as_of_date=as_of_date))
            zones.append(zone)
        output[timeframe] = zones

    all_zones = [z for values in output.values() for z in values]
    confluence = calculate_confluence(all_zones, current_price)
    return SymbolZoneResult(symbol, current_price, output, confluence)


def _eligible_symbols(panel: pl.DataFrame, as_of_date: date, min_history: int) -> list[str]:
    history = (
        panel.filter(pl.col("date") <= as_of_date)
        .group_by("symbol")
        .agg(pl.col("date").n_unique().alias("sessions"), pl.col("date").max().alias("last_date"))
        .filter(pl.col("sessions") >= min_history)
    )
    latest_session = history.select(pl.col("last_date").max()).item()
    symbols = history.filter(pl.col("last_date") == latest_session)["symbol"].to_list()
    excluded = set(DEFAULT_EXCLUSIONS) | set(KNOWN_EQUITIES)
    return sorted(symbol for symbol in symbols if symbol not in excluded and not looks_like_fund(symbol))


def run_for_universe(panel: pl.DataFrame, *, configs: dict[Timeframe, DemandSupplyConfig], as_of_date: date) -> list[SymbolZoneResult]:
    if panel.is_empty():
        return []
    min_price = Decimal(str(min(c.min_price for c in configs.values())))
    min_history = ZONES_MIN_SESSIONS
    symbols = _eligible_symbols(panel, as_of_date, min_history)
    out: list[SymbolZoneResult] = []
    for symbol in symbols:
        result = run_for_symbol(panel, symbol, configs=configs, as_of_date=as_of_date)
        if result is not None and result.current_price >= min_price:
            out.append(result)
    return out
