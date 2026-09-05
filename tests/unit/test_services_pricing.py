"""Tests for execution-day price resolution (design §6).

The property that matters: **nothing is dropped quietly.** A symbol that
cannot be priced must arrive at the caller as a named failure, because
`construction.build` would otherwise size the 29 names it has and report the
thirtieth as "dropped" -- correct for a pure function, and a silent change from
a 30-stock book to a 29-stock one for anyone reading a screen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from algotrade.services import pricing

ON = date(2026, 10, 5)


@dataclass
class FakeMapping:
    nse_symbol: str
    feed_ticker: str


@dataclass
class FakeQuotes:
    opens: dict = field(default_factory=dict)
    missing: dict = field(default_factory=dict)


def _resolver(known: dict[str, str]):
    def resolve(symbol):
        ticker = known.get(symbol)
        return FakeMapping(symbol, ticker) if ticker else None
    return resolve


def _fetcher(opens=None, missing=None, record=None):
    def fetch(tickers, on):
        if record is not None:
            record.extend(tickers)
        return FakeQuotes(opens=dict(opens or {}), missing=dict(missing or {}))
    return fetch


def test_prices_come_back_keyed_by_nse_symbol_not_by_ticker():
    """Everything downstream speaks NSE symbols. A ticker leaking into that
    vocabulary would be a silent mismatch in `construction.build`."""
    got = pricing.resolve_opens(
        ["HFCL"], on=ON, resolve=_resolver({"HFCL": "HFCL.NS"}),
        fetch=_fetcher(opens={"HFCL.NS": Decimal("224.17")}))
    assert got.prices == {"HFCL": Decimal("224.17")}
    assert got.complete


def test_an_unmapped_symbol_is_named_not_guessed():
    """Appending `.NS` and hoping is how you price a different instrument and
    size a real order on it."""
    got = pricing.resolve_opens(
        ["HFCL", "GHOST"], on=ON, resolve=_resolver({"HFCL": "HFCL.NS"}),
        fetch=_fetcher(opens={"HFCL.NS": Decimal("224.17")}))
    assert got.unmapped == ["GHOST"]
    assert not got.complete


def test_a_mapping_without_a_feed_ticker_counts_as_unmapped():
    """A half-built map row must not produce a request for an empty ticker."""
    got = pricing.resolve_opens(
        ["HFCL"], on=ON, resolve=_resolver({"HFCL": ""}),
        fetch=_fetcher())
    assert got.unmapped == ["HFCL"]


def test_a_symbol_the_feed_could_not_price_carries_the_reason():
    got = pricing.resolve_opens(
        ["HFCL"], on=ON, resolve=_resolver({"HFCL": "HFCL.NS"}),
        fetch=_fetcher(missing={"HFCL.NS": "no open for 2026-10-05"}))
    assert got.unpriced == {"HFCL": "no open for 2026-10-05"}
    assert got.prices == {}


def test_both_kinds_of_failure_appear_in_one_list():
    got = pricing.resolve_opens(
        ["HFCL", "GHOST"], on=ON, resolve=_resolver({"HFCL": "HFCL.NS"}),
        fetch=_fetcher(missing={"HFCL.NS": "boom"}))
    assert got.failures == ["GHOST", "HFCL"]


def test_no_request_is_made_when_nothing_resolves():
    """Thirty unmapped names should not become thirty pointless requests."""
    asked: list[str] = []
    got = pricing.resolve_opens(
        ["A", "B"], on=ON, resolve=_resolver({}),
        fetch=_fetcher(record=asked))
    assert asked == []
    assert got.unmapped == ["A", "B"]


def test_tickers_are_requested_in_a_stable_order():
    """So a failing run can be compared against a previous one."""
    asked: list[str] = []
    pricing.resolve_opens(
        ["ZEEL", "ACC"], on=ON,
        resolve=_resolver({"ZEEL": "ZEEL.NS", "ACC": "ACC.NS"}),
        fetch=_fetcher(record=asked))
    assert asked == ["ACC.NS", "ZEEL.NS"]


def test_an_unexpected_ticker_in_the_response_is_ignored():
    """The feed answering about something we did not ask for must not inject a
    price for a symbol that is not in the basket."""
    got = pricing.resolve_opens(
        ["HFCL"], on=ON, resolve=_resolver({"HFCL": "HFCL.NS"}),
        fetch=_fetcher(opens={"HFCL.NS": Decimal("224.17"),
                              "SURPRISE.NS": Decimal("1.00")}))
    assert set(got.prices) == {"HFCL"}


def test_the_summary_counts_priced_against_the_whole_basket():
    got = pricing.resolve_opens(
        ["HFCL", "GHOST"], on=ON, resolve=_resolver({"HFCL": "HFCL.NS"}),
        fetch=_fetcher(opens={"HFCL.NS": Decimal("224.17")}))
    assert got.summary() == "1/2 priced for 2026-10-05"


def test_an_empty_basket_is_complete_rather_than_an_error():
    got = pricing.resolve_opens([], on=ON, resolve=_resolver({}),
                                fetch=_fetcher())
    assert got.complete
    assert got.prices == {}
