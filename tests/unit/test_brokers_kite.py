"""Tests for the Kite client's decoding and throttling (design §4).

No network: a fake session returns canned payloads shaped like Kite's. What is
being tested is the boundary — that their JSON becomes our types correctly, and
that a bad response fails here with a message that says what happened rather
than surfacing as a mysterious value three layers up.

The money test is the one that matters most. Kite sends prices as JSON floats,
and `Decimal(2402.75)` from a float carries binary representation error into a
value that ends up in a permanent execution record.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from algotrade.brokers import kite
from algotrade.brokers.base import BrokerError, LIMIT, OrderRequest


class FakeResponse:
    def __init__(self, payload, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self._text = text

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    """Records requests and returns queued responses."""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append((url, headers or {}))
        return self._responses.pop(0) if self._responses else FakeResponse(
            {"status": "success", "data": []})


def _client(*responses, sleeper=None, monotonic=None):
    clock = {"t": 0.0}

    def _mono():
        return clock["t"]

    return kite.KiteBroker(
        api_key="key123", access_token="tok456",
        session=FakeSession(*responses),
        sleeper=sleeper or (lambda _: None),
        monotonic=monotonic or _mono,
    )


def _ok(data):
    return FakeResponse({"status": "success", "data": data})


# ----------------------------------------------------------- construction ---

def test_a_client_without_credentials_is_refused():
    with pytest.raises(BrokerError, match="api_key and access_token"):
        kite.KiteBroker(api_key="", access_token="tok")


def test_the_authorization_header_carries_key_and_token():
    client = _client(_ok([]))
    client.holdings()
    _, headers = client._session.calls[0]
    assert headers["Authorization"] == "token key123:tok456"
    assert headers["X-Kite-Version"] == "3"


# ---------------------------------------------------------------- money ---

def test_prices_are_read_via_str_not_float():
    """Decimal(2402.75) from a float is 2402.7499999...; from "2402.75" it is
    exact. This value ends up in a permanent execution record."""
    client = _client(_ok([{"tradingsymbol": "RELIANCE", "isin": "INE002A01018",
                           "quantity": 3, "average_price": 2402.75}]))
    assert client.holdings()[0].average_price == Decimal("2402.75")


def test_an_unreadable_price_is_refused_rather_than_defaulted():
    client = _client(_ok([{"tradingsymbol": "X", "isin": "I", "quantity": 1,
                           "average_price": "not a number"}]))
    with pytest.raises(BrokerError, match="as money"):
        client.holdings()


def test_a_missing_price_reads_as_zero_not_none():
    client = _client(_ok([{"tradingsymbol": "X", "isin": "I", "quantity": 1}]))
    assert client.holdings()[0].average_price == Decimal("0")


# ------------------------------------------------------------- holdings ---

def test_holdings_decode_symbol_isin_and_quantity():
    client = _client(_ok([{"tradingsymbol": "HFCL", "isin": "INE548A01028",
                           "quantity": 30, "average_price": 224.17}]))
    held = client.holdings()[0]
    assert (held.symbol, held.isin, held.quantity) == ("HFCL",
                                                       "INE548A01028", 30)


def test_an_empty_holdings_payload_is_an_empty_list_not_an_error():
    assert _client(_ok(None)).holdings() == []


# -------------------------------------------------------------- margins ---

def test_margins_read_net_as_available_and_debits_as_used():
    client = _client(_ok({"net": 47000.5,
                          "utilised": {"debits": 3000.25}}))
    funds = client.margins()
    assert funds.available == Decimal("47000.5")
    assert funds.used == Decimal("3000.25")


def test_margins_without_a_utilised_block_report_zero_used():
    client = _client(_ok({"net": 100.0}))
    assert client.margins().used == Decimal("0")


# --------------------------------------------------------------- orders ---

def _order_row(**kwargs):
    row = {"order_id": 251001000123456, "tradingsymbol": "RELIANCE",
           "transaction_type": "BUY", "quantity": 10, "filled_quantity": 10,
           "status": "COMPLETE", "tag": "set:1", "average_price": 2402.75}
    row.update(kwargs)
    return row


def test_orders_decode_side_from_transaction_type():
    client = _client(_ok([_order_row(transaction_type="SELL")]))
    assert client.orders()[0].side == "sell"


def test_an_order_id_arriving_as_a_number_becomes_a_string():
    """Kite has sent both. A numeric id would not compare equal to the string
    stored on a submission row, and the idempotency check would miss."""
    assert _client(_ok([_order_row()])).orders()[0].order_id == "251001000123456"


def test_orders_can_be_filtered_by_tag():
    client = _client(_ok([_order_row(tag="set:1"),
                          _order_row(tag="set:2")]))
    assert len(client.orders(tag="set:2")) == 1


def test_an_order_with_no_tag_does_not_match_a_tag_filter():
    client = _client(_ok([_order_row(tag=None)]))
    assert client.orders(tag="set:1") == []


def test_a_rejected_order_carries_its_status_message():
    client = _client(_ok([_order_row(status="REJECTED", filled_quantity=0,
                                     status_message="Insufficient funds")]))
    order = client.orders()[0]
    assert order.terminal and not order.partial
    assert order.status_message == "Insufficient funds"


# --------------------------------------------------------------- trades ---

def test_trades_decode_price_and_fill_time():
    client = _client(_ok([{"trade_id": "t1", "order_id": "o1",
                           "tradingsymbol": "RELIANCE", "quantity": 3,
                           "average_price": 2404.1,
                           "fill_timestamp": "2026-10-01 09:20:15"}]))
    trade = client.trades(order_id="o1")[0]
    assert trade.price == Decimal("2404.1")
    assert trade.filled_at == datetime(2026, 10, 1, 9, 20, 15)


def test_a_trade_falls_back_to_the_exchange_timestamp():
    client = _client(_ok([{"trade_id": "t1", "order_id": "o1",
                           "tradingsymbol": "X", "quantity": 1,
                           "average_price": 1.0,
                           "exchange_timestamp": "2026-10-01 09:20:15"}]))
    assert client.trades(order_id="o1")[0].filled_at == datetime(
        2026, 10, 1, 9, 20, 15)


# --------------------------------------------------------------- errors ---

def test_a_403_says_the_token_expired_rather_than_just_forbidden():
    """This is the failure that happens most, and the message is the whole
    difference between a five-second fix and a confused morning."""
    client = _client(FakeResponse({}, status_code=403))
    with pytest.raises(BrokerError, match="06:00 IST"):
        client.holdings()


def test_a_429_is_named_as_rate_limiting():
    client = _client(FakeResponse({}, status_code=429))
    with pytest.raises(BrokerError, match="rate limited"):
        client.holdings()


def test_a_non_json_response_is_refused():
    client = _client(FakeResponse(None))
    with pytest.raises(BrokerError, match="not JSON"):
        client.holdings()


def test_an_unsuccessful_payload_surfaces_the_brokers_message():
    client = _client(FakeResponse({"status": "error",
                                   "message": "Something broke"}))
    with pytest.raises(BrokerError, match="Something broke"):
        client.holdings()


# ------------------------------------------------------------ throttling ---

def test_the_first_call_does_not_wait():
    slept: list[float] = []
    client = _client(_ok([]), sleeper=slept.append)
    client.holdings()
    assert slept == []


def test_a_second_immediate_call_waits_a_full_interval():
    slept: list[float] = []
    client = _client(_ok([]), _ok([]), sleeper=slept.append,
                     monotonic=lambda: 0.0)
    client.holdings()
    client.holdings()
    assert slept == [kite.MIN_INTERVAL]


def test_a_call_after_the_interval_does_not_wait():
    slept: list[float] = []
    ticks = iter([0.0, 5.0])
    client = _client(_ok([]), _ok([]), sleeper=slept.append,
                     monotonic=lambda: next(ticks))
    client.holdings()
    client.holdings()
    assert slept == []


# ---------------------------------------------------------------- place ---

def test_place_refuses_rather_than_pretending():
    """A no-op that returned a fake id would have the caller record a fill
    that never happened."""
    client = _client()
    with pytest.raises(NotImplementedError, match="read-only"):
        client.place(OrderRequest(symbol="RELIANCE", side="buy", quantity=1,
                                  order_type=LIMIT, tag="t",
                                  price=Decimal("1")))
