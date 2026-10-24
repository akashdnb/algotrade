import logging
from datetime import date

import polars as pl
import pytest

from algotrade.zones import universes
from algotrade.zones.pipeline import _eligible_symbols


def _panel(n=25):
    from datetime import timedelta

    start = date(2024, 1, 1)
    rows = []
    for i in range(n):
        day = start + timedelta(days=i)
        rows.append({"symbol": "LIQUIDCO", "date": day, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1_000_000.0, "turnover": 1.0e8})
        rows.append({"symbol": "THINCO", "date": day, "open": 50.0, "high": 50.5, "low": 49.5, "close": 50.0, "volume": 200.0, "turnover": 10_000.0})
    return pl.DataFrame(rows)


def _csv(tmp_path, symbols):
    path = tmp_path / "n500.csv"
    path.write_text("Company Name,Industry,Symbol,Series,ISIN Code\n" + "".join(f"{s} Ltd,X,{s},EQ,INE{i:09d}\n" for i, s in enumerate(symbols)))
    return path


def test_committed_snapshot_is_a_full_nifty500():
    symbols = universes.load_nifty500()
    assert 450 <= len(symbols) <= 550
    assert {"RELIANCE", "TCS", "INFY"} <= symbols


def test_members_for_names():
    assert universes.members_for("liquid") is None
    assert universes.members_for("nifty500") == universes.load_nifty500()
    with pytest.raises(ValueError, match="unknown universe"):
        universes.members_for("nifty50")


def test_stale_snapshot_warns(tmp_path, caplog):
    csv, meta = _csv(tmp_path, ["AAA"]), tmp_path / "meta.json"
    meta.write_text('{"fetched_on": "2024-01-01"}')
    with caplog.at_level(logging.WARNING):
        assert universes.load_nifty500(csv, meta, today=date(2025, 1, 1)) == {"AAA"}
    assert "refresh_nifty500" in caplog.text
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        universes.load_nifty500(csv, meta, today=date(2024, 2, 1))
    assert not caplog.text


def test_members_restrict_eligible_symbols_but_liquidity_still_applies():
    panel = _panel()
    as_of = panel["date"].max()
    kwargs = dict(min_turnover=5e7, min_coverage=0.8)
    assert _eligible_symbols(panel, as_of, members={"LIQUIDCO"}, **kwargs) == ["LIQUIDCO"]
    assert _eligible_symbols(panel, as_of, members={"OTHER"}, **kwargs) == []
    # THINCO is a member but fails the liquidity floor.
    assert _eligible_symbols(panel, as_of, members={"THINCO"}, **kwargs) == []
    assert _eligible_symbols(panel, as_of, members=None, **kwargs) == ["LIQUIDCO"]


def test_job_accepts_universe_flag():
    from jobs.detect_zones import build_parser

    assert build_parser().parse_args([]).universe == "liquid"
    assert build_parser().parse_args(["--universe", "nifty500"]).universe == "nifty500"
