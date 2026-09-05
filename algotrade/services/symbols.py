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
NSE_EQUITY_SEGMENT: str = "NSE"
EQUITY_TYPE: str = "EQ"

#: Series we will hold. BE is the same equity under restricted settlement.
TRADEABLE_SERIES: frozenset[str] = frozenset({"EQ", "BE"})


def feed_ticker(nse_symbol: str) -> str:
    """The delayed public feed's name for an NSE symbol.

    A single place, so the `.NS` convention is stated once rather than
    scattered through call sites where one of them will eventually differ.
    """
    return f"{nse_symbol}.NS" if nse_symbol else ""


def _series_of(tradingsymbol: str) -> str:
    """`HFCL-BE` -> `BE`; a bare symbol is EQ.

    Kite encodes the series as a suffix on the tradingsymbol rather than as a
    field, which is why this is parsed rather than read.
    """
    _, sep, suffix = tradingsymbol.partition("-")
    return suffix if sep and suffix in TRADEABLE_SERIES else EQUITY_TYPE


def from_instruments(rows: Iterable[Mapping]) -> dict[str, SymbolMapping]:
    """Kite's instruments dump -> mappings, keyed by NSE symbol.

    Filters to NSE cash equity. Everything else in that file is an instrument
    this system has no way to trade, and carrying it would make the map's size
    meaningless as a sanity check.
    """
    out: dict[str, SymbolMapping] = {}
    for row in rows:
        if row.get("exchange") != NSE_EQUITY_SEGMENT:
            continue
        if row.get("instrument_type") != EQUITY_TYPE:
            continue
        tradingsymbol = (row.get("tradingsymbol") or "").strip()
        if not tradingsymbol:
            continue

        series = _series_of(tradingsymbol)
        symbol = tradingsymbol.split("-", 1)[0] if series != EQUITY_TYPE \
            else tradingsymbol
        token = row.get("instrument_token")
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
