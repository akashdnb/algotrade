"""Standalone multi-timeframe demand/supply zone engine."""

# The core schema module predates zones. Register the zone collection when the
# feature package is imported so the existing init_db/apply_schema machinery
# sees the new validator and indexes without changing unrelated collections.
from algotrade.repositories import schema
from algotrade.repositories.zones_schema import ZONES

if ZONES not in schema.ALL:
    schema.ALL = (*schema.ALL, ZONES)
    schema.BY_NAME = {collection.name: collection for collection in schema.ALL}

__all__ = ["ZONES"]
