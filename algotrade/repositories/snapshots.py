"""The `portfolio_snapshots` collection -- daily valuation and the basis for
reconstructing holdings on any past date (§9.1)."""

from __future__ import annotations

from datetime import date
from typing import Any

from algotrade.domain import money
from algotrade.domain.models import Position, Snapshot
from algotrade.repositories import codec, schema
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

    def save_valuation(self, snapshot: Snapshot, valuation) -> bool:  # noqa: ANN001
        """Persist a snapshot plus the Phase 4 P&L breakdown.

        Separate from `save` so the base snapshot shape stays usable on its
        own; both are upserts keyed by (portfolio, date).
        """
        created = self.save(snapshot)
        self.update(self.key(snapshot.portfolio_id, snapshot.date), {
            "market_value": valuation.market_value,
            "invested_capital": valuation.invested_capital,
            "realised_pnl": valuation.realised_pnl,
            "unrealised_pnl": valuation.unrealised_pnl,
            "total_pnl": valuation.total_pnl,
            "total_return": valuation.total_return,
            "day_return": valuation.day_return,
            "price_date": codec.to_mongo_date(valuation.price_date),
        })
        return created

    def as_of(self, portfolio_id: str, day: date) -> Snapshot | None:
        doc = self.collection.find_one({"_id": self.key(portfolio_id, day)})
        return self._to_entity(doc) if doc else None

    def latest(self, portfolio_id: str) -> Snapshot | None:
        doc = self.collection.find_one(
            {"portfolio_id": portfolio_id}, sort=[("date", -1)]
        )
        return self._to_entity(doc) if doc else None

    def previous(self, portfolio_id: str, before: date) -> Snapshot | None:
        """The most recent snapshot strictly before `before`.

        Not `latest()`. Valuing 2026-08-24 during a `--backfill` needs the
        session before *it*, not the newest row in the collection -- and using
        `latest()` meant every backfilled date compared itself against a
        snapshot dated later, failed the `previous.date < as_of` guard, and
        stored a null day return. The dashboard's Day column was empty on every
        row (found 2026-09-05).
        """
        doc = self.collection.find_one(
            {"portfolio_id": portfolio_id,
             "date": {"$lt": codec.to_mongo_date(before)}},
            sort=[("date", -1)],
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
        # units and unit_nav are NOT money and must not be quantized to paise.
        # Hand-decoding them with from_mongo_money silently undid
        # PRECISION_FIELDS and made unit_nav x units disagree with nav -- caught
        # by the snapshot reconciliation check, not by any unit test.
        precise = schema.PRECISION_FIELDS.get(self.collection_name, {})

        def p(field: str):
            return codec.from_mongo_decimal(
                doc.get(field), places=precise.get(field, 2))
        return Snapshot(
            portfolio_id=doc["portfolio_id"],
            date=codec.from_mongo_date(doc["date"]),
            nav=m(doc["nav"]),
            cash=m(doc["cash"]),
            invested_value=m(doc.get("invested_value")) or money.ZERO,
            units=p("units"),
            unit_nav=p("unit_nav"),
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
            peak_unit_nav=p("peak_unit_nav") or money.ZERO,
            drawdown=doc.get("drawdown", 0.0),
            benchmark_close=m(doc.get("benchmark_close")),
            benchmark_sleeve_value=m(doc.get("benchmark_sleeve_value")),
            panel_manifest_hash=doc.get("panel_manifest_hash", ""),
            computed_at=codec.from_mongo_instant(doc.get("computed_at")),
        )
