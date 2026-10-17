from datetime import date, timedelta

import polars as pl

from algotrade.zones.lookahead import check_zones_no_lookahead


def test_harness_detects_future_dependent_zone_records():
    panel = pl.DataFrame([{"date": date(2024,1,1) + timedelta(days=i), "symbol": "ABC"} for i in range(5)])

    def leaked(frame, cutoff):
        future_visible = frame["date"].max() > cutoff
        return [{"zone_id": "z", "zone_low": 2 if future_visible else 1, "zone_high": 3, "created_at": date(2024,1,2), "status": "active"}]

    report = check_zones_no_lookahead(panel, leaked, cutoffs=[date(2024,1,3), date(2024,1,5)])
    assert report.clean is False
    assert report.failures[0]["cutoff"] == date(2024,1,3)
