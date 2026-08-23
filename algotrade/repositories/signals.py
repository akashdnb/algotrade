"""The `signals` collection -- the daily ranking.

One document per (strategy_version, session), shared by every portfolio on that
version. This is what the daily Telegram's "Top 30" reads, and what lets you
answer "when did PERSISTENT enter the top 30?" without recomputing a year of
rankings against data that may since have been backfilled.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from algotrade.domain.models import SignalRow, SignalSet
from algotrade.repositories import codec
from algotrade.repositories.base import Repository


class SignalRepository(Repository):
    collection_name = "signals"

    @staticmethod
    def key(strategy_version: str, day: date) -> str:
        return f"{strategy_version}:{day.isoformat()}"

    def save(self, signals: SignalSet) -> bool:
        """Write, replacing any existing ranking for the session.

        Upsert rather than insert: a ranking is a pure function of (panel,
        params, date), so recomputing it must be safe -- that is what makes
        `recover.yml` idempotent over a date range.
        """
        return self.upsert({
            "_id": signals.signal_id,
            "strategy_version": signals.strategy_version,
            "date": codec.to_mongo_date(signals.date),
            "universe_size": signals.universe_size,
            "panel_manifest_hash": signals.panel_manifest_hash,
            "computed_at": codec.to_mongo_instant(signals.computed_at),
            "rows": [
                {
                    "rank": r.rank,
                    "symbol": r.symbol,
                    "isin": r.isin,
                    "feature": r.feature,
                    "adtv": codec.to_mongo_money(r.adtv),
                }
                for r in sorted(signals.rows, key=lambda r: r.rank)
            ],
        })

    def as_of(self, strategy_version: str, day: date) -> SignalSet | None:
        doc = self.collection.find_one({"_id": self.key(strategy_version, day)})
        return self._to_entity(doc) if doc else None

    def latest(self, strategy_version: str) -> SignalSet | None:
        doc = self.collection.find_one(
            {"strategy_version": strategy_version}, sort=[("date", -1)]
        )
        return self._to_entity(doc) if doc else None

    def rank_history(
        self, strategy_version: str, symbol: str, *, start: date, end: date
    ) -> list[tuple[date, int]]:
        """One symbol's rank over time -- how a name approached or left the book."""
        docs = self.collection.find(
            {
                "strategy_version": strategy_version,
                "date": {"$gte": codec.to_mongo_date(start),
                         "$lte": codec.to_mongo_date(end)},
                "rows.symbol": symbol,
            },
            {"date": 1, "rows": {"$elemMatch": {"symbol": symbol}}},
        ).sort("date", 1)
        return [
            (codec.from_mongo_date(d["date"]), d["rows"][0]["rank"])
            for d in docs
            if d.get("rows")
        ]

    def dates(self, strategy_version: str) -> list[date]:
        docs = self.collection.find(
            {"strategy_version": strategy_version}, {"date": 1}
        )
        return sorted(codec.from_mongo_date(d["date"]) for d in docs)

    def _to_entity(self, doc: dict[str, Any]) -> SignalSet:
        return SignalSet(
            strategy_version=doc["strategy_version"],
            date=codec.from_mongo_date(doc["date"]),
            universe_size=doc.get("universe_size", 0),
            panel_manifest_hash=doc.get("panel_manifest_hash", ""),
            computed_at=codec.from_mongo_instant(doc.get("computed_at")),
            rows=[
                SignalRow(
                    rank=r["rank"],
                    symbol=r["symbol"],
                    isin=r.get("isin", ""),
                    feature=r["feature"],
                    adtv=codec.from_mongo_money(r.get("adtv")),
                )
                for r in doc.get("rows", [])
            ],
        )
