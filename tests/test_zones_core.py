from datetime import date, timedelta
from decimal import Decimal
import polars as pl
import pytest
from algotrade.zones.aggregation import aggregate_to_weekly, aggregate_to_monthly, split_completed_current
from algotrade.zones.config import DemandSupplyConfig, Timeframe, ZoneStatus, ZoneType
from algotrade.zones.metrics import atr, candle_metrics
from algotrade.zones.detect import detect_zones
from algotrade.zones.lifecycle import invalidate_zone, register_zone_test, update_zone_status
from algotrade.zones.proximity import calculate_zone_distance, classify_zone_proximity
from algotrade.zones.lookahead import check_zones_no_lookahead
from algotrade.zones.models import Zone

def candles(n=8):
    start=date(2024,1,1)
    return pl.DataFrame([{"date":start+timedelta(days=i),"symbol":"ABC","open":10+i*0.01,"high":10.5+i*0.01,"low":9.5+i*0.01,"close":10.1+i*0.01,"volume":100.0} for i in range(n)])

def config(): return DemandSupplyConfig(Timeframe.DAILY, atr_period=3, minimum_displacement_atr=1.2, minimum_volume_ratio=1.0, min_sessions_history=1)

def test_aggregation_and_completed_split():
    d=candles(10); w=aggregate_to_weekly(d); m=aggregate_to_monthly(d)
    assert w.height >= 2 and m.height == 1
    completed,current=split_completed_current(w,date(2024,1,8),Timeframe.WEEKLY)
    assert completed.height == 1 and current is not None

def test_candle_metrics_and_wilder_atr():
    c=candles(5); m=candle_metrics(c); a=atr(c,3)
    assert m["range"][0] == pytest.approx(1.0)
    assert a["atr"][1] is None and a["atr"][2] is not None

def test_detection_is_cutoff_safe_and_idempotent():
    d=candles(10).with_columns(pl.when(pl.col("date")==date(2024,1,10)).then(12.0).otherwise(pl.col("close")).alias("close"),pl.when(pl.col("date")==date(2024,1,10)).then(13.0).otherwise(pl.col("high")).alias("high"),pl.when(pl.col("date")==date(2024,1,10)).then(500.0).otherwise(pl.col("volume")).alias("volume"))
    a=detect_zones(d,Timeframe.DAILY,config(),date(2024,1,10)); b=detect_zones(d,Timeframe.DAILY,config(),date(2024,1,10))
    assert [x["created_at"] for x in a] == [x["created_at"] for x in b]
    assert not detect_zones(d,Timeframe.DAILY,config(),date(2024,1,9))

def test_lifecycle_proximity_and_invalidation():
    z=Zone("ABC",Timeframe.DAILY,ZoneType.DEMAND,Decimal("9"),Decimal("10"),date(2024,1,1),date(2024,1,2),date(2024,1,3),Decimal("1"),2.0,2.0,80,ZoneStatus.ACTIVE,0,None,None,date(2024,1,3),None)
    assert register_zone_test(z,date(2024,1,4)).touch_count == 1
    assert invalidate_zone(z,{"low":8.5,"high":10.5,"close":8.5},config())
    assert classify_zone_proximity(z,Decimal("9.5"),config()).value == "in_zone"
    assert calculate_zone_distance(z,Decimal("9.5"))[0] == Decimal("0")

def test_lookahead_harness_compares_structured_zone_records():
    p=candles(6)
    def fn(panel, cutoff): return []
    assert check_zones_no_lookahead(p,fn,cutoffs=[date(2024,1,4)]).clean
