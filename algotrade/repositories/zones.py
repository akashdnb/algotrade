from __future__ import annotations
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from algotrade.repositories.base import Repository
from algotrade.repositories import codec
from algotrade.zones.config import Timeframe, ZoneStatus, ZoneType
from algotrade.zones.models import Zone

class ZoneRepository(Repository):
    collection_name = "zones"

    @staticmethod
    def key(zone: Zone) -> str:
        return zone.zone_id

    @staticmethod
    def _document(zone: Zone, config_hash: str = "", computed_at: datetime | None = None):
        return {
            "_id": zone.zone_id, "symbol": zone.symbol, "timeframe": zone.timeframe.value,
            "zone_type": zone.zone_type.value, "zone_low": codec.to_mongo_money(zone.zone_low),
            "zone_high": codec.to_mongo_money(zone.zone_high), "base_start_date": zone.base_start_date.isoformat(),
            "base_end_date": zone.base_end_date.isoformat(), "displacement_date": zone.displacement_date.isoformat(),
            "atr_at_creation": codec.to_mongo_money(zone.atr_at_creation), "displacement_atr": zone.displacement_atr,
            "volume_ratio": zone.volume_ratio, "strength_score": zone.strength_score, "status": zone.status.value,
            "touch_count": zone.touch_count, "first_tested_at": zone.first_tested_at.isoformat() if zone.first_tested_at else None,
            "last_tested_at": zone.last_tested_at.isoformat() if zone.last_tested_at else None,
            "created_at": zone.created_at.isoformat(), "invalidated_at": zone.invalidated_at.isoformat() if zone.invalidated_at else None,
            "config_hash": config_hash, "computed_at": codec.to_mongo_instant(computed_at),
        }

    @staticmethod
    def _zone(doc):
        return Zone(doc["symbol"], Timeframe(doc["timeframe"]), ZoneType(doc["zone_type"]),
            codec.from_mongo_money(doc["zone_low"]), codec.from_mongo_money(doc["zone_high"]),
            date.fromisoformat(doc["base_start_date"]), date.fromisoformat(doc["base_end_date"]), date.fromisoformat(doc["displacement_date"]),
            codec.from_mongo_money(doc["atr_at_creation"]), float(doc["displacement_atr"]), doc.get("volume_ratio"), int(doc["strength_score"]),
            ZoneStatus(doc["status"]), int(doc.get("touch_count",0)), date.fromisoformat(doc["first_tested_at"]) if doc.get("first_tested_at") else None,
            date.fromisoformat(doc["last_tested_at"]) if doc.get("last_tested_at") else None, date.fromisoformat(doc["created_at"]),
            date.fromisoformat(doc["invalidated_at"]) if doc.get("invalidated_at") else None)

    def replace_for_symbol_timeframe(self, symbol, timeframe, zones, *, config_hash="", computed_at=None):
        current_ids=[]
        for zone in zones:
            current_ids.append(zone.zone_id)
            self.collection.replace_one({"_id": zone.zone_id}, self._document(zone, config_hash, computed_at), upsert=True)
        self.collection.delete_many({"symbol":symbol,"timeframe":timeframe.value,"_id":{"$nin":current_ids}})

    def for_symbol(self, symbol, timeframe=None):
        query={"symbol":symbol}
        if timeframe: query["timeframe"]=timeframe.value
        return [self._zone(d) for d in self.collection.find(query).sort("created_at",1)]

    def active_for_symbol(self, symbol, timeframe=None):
        query={"symbol":symbol,"status":{"$in":["active","tested"]}}
        if timeframe: query["timeframe"]=timeframe.value
        return [self._zone(d) for d in self.collection.find(query)]

    def nearest_demand_below(self, symbol, timeframe, price):
        docs=self.collection.find({"symbol":symbol,"timeframe":timeframe.value,"zone_type":"demand","status":{"$in":["active","tested"]},"zone_high":{"$lt":price}}).sort("zone_high",-1).limit(1)
        doc=next(iter(docs),None); return self._zone(doc) if doc else None

    def nearest_supply_above(self, symbol, timeframe, price):
        docs=self.collection.find({"symbol":symbol,"timeframe":timeframe.value,"zone_type":"supply","status":{"$in":["active","tested"]},"zone_low":{"$gt":price}}).sort("zone_low",1).limit(1)
        doc=next(iter(docs),None); return self._zone(doc) if doc else None

    def all_active(self, *, since=None):
        query={"status":{"$in":["active","tested"]}}
        if since: query["created_at"]={"$gte":since.isoformat()}
        return [self._zone(d) for d in self.collection.find(query)]
