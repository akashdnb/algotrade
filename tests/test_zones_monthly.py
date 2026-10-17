from datetime import date

import polars as pl

from algotrade.zones.config import Timeframe, load_configs
from algotrade.zones.detect import detect_zones


def test_monthly_config_is_not_starved_by_sparse_monthly_history():
    configs = load_configs(__import__("pathlib").Path("config/zones/zones_v1.yaml"))
    config = configs[Timeframe.MONTHLY]

    rows = []
    for i in range(18):
        rows.append(
            {
                "date": date(2024 + (i // 12), (i % 12) + 1, 1),
                "symbol": "RELIANCE",
                "open": 100.0,
                "high": 110.0,
                "low": 95.0,
                "close": 105.0,
                "volume": 100.0,
            }
        )
    rows[-3] = {
        "date": date(2025, 4, 1),
        "symbol": "RELIANCE",
        "open": 100.0,
        "high": 110.0,
        "low": 95.0,
        "close": 105.0,
        "volume": 100.0,
    }
    rows[-2] = {
        "date": date(2025, 5, 1),
        "symbol": "RELIANCE",
        "open": 100.0,
        "high": 110.0,
        "low": 95.0,
        "close": 105.0,
        "volume": 100.0,
    }
    rows[-1] = {
        "date": date(2025, 6, 1),
        "symbol": "RELIANCE",
        "open": 105.0,
        "high": 138.0,
        "low": 104.0,
        "close": 135.0,
        "volume": 250.0,
    }

    candles = pl.DataFrame(rows)
    zones = detect_zones(candles, Timeframe.MONTHLY, config, date(2025, 6, 1))

    assert zones
    assert any(zone["zone_type"] == "demand" for zone in zones)
