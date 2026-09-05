"""Tests for the delayed price feed (design §6).

No network: canned payloads shaped like Yahoo's chart response.

The property this file exists for is the **date match**. The feed answers a
range, not a day, and lags on some mornings. Taking the newest row would then
size on yesterday's open -- and that failure reads as a small price move, not
as the wrong day, which is exactly the kind of wrongness nobody notices.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from algotrade.io import feed
from algotrade.platform import clock

MONDAY = date(2026, 10, 5)
TUESDAY = date(2026, 10, 6)


def _stamp(day: date, hour: int = 9) -> int:
    return int(datetime(day.year, day.month, day.day, hour, 15,
                        tzinfo=clock.IST).timestamp())


def _payload(*rows) -> dict:
    """rows are (date, open) pairs."""
    return {"chart": {"result": [{
        "timestamp": [_stamp(d) for d, _ in rows],
        "indicators": {"quote": [{"open": [o for _, o in rows]}]},
    }]}}


# ----------------------------------------------------------- parse_open ---

def test_the_open_for_the_requested_day_is_returned():
    got = feed.parse_open(_payload((MONDAY, 224.17)), on=MONDAY)
    assert got == Decimal("224.17")


def test_the_row_is_chosen_by_date_not_by_being_newest():
    """The defect this guards: a feed that lags returns yesterday as its
    newest row, and sizing on it looks like a small move, not a wrong day."""
    payload = _payload((MONDAY, 224.17), (TUESDAY, 231.40))
    assert feed.parse_open(payload, on=MONDAY) == Decimal("224.17")


def test_a_day_the_feed_does_not_carry_is_none_not_the_nearest():
    payload = _payload((MONDAY, 224.17))
    assert feed.parse_open(payload, on=TUESDAY) is None


def test_prices_are_read_via_str_not_float():
    """Decimal(224.17) from a float is 224.16999...; it becomes a share count."""
    assert feed.parse_open(_payload((MONDAY, 224.17)), on=MONDAY) == Decimal(
        "224.17")


def test_float32_noise_is_quantized_to_paise():
    """The feed serves float32: a real 1304.10 open arrives as
    1304.0999755859375. Carrying that implies an accuracy the source does not
    have, and NSE prices are paise."""
    got = feed.parse_open(_payload((MONDAY, 1304.0999755859375)), on=MONDAY)
    assert got == Decimal("1304.10")
    assert got.as_tuple().exponent == -2


def test_quantizing_happens_once_so_comparisons_agree():
    """Two roundings of the same number would make verify() disagree with the
    price that was actually sized on."""
    payload = _payload((MONDAY, 3158.699951171875))
    assert feed.parse_open(payload, on=MONDAY) == Decimal("3158.70")


def test_a_null_open_is_treated_as_absent():
    """Yahoo nulls the open on a session it has no data for."""
    assert feed.parse_open(_payload((MONDAY, None)), on=MONDAY) is None


def test_a_non_positive_open_is_treated_as_absent():
    """Zero would size an infinite quantity; negative is nonsense."""
    assert feed.parse_open(_payload((MONDAY, 0)), on=MONDAY) is None


def test_an_empty_result_is_absent_rather_than_an_error():
    assert feed.parse_open({"chart": {"result": []}}, on=MONDAY) is None


def test_a_malformed_payload_is_absent_rather_than_an_exception():
    """An unexpected shape must not take down a 30-symbol fetch."""
    assert feed.parse_open({"chart": None}, on=MONDAY) is None
    assert feed.parse_open({}, on=MONDAY) is None


def test_the_timestamp_is_read_in_ist():
    """An epoch stamp for 09:15 IST is the previous UTC day. Reading it in UTC
    would match the wrong session on every single row."""
    payload = _payload((MONDAY, 224.17))
    assert feed.parse_open(payload, on=MONDAY) == Decimal("224.17")
    assert feed.parse_open(payload, on=date(2026, 10, 4)) is None


# ---------------------------------------------------------------- opens ---

def _fetcher(mapping):
    def fetch(url):
        for ticker, payload in mapping.items():
            if f"/{ticker}?" in url:
                if isinstance(payload, Exception):
                    raise payload
                return payload
        raise feed.FeedError("unexpected url")
    return fetch


def test_every_ticker_is_priced_when_the_feed_answers():
    quotes = feed.opens(
        ["HFCL.NS", "ACC.NS"], on=MONDAY, sleeper=lambda _: None,
        fetch=_fetcher({"HFCL.NS": _payload((MONDAY, 224.17)),
                        "ACC.NS": _payload((MONDAY, 1850.0))}))
    assert quotes.opens == {"HFCL.NS": Decimal("224.17"),
                            "ACC.NS": Decimal("1850.0")}
    assert quotes.complete


def test_a_failed_symbol_is_reported_not_omitted():
    """A feed that quietly dropped one would let a rebalance size 29 names and
    call it 30."""
    quotes = feed.opens(
        ["HFCL.NS", "GONE.NS"], on=MONDAY, sleeper=lambda _: None,
        fetch=_fetcher({"HFCL.NS": _payload((MONDAY, 224.17)),
                        "GONE.NS": feed.FeedError("HTTP 404")}))
    assert set(quotes.opens) == {"HFCL.NS"}
    assert quotes.missing == {"GONE.NS": "HTTP 404"}
    assert not quotes.complete


def test_a_symbol_the_feed_has_no_open_for_is_reported():
    quotes = feed.opens(
        ["HFCL.NS"], on=TUESDAY, sleeper=lambda _: None,
        fetch=_fetcher({"HFCL.NS": _payload((MONDAY, 224.17))}))
    assert quotes.opens == {}
    assert "no open for 2026-10-06" in quotes.missing["HFCL.NS"]


def test_one_failure_does_not_stop_the_rest():
    quotes = feed.opens(
        ["A.NS", "B.NS", "C.NS"], on=MONDAY, sleeper=lambda _: None,
        fetch=_fetcher({"A.NS": feed.FeedError("boom"),
                        "B.NS": _payload((MONDAY, 10.0)),
                        "C.NS": _payload((MONDAY, 20.0))}))
    assert set(quotes.opens) == {"B.NS", "C.NS"}


def test_requests_are_spaced_but_the_first_does_not_wait():
    slept: list[float] = []
    feed.opens(["A.NS", "B.NS"], on=MONDAY, sleeper=slept.append,
               fetch=_fetcher({"A.NS": _payload((MONDAY, 1.0)),
                               "B.NS": _payload((MONDAY, 2.0))}))
    assert slept == [feed.MIN_INTERVAL]


def test_an_empty_ticker_is_skipped_without_a_request():
    calls: list[str] = []

    def fetch(url):
        calls.append(url)
        return _payload((MONDAY, 1.0))

    feed.opens(["", None], on=MONDAY, sleeper=lambda _: None, fetch=fetch)
    assert calls == []


# --------------------------------------------------------------- verify ---

def test_matching_prices_produce_no_disagreement():
    bad, unchecked = feed.verify({"HFCL": Decimal("224.17")},
                                 {"HFCL": Decimal("224.17")})
    assert bad == [] and unchecked == []


def test_a_small_difference_is_within_tolerance():
    """The feed and the exchange round differently; a few paise is not news."""
    bad, _ = feed.verify({"HFCL": Decimal("224.17")},
                         {"HFCL": Decimal("224.20")})
    assert bad == []


def test_a_large_difference_is_reported_with_its_size():
    bad, _ = feed.verify({"HFCL": Decimal("224.17")},
                         {"HFCL": Decimal("250.00")})
    assert len(bad) == 1
    assert bad[0].bps > 1000
    assert "sized on 224.17" in str(bad[0])


def test_a_wrong_symbol_mapping_shows_up_as_an_enormous_gap():
    """The point of running this at all: a mapping error is a different
    company's price, not a plausible one."""
    bad, _ = feed.verify({"HFCL": Decimal("2402.75")},
                         {"HFCL": Decimal("224.17")})
    assert bad and bad[0].bps > 90_00


def test_a_symbol_bhavcopy_does_not_carry_is_unchecked_not_clean():
    """Silently passing it would let the check report success for a price it
    never verified."""
    bad, unchecked = feed.verify({"HFCL": Decimal("224.17")}, {})
    assert bad == []
    assert unchecked == ["HFCL"]


def test_a_zero_official_price_is_unchecked_rather_than_infinite():
    bad, unchecked = feed.verify({"HFCL": Decimal("224.17")},
                                 {"HFCL": Decimal("0")})
    assert bad == [] and unchecked == ["HFCL"]


@pytest.mark.parametrize("tolerance,expected", [(50, 1), (500, 0)])
def test_tolerance_is_configurable(tolerance, expected):
    bad, _ = feed.verify({"HFCL": Decimal("224.17")},
                         {"HFCL": Decimal("230.00")},
                         tolerance_bps=tolerance)
    assert len(bad) == expected
