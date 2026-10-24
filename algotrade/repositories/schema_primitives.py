"""Shared building blocks for collection schema definitions.

Split out of `schema.py` so a per-feature schema module (for example
`zones_schema.py`) can depend on `Collection`/`Index` without creating an
import cycle: `schema.py` imports each feature's collection to include it in
`ALL`, so the feature module cannot import these primitives back from
`schema.py` itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

import pymongo

_MONEY: Final[dict] = {"bsonType": "decimal"}
_DATE_STR: Final[dict] = {"bsonType": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"}

ASC: Final[int] = pymongo.ASCENDING
DESC: Final[int] = pymongo.DESCENDING


@dataclass(frozen=True, slots=True)
class Index:
    keys: list[tuple[str, int]]
    name: str
    unique: bool = False
    #: Restricts BOTH what the index covers and what uniqueness applies to.
    partial: dict[str, Any] | None = None
    expire_after_seconds: int | None = None

    def kwargs(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name}
        if self.unique:
            out["unique"] = True
        if self.partial is not None:
            out["partialFilterExpression"] = self.partial
        if self.expire_after_seconds is not None:
            out["expireAfterSeconds"] = self.expire_after_seconds
        return out


@dataclass(frozen=True, slots=True)
class Collection:
    name: str
    validator: dict[str, Any]
    indexes: list[Index] = field(default_factory=list)
    #: Documented so retention is reviewable in one place (§2).
    retention: str = "permanent"
