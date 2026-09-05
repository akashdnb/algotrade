"""Kite Connect, read-only.

Covers exactly the free Personal tier: orders, trades, holdings, funds. Live
and historical **market data are excluded** from that tier, and this client
does not reach for them -- the price panel comes from committed bhavcopy and
sizing prices from the public delayed feed (design §6).

`place()` exists to satisfy `BrokerClient` and **raises**. Submission is §15
step 4, and an implementation that quietly did nothing would be worse than one
that refuses: the caller would believe an order had been sent.

Written against `requests` rather than the `kiteconnect` package. Five
endpoints do not justify a dependency, and decoding their JSON here means a
change in their payload breaks in one file rather than in a template.

**Rate limited to 1 request/second.** Kite allows 10 orders/sec and 3 req/sec
generally, and counts rejected requests toward the limit -- so a retry storm
against a failing call is self-defeating. One per second is an order of
magnitude under the tighter ceiling and irrelevant to a human clicking.
"""

from __future__ import annotations

import csv
import io
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

import requests

from algotrade.brokers.base import (
    BrokerAuthError,
    BrokerError,
    BrokerFunds,
    BrokerHolding,
    BrokerOrder,
    BrokerTrade,
    OrderRequest,
)

BASE_URL: str = "https://api.kite.trade"
API_VERSION: str = "3"

#: Seconds between requests. See the module docstring.
MIN_INTERVAL: float = 1.0

#: Kite returns wall-clock strings in IST without an offset.
_TIMESTAMP_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
)


def _money(value: Any) -> Decimal:
    """Kite sends numbers as JSON floats. Convert via `str`, never `float`.

    `Decimal(2402.75)` from a float carries the binary representation error
    into a value that ends up in an execution record; `Decimal("2402.75")`
    does not.
    """
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise BrokerError(f"could not read {value!r} as money") from exc


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    for fmt in _TIMESTAMP_FORMATS:
        try:
            return datetime.strptime(str(value), fmt)
        except ValueError:
            continue
    return None


def _side(transaction_type: str) -> str:
    return "sell" if str(transaction_type).upper() == "SELL" else "buy"


