"""The `portfolio_snapshots` collection -- daily valuation and the basis for
reconstructing holdings on any past date (§9.1)."""

from __future__ import annotations

from datetime import date
from typing import Any

from algotrade.domain import money
from algotrade.domain.models import Position, Snapshot
from algotrade.repositories import codec
from algotrade.repositories.base import Repository


class SnapshotRepository(Repository):
    collection_name = "portfolio_snapshots"

    @staticmethod
    def key(portfolio_id: str, day: date) -> str:
        return f"{portfolio_id}:{day.isoformat()}"

    def save(self, snapshot: Snapshot) -> bool:
        """Write, replacing any existing snapshot for the same date.

        Upsert rather than insert: a snapshot is a pure function of (holdings,
        prices, contributions) for that date, so recomputing it must be safe.
        This is what makes `recover.yml` idempotent over a date range (§11.5).
        """
        return self.upsert({
            "_id": self.key(snapshot.portfolio_id, snapshot.date),
            "portfolio_id": snapshot.portfolio_id,
            "date": codec.to_mongo_date(snapshot.date),
            "nav": snapshot.nav,
            "cash": snapshot.cash,
            "invested_value": snapshot.invested_value,
            "units": snapshot.units,
            "unit_nav": snapshot.unit_nav,
            "total_contributed": snapshot.total_contributed,
            "peak_unit_nav": snapshot.peak_unit_nav,
            "drawdown": snapshot.drawdown,
            # Money left as Decimal: the base repository encodes nested money
            # from schema.NESTED_MONEY_FIELDS (one implementation, not three).
            "positions": [
                {
                    "symbol": p.symbol,
                    "quantity": p.quantity,
                    "close": p.close,
                    "value": p.value,
                    "weight": p.weight,
                    "unrealised_pnl": p.unrealised_pnl,
                }
                for p in snapshot.positions
            ],
            "benchmark_close": snapshot.benchmark_close,
            "benchmark_sleeve_value": snapshot.benchmark_sleeve_value,
            "panel_manifest_hash": snapshot.panel_manifest_hash,
            "computed_at": codec.to_mongo_instant(snapshot.computed_at),
        })

    def _encode(self, doc):  # noqa: D102 -- benchmark_close is money too
        doc = dict(doc)
        for extra in ("benchmark_close", "benchmark_sleeve_value"):
            if extra in doc:
                doc[extra] = codec.to_mongo_money(doc[extra])
        return super()._encode(doc)

    def as_of(self, portfolio_id: str, day: date) -> Snapshot | None:
        doc = self.collection.find_one({"_id": self.key(portfolio_id, day)})
        return self._to_entity(doc) if doc else None

    def latest(self, portfolio_id: str) -> Snapshot | None:
        doc = self.collection.find_one(
            {"portfolio_id": portfolio_id}, sort=[("date", -1)]
        )
        return self._to_entity(doc) if doc else None

    def series(
        self, portfolio_id: str, *, start: date | None = None,
        end: date | None = None, fields: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Time series for charts. `fields` projects away the positions array,
        which is 90% of a snapshot's size and never needed for a NAV chart."""
        query: dict[str, Any] = {"portfolio_id": portfolio_id}
        if start or end:
            bounds: dict[str, str] = {}
            if start:
                bounds["$gte"] = codec.to_mongo_date(start)
            if end:
                bounds["$lte"] = codec.to_mongo_date(end)
            query["date"] = bounds

        projection = None
        if fields:
            projection = {f: 1 for f in fields}
            projection["date"] = 1
        return self.find(query, sort=[("date", 1)], projection=projection)

    def peak_unit_nav(self, portfolio_id: str) -> Any:
        """The running maximum to date, for drawdown on the next snapshot."""
        doc = self.collection.find_one(
            {"portfolio_id": portfolio_id},
            sort=[("peak_unit_nav", -1)],
            projection={"peak_unit_nav": 1},
        )
        return codec.from_mongo_money(doc["peak_unit_nav"]) if doc else money.ZERO

    def dates(self, portfolio_id: str) -> list[date]:
        docs = self.collection.find({"portfolio_id": portfolio_id}, {"date": 1})
        return sorted(codec.from_mongo_date(d["date"]) for d in docs)

    def _to_entity(self, doc: dict[str, Any]) -> Snapshot:
        m = codec.from_mongo_money
        return Snapshot(
            portfolio_id=doc["portfolio_id"],
            date=codec.from_mongo_date(doc["date"]),
            nav=m(doc["nav"]),
            cash=m(doc["cash"]),
            invested_value=m(doc.get("invested_value")) or money.ZERO,
            units=m(doc["units"]),
            unit_nav=m(doc["unit_nav"]),
            total_contributed=m(doc.get("total_contributed")) or money.ZERO,
            positions=[
                Position(
                    symbol=p["symbol"], quantity=p["quantity"],
                    close=m(p["close"]), value=m(p["value"]),
                    weight=p.get("weight", 0.0),
                    unrealised_pnl=m(p.get("unrealised_pnl")) or money.ZERO,
                )
                for p in doc.get("positions", [])
            ],
            peak_unit_nav=m(doc.get("peak_unit_nav")) or money.ZERO,
            drawdown=doc.get("drawdown", 0.0),
            benchmark_close=m(doc.get("benchmark_close")),
            benchmark_sleeve_value=m(doc.get("benchmark_sleeve_value")),
            panel_manifest_hash=doc.get("panel_manifest_hash", ""),
            computed_at=codec.from_mongo_instant(doc.get("computed_at")),
        )
