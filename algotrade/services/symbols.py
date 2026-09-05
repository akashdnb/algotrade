"""Assembling symbol identity from the four sources that each know part of it.

No one source has the whole answer:

| Source | symbol | tradingsymbol + token | ISIN |
|---|---|---|---|
| Kite `/instruments` | yes | **yes** | no |
| Kite `/portfolio/holdings` | yes | yes | **yes**, held names only |
| `io.actions.isin_map` | yes | no | **yes**, names with corporate actions |
| the price feed | derived (`SYMBOL.NS`) | no | no |

So the map is a merge, and the merge order is a **precedence decision**, not a
detail: the broker's own holdings outrank the corporate-actions feed, because
the broker is the party we will reconcile against and a disagreement between
them means the feed is stale.

Pure functions over already-fetched rows. Fetching is the job's problem.
"""

from __future__ import annotations

from typing import Iterable, Mapping, Sequence

from algotrade.brokers.base import BrokerHolding
from algotrade.repositories.symbol_map import SymbolMapping

#: NSE equity, cash segment. Kite's dump also carries derivatives, currency and
#: commodities, none of which this system can trade.
NSE_EXCHANGE: str = "NSE"
EQUITY_TYPE: str = "EQ"

#: The cash segment. Kite marks index rows `INDICES` on the same exchange with
#: the same `instrument_type`, so filtering on type alone puts NIFTY 50 in the
#: map as though it were a stock you could buy.
CASH_SEGMENT: str = "NSE"

#: Series we will hold. BE is the same equity under restricted settlement.
#:
#: Everything else in the cash segment is a different instrument wearing the
#: same `instrument_type`: `SG`/`GS`/`TB`/`GB` government paper, `N0`-`N9` and
#: `NA`-`NE` debentures, `SM` SME, `BZ` trade-to-trade, `IV`/`SF`/`ST` others.
#: On 2026-09-05 the dump held 10,126 cash-segment rows of which **2,994 were
#: plain equities and 242 were BE** -- so accepting the rest would have made
#: the map three times the size of the universe it describes, and its size
#: useless as a sanity check.
TRADEABLE_SERIES: frozenset[str] = frozenset({"EQ", "BE"})


def feed_ticker(nse_symbol: str) -> str:
    """The delayed public feed's name for an NSE symbol.

    A single place, so the `.NS` convention is stated once rather than
    scattered through call sites where one of them will eventually differ.
    """
    return f"{nse_symbol}.NS" if nse_symbol else ""


def split_series(tradingsymbol: str) -> tuple[str, str]:
    """`HFCL-BE` -> `("HFCL", "BE")`; `HFCL` -> `("HFCL", "EQ")`.

    Kite encodes the series as a suffix rather than a field, so it is parsed
    rather than read.

    **A suffix counts as a series code only when it is exactly two characters.**
    That rule exists because real symbols contain hyphens: `M-M` is Mahindra &
    Mahindra and `MRO-TEK` is a company, not an `M` or a `TEK` series. Treating
    every suffix as a series would silently rename both — and a renamed symbol
    in an identity table is the precise failure this map exists to prevent.
    """
    head, sep, suffix = tradingsymbol.rpartition("-")
    if sep and len(suffix) == 2 and suffix.isalnum() and suffix.isupper():
        return head, suffix
    return tradingsymbol, EQUITY_TYPE


def from_instruments(rows: Iterable[Mapping]) -> dict[str, SymbolMapping]:
    """Kite's instruments dump -> mappings, keyed by NSE symbol.

    Filters to NSE cash equity. Everything else in that file is an instrument
    this system has no way to trade, and carrying it would make the map's size
    meaningless as a sanity check.
    """
    out: dict[str, SymbolMapping] = {}
    for row in rows:
        if row.get("exchange") != NSE_EXCHANGE:
            continue
        if row.get("instrument_type") != EQUITY_TYPE:
            continue
        # Index rows share the exchange AND the instrument_type; only the
        # segment separates them. Without this, NIFTY 50 enters the map as a
        # stock, and nothing downstream would question a symbol that resolves.
        if row.get("segment") != CASH_SEGMENT:
            continue
        tradingsymbol = (row.get("tradingsymbol") or "").strip()
        if not tradingsymbol:
            continue

        symbol, series = split_series(tradingsymbol)
        if series not in TRADEABLE_SERIES:
            continue

        token = row.get("instrument_token")
        # EQ wins over BE when a scrip appears as both: the plain series is
        # what the panel and the book use, and the BE row would otherwise
        # overwrite it depending on file order.
        if series == "BE" and symbol in out:
            continue
        out[symbol] = SymbolMapping(
            nse_symbol=symbol,
            feed_ticker=feed_ticker(symbol),
            kite_tradingsymbol=tradingsymbol,
            kite_instrument_token=int(token) if token is not None else None,
            series=series,
        )
    return out


def enrich(
    base: Mapping[str, SymbolMapping],
    *,
    isins: Mapping[str, str] | None = None,
    demat: Sequence[BrokerHolding] = (),
) -> list[SymbolMapping]:
    """Add ISINs to `base`, broker first.

    A symbol present only in `isins` or `demat` and absent from the instruments
    dump is **skipped**, not invented: without a tradingsymbol and token the
    row cannot address anything at Kite, and a half-row in an identity table is
    worse than a missing one -- it looks like an answer.
    """
    merged = {symbol: mapping for symbol, mapping in base.items()}

    for symbol, isin in (isins or {}).items():
        existing = merged.get(symbol)
        if existing is not None and isin and not existing.isin:
            merged[symbol] = _with_isin(existing, isin)

    # Last, so the broker wins any disagreement: it is the party we reconcile
    # against, and if the corporate-actions feed says otherwise the feed is
    # what is stale.
    for held in demat:
        existing = merged.get(held.symbol)
        if existing is not None and held.isin:
            merged[held.symbol] = _with_isin(existing, held.isin)

    return [merged[symbol] for symbol in sorted(merged)]


def _with_isin(mapping: SymbolMapping, isin: str) -> SymbolMapping:
    return SymbolMapping(
        nse_symbol=mapping.nse_symbol,
        isin=isin,
        feed_ticker=mapping.feed_ticker,
        kite_tradingsymbol=mapping.kite_tradingsymbol,
        kite_instrument_token=mapping.kite_instrument_token,
        series=mapping.series,
    )


def coverage(mappings: Sequence[SymbolMapping]) -> tuple[int, int]:
    """`(identified, total)`.

    Reported rather than asserted. ISIN coverage will be partial for a long
    time, and a job that failed on that would simply never run -- but a number
    that silently drops is worth seeing.
    """
    return sum(1 for m in mappings if m.identified), len(mappings)