class KiteBroker:
    """A read-only Kite Connect client. Satisfies `BrokerClient`."""

    def __init__(
        self,
        *,
        api_key: str,
        access_token: str,
        session: requests.Session | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        timeout: float = 10.0,
    ) -> None:
        if not api_key or not access_token:
            raise BrokerError("kite client needs both api_key and access_token")
        self._api_key = api_key
        self._access_token = access_token
        self._session = session or requests.Session()
        self._sleep = sleeper
        self._monotonic = monotonic
        self._timeout = timeout
        self._last_call: float | None = None

    # ------------------------------------------------------------ plumbing ---

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "X-Kite-Version": API_VERSION,
            "Authorization": f"token {self._api_key}:{self._access_token}",
        }

    def _throttle(self) -> None:
        """Space requests at least `MIN_INTERVAL` apart.

        The clock is read exactly once per request and the slept time added
        back, rather than read again after sleeping. One reading cannot
        disagree with another, which makes the spacing exact instead of
        approximately right.
        """
        now = self._monotonic()
        if self._last_call is not None:
            waited = now - self._last_call
            if waited < MIN_INTERVAL:
                self._sleep(MIN_INTERVAL - waited)
                now += MIN_INTERVAL - waited
        self._last_call = now

    def _get(self, path: str) -> Any:
        self._throttle()
        try:
            response = self._session.get(
                f"{BASE_URL}{path}", headers=self._headers,
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise BrokerError(f"GET {path} failed: {exc}") from exc

        if response.status_code == 403:
            raise BrokerAuthError(
                "the broker rejected this session. Tokens expire at 06:00 IST "
                "daily, and a master logout from Kite's web terminal ends one "
                "immediately. Authorise again."
            )
        if response.status_code == 429:
            raise BrokerError(f"GET {path}: 429 -- rate limited")
        if response.status_code >= 400:
            raise BrokerError(f"GET {path}: HTTP {response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise BrokerError(f"GET {path}: response was not JSON") from exc
        if payload.get("status") != "success":
            raise BrokerError(
                f"GET {path}: {payload.get('message', 'unsuccessful response')}"
            )
        return payload.get("data")

    # --------------------------------------------------------------- reads ---

    def holdings(self) -> list[BrokerHolding]:
        rows = self._get("/portfolio/holdings") or []
        return [
            BrokerHolding(
                symbol=row.get("tradingsymbol", ""),
                isin=row.get("isin", ""),
                quantity=int(row.get("quantity") or 0),
                average_price=_money(row.get("average_price")),
            )
            for row in rows
        ]

    def margins(self) -> BrokerFunds:
        """Equity segment only. The commodity segment is not traded here."""
        data = self._get("/user/margins/equity") or {}
        utilised = data.get("utilised") or {}
        return BrokerFunds(
            available=_money(data.get("net")),
            used=_money(utilised.get("debits")),
        )

    def orders(self, *, tag: str | None = None) -> list[BrokerOrder]:
        rows = self._get("/orders") or []
        orders = [
            BrokerOrder(
                order_id=str(row.get("order_id", "")),
                symbol=row.get("tradingsymbol", ""),
                side=_side(row.get("transaction_type", "")),
                quantity=int(row.get("quantity") or 0),
                filled_quantity=int(row.get("filled_quantity") or 0),
                status=str(row.get("status", "")).upper(),
                tag=row.get("tag") or "",
                average_price=_money(row.get("average_price")),
                status_message=row.get("status_message") or "",
            )
            for row in rows
        ]
        if tag is None:
            return orders
        return [o for o in orders if o.tag == tag]

    def trades(self, *, order_id: str) -> list[BrokerTrade]:
        rows = self._get(f"/orders/{order_id}/trades") or []
        return [
            BrokerTrade(
                trade_id=str(row.get("trade_id", "")),
                order_id=str(row.get("order_id", order_id)),
                symbol=row.get("tradingsymbol", ""),
                quantity=int(row.get("quantity") or 0),
                price=_money(row.get("average_price")),
                filled_at=_timestamp(row.get("fill_timestamp"))
                or _timestamp(row.get("exchange_timestamp"))
                or datetime.min,
            )
            for row in rows
        ]

    def instruments(self) -> list[dict[str, Any]]:
        """The full instrument dump, as dict rows.

        The only Kite endpoint that answers in CSV rather than JSON, and the
        only one whose response is measured in megabytes -- so it is parsed
        here and not held anywhere afterwards.

        Available on the free tier: it is reference data, not market data.
        """
        self._throttle()
        try:
            response = self._session.get(
                f"{BASE_URL}/instruments", headers=self._headers,
                timeout=max(self._timeout, 30.0),
            )
        except requests.RequestException as exc:
            raise BrokerError(f"GET /instruments failed: {exc}") from exc

        if response.status_code == 403:
            raise BrokerAuthError(
                "the broker rejected this session while fetching instruments"
            )
        if response.status_code >= 400:
            raise BrokerError(f"GET /instruments: HTTP {response.status_code}")

        rows = list(csv.DictReader(io.StringIO(response.text)))
        if not rows:
            # An empty dump would quietly empty the symbol map, and every
            # subsequent lookup would fail closed on every symbol.
            raise BrokerError("GET /instruments returned no rows")
        return rows

    # -------------------------------------------------------------- writes ---

    def place(self, req: OrderRequest) -> str:
        """Not built. Submission is design §15 step 4.

        Raises rather than returning a fake id: a caller that believed an order
        had been sent would record a fill that never happened.
        """
        raise NotImplementedError(
            "order submission is not built yet (design §15 step 4); "
            "this client is read-only"
        )
