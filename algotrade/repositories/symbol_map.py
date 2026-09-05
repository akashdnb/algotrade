"""The `symbol_map` collection -- one identity across three namespaces.

bhavcopy calls it `HFCL`. The price feed calls it `HFCL.NS`. Kite calls it
`HFCL` with an instrument token, until the scrip moves to the BE series and the
tradingsymbol changes. The ISIN does not change, so the ISIN is the key.

**Resolution fails closed.** `resolve` returns None for an unknown symbol and
the caller must refuse to trade it. The alternative -- falling back to a string
match, or appending `.NS` and hoping -- returns *a* price for *an* instrument,
silently, and sizes a real order on it. This system has already paid for one
symbol-identity mistake.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable

from algotrade.repositories import codec
from algotrade.repositories.base import Repository


@dataclass(frozen=True, slots=True)
class SymbolMapping:
    isin: str
    nse_symbol: str
    feed_ticker: str = ""
    kite_tradingsymbol: str = ""
    kite_instrument_token: int | None = None
    series: str = ""

    @classmethod
    def from_doc(cls, doc: dict) -> "SymbolMapping":
        return cls(
            isin=doc["_id"],
            nse_symbol=doc["nse_symbol"],
            feed_ticker=doc.get("feed_ticker", ""),
            kite_tradingsymbol=doc.get("kite_tradingsymbol", ""),
            kite_instrument_token=doc.get("kite_instrument_token"),
            series=doc.get("series", ""),
        )


@dataclass(frozen=True, slots=True)
class MappingChange:
    """One field of one symbol's identity moved. Always a finding, never a
    silent update -- a changed mapping is either a corporate action worth
    knowing about or a bug worth catching."""

    isin: str
    field: str
    was: object
    now: object

    def __str__(self) -> str:
        return f"{self.isin}: {self.field} {self.was!r} -> {self.now!r}"


#: Fields whose change is worth reporting. `updated_on` moves every refresh.
TRACKED: tuple[str, ...] = ("nse_symbol", "feed_ticker", "kite_tradingsymbol",
                            "kite_instrument_token", "series")


def diff(old: SymbolMapping, new: SymbolMapping) -> list[MappingChange]:
    """What changed between two versions of one symbol's identity."""
    return [
        MappingChange(isin=new.isin, field=name,
                      was=getattr(old, name), now=getattr(new, name))
        for name in TRACKED
        if getattr(old, name) != getattr(new, name)
    ]


class SymbolMapRepository(Repository):
    collection_name = "symbol_map"

    def resolve(self, nse_symbol: str) -> SymbolMapping | None:
        """Identity for an NSE symbol, or None. **Callers must fail closed.**"""
        docs = self.find({"nse_symbol": nse_symbol}, limit=2)
        if len(docs) != 1:
            # Zero is unknown; two would mean a symbol claimed by two ISINs,
            # which is ambiguous and must be resolved rather than guessed.
            return None
        return SymbolMapping.from_doc(docs[0])

    def by_isin(self, isin: str) -> SymbolMapping | None:
        doc = self.get(isin)
        return SymbolMapping.from_doc(doc) if doc else None

    def put(self, mapping: SymbolMapping, *, on: date) -> None:
        self.upsert({
            "_id": mapping.isin,
            "nse_symbol": mapping.nse_symbol,
            "feed_ticker": mapping.feed_ticker,
            "kite_tradingsymbol": mapping.kite_tradingsymbol,
            "kite_instrument_token": mapping.kite_instrument_token,
            "series": mapping.series,
            "updated_on": codec.to_mongo_date(on),
        })

    def all_mappings(self) -> list[SymbolMapping]:
        return [SymbolMapping.from_doc(d)
                for d in self.find(sort=[("nse_symbol", 1)])]

    def refresh(
        self,
        mappings: Iterable[SymbolMapping],
        *,
        on: date,
    ) -> list[MappingChange]:
        """Store `mappings`, returning every identity change observed.

        The changes are the point. A refresh that silently overwrote would turn
        a corporate action into invisible data movement; returning them lets
        the caller report and a human confirm.
        """
        changes: list[MappingChange] = []
        for mapping in mappings:
            existing = self.by_isin(mapping.isin)
            if existing is not None:
                changes.extend(diff(existing, mapping))
            self.put(mapping, on=on)
        return changes
