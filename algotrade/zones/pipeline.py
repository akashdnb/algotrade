from __future__ import annotations

from dataclasses import dataclass, replace
from collections.abc import Collection
from datetime import date, timedelta
from decimal import Decimal

import polars as pl

from algotrade.core.universe import DEFAULT_EXCLUSIONS, liquidity, looks_like_fund

from .aggregation import aggregate_to_monthly, aggregate_to_weekly
from .config import DemandSupplyConfig, Timeframe, ZoneStatus
from .confluence import Confluence, calculate_confluence
from .detect import detect_zones
from .hygiene import dedupe_zones, drop_expired
from .lifecycle import update_zone_status
from .models import Zone
from .scoring import score_zone

# Minimum complete daily history required by the production panel loader. This
# is a data-quality floor, not a universe-eligibility rule: weekly/monthly
# detection can still run for symbols with fewer daily sessions when the caller
# supplies a smaller historical panel.
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


def _completed_frame(daily: pl.DataFrame, timeframe: Timeframe, as_of_date: date) -> pl.DataFrame:
    if timeframe == Timeframe.DAILY:
        return daily
    frame = aggregate_to_weekly(daily) if timeframe == Timeframe.WEEKLY else aggregate_to_monthly(daily)
    if frame.is_empty():
        return frame
    if timeframe == Timeframe.WEEKLY:
        current_key = as_of_date - timedelta(days=as_of_date.weekday())
        period = "1w"
    else:
        current_key = as_of_date.replace(day=1)
        period = "1mo"
    return (
        frame.with_columns(pl.col("date").dt.truncate(period).alias("_period"))
        .filter(pl.col("_period") < pl.lit(current_key))
        .drop("_period")
    )


def _lifecycle_frame(frame: pl.DataFrame, daily: pl.DataFrame, timeframe: Timeframe) -> pl.DataFrame:
    """Candles that drive zone status: completed bars plus the current partial period's daily bars."""
    columns = ["date", "low", "high", "close"]
    if timeframe == Timeframe.DAILY or frame.is_empty():
        return frame.select(columns)
    partial = daily.filter(pl.col("date") > frame["date"].max()).select(columns)
    return pl.concat([frame.select(columns), partial])


def _required_bars(timeframe: Timeframe, config: DemandSupplyConfig) -> int:
    if timeframe == Timeframe.DAILY:
        return max(2, config.min_sessions_history)
    return max(2, config.atr_period + config.base_max_candles + config.expansion_max_candles)


def detect_symbol_zones(daily: pl.DataFrame, symbol: str, *, configs: dict[Timeframe, DemandSupplyConfig], as_of_date: date) -> dict[Timeframe, list[Zone]]:
    """Raw zones per timeframe, formed from completed candles only.

    No lifecycle, expiry or de-duplication is applied, so every zone is exactly
    as it was on its creation date. `daily` must hold this symbol's rows through
    `as_of_date`, sorted by date.
    """
    output: dict[Timeframe, list[Zone]] = {}
    for timeframe, config in configs.items():
        frame = _completed_frame(daily, timeframe, as_of_date)
        if frame.select(pl.col("date").n_unique()).item() < _required_bars(timeframe, config):
            output[timeframe] = []
            continue
        output[timeframe] = [
            Zone(
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
            for item in detect_zones(frame, timeframe, config, as_of_date)
        ]
    return output


def run_for_symbol(daily_candles: pl.DataFrame, symbol: str, *, configs: dict[Timeframe, DemandSupplyConfig], as_of_date: date, current_price: Decimal | None = None) -> SymbolZoneResult | None:
    daily = daily_candles.filter((pl.col("symbol") == symbol) & (pl.col("date") <= as_of_date)).sort("date")
    if daily.is_empty():
        return None
    if current_price is None:
        current_price = Decimal(str(daily.tail(1)["close"][0]))
    current_price = current_price.quantize(Decimal("0.01"))

    output: dict[Timeframe, list[Zone]] = {}
    for timeframe, raw_zones in detect_symbol_zones(daily, symbol, configs=configs, as_of_date=as_of_date).items():
        config = configs[timeframe]
        frame = _completed_frame(daily, timeframe, as_of_date)
        # Zones form from completed candles only, but the still-forming weekly or
        # monthly period's daily bars must be able to test and invalidate them.
        lifecycle = _lifecycle_frame(frame, daily, timeframe)
        zones: list[Zone] = []
        ages: dict[str, int] = {}
        for zone in raw_zones:
            history = lifecycle.filter((pl.col("date") > zone.created_at) & (pl.col("date") <= as_of_date))
            ages[zone.zone_id] = history.height
            zone = update_zone_status(zone, history, config)
            zone = replace(zone, strength_score=score_zone(zone, config=config, as_of_date=as_of_date, age_bars=history.height))
            zones.append(zone)
        output[timeframe] = dedupe_zones(drop_expired(zones, ages, config.max_age_bars), config.dedupe_overlap)

    all_zones = [z for values in output.values() for z in values]
    confluence = calculate_confluence(all_zones, current_price)
    return SymbolZoneResult(symbol, current_price, output, confluence)


def _eligible_symbols(
    panel: pl.DataFrame, as_of_date: date, *, min_turnover: float = 0.0, min_coverage: float = 0.0, members: Collection[str] | None = None
) -> list[str]:
    visible = panel.filter(pl.col("date") <= as_of_date)
    history = visible.group_by("symbol").agg(pl.col("date").n_unique().alias("sessions"), pl.col("date").max().alias("last_date"))
    if history.is_empty():
        return []
    latest_session = history.select(pl.col("last_date").max()).item()
    symbols = history.filter(pl.col("last_date") == latest_session)["symbol"].to_list()
    # KNOWN_EQUITIES is an allow-list of real companies with fund-like names; it
    # must never be excluded here.
    excluded = set(DEFAULT_EXCLUSIONS)
    candidates = {symbol for symbol in symbols if symbol not in excluded and not looks_like_fund(symbol)}
    if members is not None:
        candidates &= set(members)
    if min_turnover > 0 or min_coverage > 0:
        # Point-in-time: adtv/coverage at `latest_session` look back only, no
        # look-ahead. A thin name left in lets `_volume_ratio`'s near-zero
        # denominator produce meaningless ratios (seen as high as 16,000x in
        # the top score bucket), which saturate the volume score same as a
        # genuine 2x and correlate with a worse, not better, failure rate.
        liquid = liquidity(visible).filter(pl.col("date") == latest_session)
        passing = liquid.filter(
            pl.col("adtv").is_not_null()
            & (pl.col("adtv") >= min_turnover)
            & (pl.col("coverage") >= min_coverage)
        )["symbol"].to_list()
        candidates &= set(passing)
    return sorted(candidates)


def run_for_universe(panel: pl.DataFrame, *, configs: dict[Timeframe, DemandSupplyConfig], as_of_date: date, members: Collection[str] | None = None) -> list[SymbolZoneResult]:
    """Zones for every eligible symbol; `members` further restricts to a named universe (e.g. Nifty 500)."""
    if panel.is_empty():
        return []
    min_price = Decimal(str(min(c.min_price for c in configs.values())))
    min_turnover = min(c.min_turnover for c in configs.values())
    min_coverage = min(c.min_coverage for c in configs.values())
    out: list[SymbolZoneResult] = []
    for symbol in _eligible_symbols(panel, as_of_date, min_turnover=min_turnover, min_coverage=min_coverage, members=members):
        result = run_for_symbol(panel, symbol, configs=configs, as_of_date=as_of_date)
        if result is not None and result.current_price >= min_price:
            out.append(result)
    return out
