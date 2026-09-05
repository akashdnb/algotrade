"""The `symbol_map` collection -- one identity across three namespaces.

bhavcopy calls it `HFCL`. The price feed calls it `HFCL.NS`. Kite calls it
`HFCL` with an instrument token, until the scrip moves to the BE series and the
tradingsymbol changes.

**Keyed by NSE symbol, with ISIN as an optional field.** ISIN is the better
identity and the design asked for it as the key -- but the current session
format (`sec_bhavdata_full_*`) carries no ISIN, and neither does Kite's
instruments dump. It can be recovered from the pre-2021 `cm*bhav` archive, from
corporate actions via `io.actions.isin_map`, and from Kite's holdings for names
actually held. None of those covers the universe. A key that cannot always be
computed is not a key, so the symbol is the key and the ISIN is carried
wherever it is found; `services/demat.py` prefers it and falls back to symbol.

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

from pymongo import ReplaceOne

from algotrade.repositories import codec
from algotrade.repositories.base import Repository


@dataclass(frozen=True, slots=True)
class SymbolMapping:
    nse_symbol: str
    isin: str = ""
    feed_ticker: str = ""
    kite_tradingsymbol: str = ""
    kite_instrument_token: int | None = None
    series: str = ""

    @classmethod
    def from_doc(cls, doc: dict) -> "SymbolMapping":
        return cls(
            nse_symbol=doc.get("nse_symbol") or doc["_id"],
            isin=doc.get("isin", ""),
            feed_ticker=doc.get("feed_ticker", ""),
            kite_tradingsymbol=doc.get("kite_tradingsymbol", ""),
            kite_instrument_token=doc.get("kite_instrument_token"),
            series=doc.get("series", ""),
        )

    @property
    def identified(self) -> bool:
        """Whether this row can take part in ISIN matching at all."""
        return bool(self.isin)


@dataclass(frozen=True, slots=True)
class MappingChange:
    """One field of one symbol's identity moved. Always a finding, never a
    silent update -- a changed mapping is either a corporate action worth
    knowing about or a bug worth catching."""

    symbol: str
    field: str
    was: object
    now: object

    def __str__(self) -> str:
        return f"{self.symbol}: {self.field} {self.was!r} -> {self.now!r}"


#: Fields whose change is worth reporting. `updated_on` moves every refresh.
TRACKED: tuple[str, ...] = ("isin", "feed_ticker", "kite_tradingsymbol",
                            "kite_instrument_token", "series")


def diff(old: SymbolMapping, new: SymbolMapping) -> list[MappingChange]:
    """What changed between two versions of one symbol's identity.

    Learning an ISIN for the first time counts, and deliberately so: the row
    becomes eligible for ISIN matching, which changes how every later
    comparison treats it.
    """
    return [
        MappingChange(symbol=new.nse_symbol, field=name,
                      was=getattr(old, name), now=getattr(new, name))
        for name in TRACKED
        if getattr(old, name) != getattr(new, name)
    ]


class SymbolMapRepository(Repository):
    collection_name = "symbol_map"

    def resolve(self, nse_symbol: str) -> SymbolMapping | None:
        """Identity for an NSE symbol, or None. **Callers must fail closed.**"""
        doc = self.get(nse_symbol)
        return SymbolMapping.from_doc(doc) if doc else None

    def by_isin(self, isin: str) -> SymbolMapping | None:
        """Reverse lookup. None for an empty ISIN, never the first row that
        happens to lack one."""
        if not isin:
            return None
        docs = self.find({"isin": isin}, limit=2)
        if len(docs) != 1:
            # Two would mean a rename we have not noticed: one ISIN now
            # answering to two symbols. Ambiguous, so resolve it by hand.
            return None
        return SymbolMapping.from_doc(docs[0])

    @staticmethod
    def _doc(mapping: SymbolMapping, *, on: date) -> dict:
        return {
            "_id": mapping.nse_symbol,
            "nse_symbol": mapping.nse_symbol,
            "isin": mapping.isin,
            "feed_ticker": mapping.feed_ticker,
            "kite_tradingsymbol": mapping.kite_tradingsymbol,
            "kite_instrument_token": mapping.kite_instrument_token,
            "series": mapping.series,
            "updated_on": codec.to_mongo_date(on),
        }

    def put(self, mapping: SymbolMapping, *, on: date) -> None:
        self.upsert(self._doc(mapping, on=on))

    def all_mappings(self) -> list[SymbolMapping]:
        return [SymbolMapping.from_doc(d)
                for d in self.find(sort=[("nse_symbol", 1)])]

    def compare(
        self,
        mappings: Iterable[SymbolMapping],
    ) -> list[MappingChange]:
        """Every identity change `mappings` would make, without writing.

        **One read for the whole map, not one per symbol.** The NSE cash
        universe is a few thousand names; a per-symbol `resolve` is that many
        round trips to Atlas, which turned a two-second job into one that had
        to be killed.
        """
        existing = {m.nse_symbol: m for m in self.all_mappings()}
        changes: list[MappingChange] = []
        for mapping in mappings:
            was = existing.get(mapping.nse_symbol)
            if was is not None:
                changes.extend(diff(was, mapping))
        return changes

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

        One read and one bulk write, for the reason `compare` gives.
        """
        mappings = list(mappings)
        changes = self.compare(mappings)
        self.put_many(mappings, on=on)
        return changes

    def put_many(self, mappings: Iterable[SymbolMapping], *, on: date) -> int:
        """Upsert in one round trip. Returns how many were sent."""
        operations = [
            ReplaceOne({"_id": mapping.nse_symbol},
                       self._encode(self._doc(mapping, on=on)), upsert=True)
            for mapping in mappings
        ]
        if not operations:
            return 0
        self.collection.bulk_write(operations, ordered=False)
        return len(operations)
