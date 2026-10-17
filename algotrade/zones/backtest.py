from __future__ import annotations
from collections import defaultdict
from datetime import timedelta
import polars as pl


def event_study_metrics(zones, panel, horizons=(5, 10, 20)):
    rows=[]
    for z in zones:
        bars = panel.filter((pl.col("symbol")==z.symbol) & (pl.col("date")>z.created_at)).sort("date").to_dicts()
        if not bars: continue
        base=float(z.zone_high-z.zone_low)
        first=bars[0]
        record={"timeframe":z.timeframe.value,"score":z.strength_score,"displacement_atr":z.displacement_atr,"volume_ratio":z.volume_ratio,"tested":z.touch_count>0,"broken":z.status.value=="broken","reaction":None}
        if z.touch_count and z.first_tested_at:
            after=[b for b in bars if b["date"]>=z.first_tested_at]
            if after: record["reaction"]=max((float(b["high"])-float(z.zone_high))/max(float(z.zone_high),1e-9) for b in after[:20])
        for h in horizons:
            future=bars[:h]
            record[f"favourable_move_{h}_sessions"]=max((float(b["high"])-float(z.zone_high))/max(float(z.zone_high),1e-9) for b in future) if future else None
        rows.append(record)
    if not rows:
        return {"overall":{"zones":0},"by_timeframe_score_displacement_distance_volume":{}}
    def summary(items):
        tested=sum(bool(x["tested"]) for x in items); broken=sum(bool(x["broken"]) for x in items)
        out={"zones":len(items),"tested":tested,"broken":broken,"failure_rate":broken/tested if tested else 0.0}
        for h in horizons:
            vals=[x[f"favourable_move_{h}_sessions"] for x in items if x[f"favourable_move_{h}_sessions"] is not None]
            out[f"average_favourable_move_{h}_sessions"]=sum(vals)/len(vals) if vals else None
        vals=[x["reaction"] for x in items if x["reaction"] is not None]
        out["average_reaction_after_first_test"]=sum(vals)/len(vals) if vals else None
        return out
    grouped=defaultdict(list)
    for row in rows: grouped[row["timeframe"]].append(row)
    return {"overall":summary(rows),"by_timeframe_score_displacement_distance_volume":{k:summary(v) for k,v in grouped.items()}}
