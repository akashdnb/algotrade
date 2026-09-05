"""The delayed public price feed, used for one thing: the execution-day open.

**Why a delayed feed is enough.** `backtest.engine` fills at `open(D+1)` --
`open.shift(-1)` in `_fill_prices` -- and an opening price is a *static
historical number* fifteen minutes after the bell. Nothing here needs a live
tick, which is what makes the free Kite Personal tier sufficient and a
licensed real-time subscription unnecessary.

**A feed price must never enter stored state.** It sizes an order and is then
discarded. Sizing quantities are derived from it, but valuation, the panel and
every snapshot stay on committed bhavcopy -- if a scraped price ever landed in
a snapshot, `panel_manifest_hash` and signal parity would stop meaning
anything.

**Failures are returned, not swallowed.** `opens()` reports which symbols it
could not price so the caller can refuse to trade them. A feed that quietly
omitted a symbol would let a rebalance size 29 names and call it 30.

The date is checked against what the feed actually returned. Yahoo answers a
range, not a day, and taking the last row would silently size on a stale price
on any morning the feed lagged -- the failure would look like a small price
move rather than a wrong day.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Callable, Iterable, Sequence

from algotrade.domain import money
from algotrade.platform import clock

log = logging.getLogger("io.feed")

CHART_URL: str = "https://query1.finance.yahoo.com/v8/finance/chart/"

#: Yahoo rejects the default urllib agent.
USER_AGENT: str = "Mozilla/5.0 (compatible; algotrade/1.0)"

#: Seconds between requests. Thirty symbols is not a load, but a tight loop
#: against a free endpoint is how access gets withdrawn.
MIN_INTERVAL: float = 0.5

#: How far back to ask.
#:
#: A month, not a week. Correctness does not depend on this being narrow --
#: `parse_open` matches the row's own date, so a longer range cannot cause a
#: wrong row to be chosen. What a narrow range *does* cause is a verification
#: re-run failing after a long weekend or an outage, reporting "no open" for
#: every symbol and looking exactly like a broken feed.
RANGE: str = "1mo"


class FeedError(RuntimeError):
    """The feed could not be reached or could not be understood."""


@dataclass
class Quotes:
    """What the feed could and could not price."""

    opens: dict[str, Decimal] = field(default_factory=dict)
    #: symbol -> why. Never merged into `opens`; the caller must fail closed.
    missing: dict[str, str] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return not self.missing


def _decimal(value: object) -> Decimal | None:
    """Via `str`, never `float`, then quantized to paise.

    The feed serves float32, so a ₹1304.10 open arrives as
    `1304.0999755859375`. Carrying that is precision theatre -- it implies an
    accuracy the source does not have, and NSE prices are paise. Quantizing
    once here means every downstream comparison and display works on the same
    number rather than on a different rounding of it.
    """
    if value is None:
        return None
    try:
        out = money.rupees(Decimal(str(value)))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return out if out > 0 else None


def _fetch(url: str, *, timeout: float) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        raise FeedError(f"HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise FeedError(str(exc)) from exc
    except json.JSONDecodeError as exc:
        raise FeedError("response was not JSON") from exc


def parse_open(payload: dict, *, on: date) -> Decimal | None:
    """The opening price for `on`, or None if the feed did not carry it.

    Matches on the row's own date rather than taking the newest. The feed
    returns a range and lags on some mornings; the newest row is then
    yesterday, and sizing on it would read as a small price move rather than
    as the wrong day.
    """
    try:
        result = (payload.get("chart") or {}).get("result") or []
        if not result:
            return None
        block = result[0]
        stamps = block.get("timestamp") or []
        quote = ((block.get("indicators") or {}).get("quote") or [{}])[0]
        values = quote.get("open") or []
    except (AttributeError, IndexError, TypeError):
        return None

    for stamp, value in zip(stamps, values):
        try:
            when = datetime.fromtimestamp(stamp, tz=clock.IST).date()
        except (OSError, OverflowError, ValueError, TypeError):
            continue
        if when == on:
            return _decimal(value)
    return None


def opens(
    tickers: Iterable[str],
    *,
    on: date,
    timeout: float = 10.0,
    sleeper: Callable[[float], None] = time.sleep,
    fetch: Callable[[str], dict] | None = None,
) -> Quotes:
    """Opening price on `on` for each ticker, and why any is absent.

    `tickers` are feed tickers (`HFCL.NS`), not NSE symbols -- the translation
    belongs to `symbol_map`, so a name this system cannot identify never
    reaches the network.
    """
    getter = fetch or (lambda url: _fetch(url, timeout=timeout))
    quotes = Quotes()
    first = True

    for ticker in tickers:
        if not ticker:
            continue
        if not first:
            sleeper(MIN_INTERVAL)
        first = False

        url = f"{CHART_URL}{ticker}?interval=1d&range={RANGE}"
        try:
            payload = getter(url)
        except FeedError as exc:
            quotes.missing[ticker] = str(exc)
            continue

        price = parse_open(payload, on=on)
        if price is None:
            quotes.missing[ticker] = f"no open for {on.isoformat()}"
        else:
            quotes.opens[ticker] = price

    return quotes


@dataclass(frozen=True, slots=True)
class Disagreement:
    """A sized-on price that the official open later contradicted."""

    symbol: str
    feed: Decimal
    official: Decimal

    @property
    def bps(self) -> int:
        if self.official <= 0:
            return 0
        return int(abs(self.feed - self.official) / self.official * 10_000)

    def __str__(self) -> str:
        return (f"{self.symbol}: sized on {self.feed}, official open "
                f"{self.official} ({self.bps} bps)")


def verify(
    sized_on: dict[str, Decimal],
    official: dict[str, Decimal],
    *,
    tolerance_bps: int = 50,
) -> tuple[list[Disagreement], list[str]]:
    """Compare feed prices against the official opens from bhavcopy.

    Returns disagreements beyond `tolerance_bps`, and symbols bhavcopy did not
    carry. Run the session after execution, when the official file lands: the
    price we actually traded on then becomes checkable after the fact, the
    same shape as the holiday-duplicate check.

    A wrong symbol mapping shows up here as a very large disagreement rather
    than as a plausible one, because it is a different company's price.
    """
    bad: list[Disagreement] = []
    unchecked: list[str] = []
    for symbol, feed_price in sized_on.items():
        truth = official.get(symbol)
        if truth is None or truth <= 0:
            unchecked.append(symbol)
            continue
        found = Disagreement(symbol=symbol, feed=feed_price, official=truth)
        if found.bps > tolerance_bps:
            bad.append(found)
    return bad, sorted(unchecked)
