"""Execution-day prices: NSE symbols in, opening prices out.

Two steps, and both can fail in ways that must not be silent:

1. **Resolve** each NSE symbol to a feed ticker through `symbol_map`. A symbol
   with no mapping is reported, never guessed -- appending `.NS` and hoping is
   how you fetch a price for a different instrument and size a real order on
   it.
2. **Fetch** the opening price for the execution date.

**Nothing is dropped quietly.** `construction.build` will happily size the 29
names it has prices for and report the thirtieth as dropped, which is correct
behaviour for a pure function and completely wrong as a user experience: the
book silently becomes a 29-stock portfolio. So the failures come back
separately and the caller decides, with the number in front of them.

Pure but for the injected feed: no repository, no network of its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Callable, Iterable, Protocol, Sequence


class Mapping(Protocol):
    """The part of `SymbolMapping` this module needs."""

    nse_symbol: str
    feed_ticker: str


@dataclass
class ExecutionPrices:
    """What could be priced for the execution session, and what could not."""

    on: date
    prices: dict[str, Decimal] = field(default_factory=dict)
    #: Symbols with no `symbol_map` entry at all.
    unmapped: list[str] = field(default_factory=list)
    #: Mapped, but the feed carried no open for this session. symbol -> why.
    unpriced: dict[str, str] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return not self.unmapped and not self.unpriced

    @property
    def failures(self) -> list[str]:
        """Every symbol that cannot be sized, in one list."""
        return sorted(set(self.unmapped) | set(self.unpriced))

    def summary(self) -> str:
        priced = len(self.prices)
        total = priced + len(self.failures)
        return f"{priced}/{total} priced for {self.on.isoformat()}"


def resolve_opens(
    symbols: Iterable[str],
    *,
    on: date,
    resolve: Callable[[str], Mapping | None],
    fetch: Callable[[Sequence[str], date], "QuotesLike"],
) -> ExecutionPrices:
    """Opening prices for `symbols` on `on`, keyed by **NSE symbol**.

    `fetch` takes feed tickers and returns something with `.opens` and
    `.missing` keyed by ticker -- `io.feed.opens`, in production. The result is
    translated back to NSE symbols here, because everything downstream
    (`construction.build`, the book, the recommendation) speaks NSE symbols and
    a ticker leaking into that vocabulary would be a silent mismatch.
    """
    out = ExecutionPrices(on=on)
    tickers: dict[str, str] = {}      # ticker -> nse symbol

    for symbol in symbols:
        mapping = resolve(symbol)
        if mapping is None or not mapping.feed_ticker:
            out.unmapped.append(symbol)
            continue
        tickers[mapping.feed_ticker] = symbol

    if not tickers:
        out.unmapped.sort()
        return out

    quotes = fetch(sorted(tickers), on)
    for ticker, price in quotes.opens.items():
        symbol = tickers.get(ticker)
        if symbol is not None:
            out.prices[symbol] = price
    for ticker, reason in quotes.missing.items():
        symbol = tickers.get(ticker)
        if symbol is not None:
            out.unpriced[symbol] = reason

    out.unmapped.sort()
    return out


class QuotesLike(Protocol):
    opens: dict[str, Decimal]
    missing: dict[str, str]
