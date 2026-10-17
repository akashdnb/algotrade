from __future__ import annotations

from typing import Final

from .schema import ASC, DESC, Collection, Index, _DATE_STR, _MONEY

ZONES: Final[Collection] = Collection(
    name="zones",
    retention="permanent -- deterministic historical zone records",
    validator={
        "$jsonSchema": {
            "bsonType": "object",
            "required": [
                "_id", "symbol", "timeframe", "zone_type", "zone_low", "zone_high",
                "base_start_date", "base_end_date", "displacement_date", "atr_at_creation",
                "displacement_atr", "strength_score", "status", "touch_count", "created_at",
            ],
            "properties": {
                "_id": {"bsonType": "string"},
                "symbol": {"bsonType": "string"},
                "timeframe": {"enum": ["daily", "weekly", "monthly"]},
                "zone_type": {"enum": ["demand", "supply"]},
                "zone_low": _MONEY,
                "zone_high": _MONEY,
                "base_start_date": _DATE_STR,
                "base_end_date": _DATE_STR,
                "displacement_date": _DATE_STR,
                "atr_at_creation": _MONEY,
                "displacement_atr": {"bsonType": "double"},
                "volume_ratio": {"bsonType": ["double", "null"]},
                "strength_score": {"bsonType": "int", "minimum": 0, "maximum": 100},
                "status": {"enum": ["active", "tested", "broken"]},
                "touch_count": {"bsonType": "int", "minimum": 0},
                "first_tested_at": {"bsonType": ["string", "null"]},
                "last_tested_at": {"bsonType": ["string", "null"]},
                "created_at": _DATE_STR,
                "invalidated_at": {"bsonType": ["string", "null"]},
                "config_hash": {"bsonType": "string"},
                "computed_at": {"bsonType": "date"},
            },
        }
    },
    indexes=[
        Index([("symbol", ASC), ("timeframe", ASC), ("created_at", DESC)], name="symbol_timeframe_created"),
        Index([("symbol", ASC), ("timeframe", ASC), ("status", ASC)], name="symbol_timeframe_status"),
        Index([("symbol", ASC), ("zone_type", ASC), ("status", ASC)], name="symbol_type_status"),
        Index([("created_at", DESC)], name="created_at"),
    ],
)
