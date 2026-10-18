"""Standalone multi-timeframe demand/supply zone engine."""

# The zone collection is registered directly in `repositories/schema.py`'s
# `ALL`, so init_db/check_config/backup see it without importing this package.
from algotrade.repositories.zones_schema import ZONES

__all__ = ["ZONES"]
