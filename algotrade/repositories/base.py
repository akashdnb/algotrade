"""Shared repository behaviour.

Every repository is a thin, typed wrapper over one collection. They exist so
that services never build a query -- and so the money codec is applied on every
read and write without each call site remembering to.
"""

from __future__ import annotations

from typing import Any, Iterator, Mapping

from pymongo.collection import Collection as MongoCollection
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from algotrade.repositories import codec, schema


class AlreadyExists(RuntimeError):
    """A document with this `_id` is already stored.

    Surfaced as a named error rather than a raw `DuplicateKeyError` because for
    most callers it is not a failure at all -- it is the idempotency guarantee
    working, and the right response is usually to read the existing document
    (design §11.5).
    """


class Repository:
    """One collection, with money encoding applied at the boundary."""

    collection_name: str = ""

    def __init__(self, db: Database) -> None:
        if not self.collection_name:
            raise NotImplementedError("subclass must set collection_name")
        self._db = db
        self._money = schema.MONEY_FIELDS.get(self.collection_name, ())
        self._nested_money = schema.NESTED_MONEY_FIELDS.get(self.collection_name, {})
        self._precise = schema.PRECISION_FIELDS.get(self.collection_name, {})
        self._nested_precise = schema.NESTED_PRECISION_FIELDS.get(
            self.collection_name, {}
        )

    @property
    def collection(self) -> MongoCollection:
        return self._db[self.collection_name]

    # ------------------------------------------------------------- encode ---

    def _encode(self, doc: Mapping[str, Any]) -> dict[str, Any]:
        out = codec.encode_doc(doc, money_fields=self._money)
        if self._precise:
            out = codec.encode_precise(out, self._precise)
        if self._nested_money:
            out = codec.encode_nested(out, self._nested_money)
        if self._nested_precise:
            out = codec.encode_nested_precise(out, self._nested_precise)
        return out

    def _decode(self, doc: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if doc is None:
            return None
        out = codec.decode_doc(doc, money_fields=self._money)
        if self._precise:
            out = codec.decode_precise(out, self._precise)
        if self._nested_money:
            out = codec.decode_nested(out, self._nested_money)
        if self._nested_precise:
            out = codec.decode_nested_precise(out, self._nested_precise)
        return out

    # -------------------------------------------------------------- write ---

    def insert(self, doc: Mapping[str, Any]) -> str:
        """Insert, raising `AlreadyExists` on a duplicate `_id`.

        Deliberately not an upsert. The deterministic ids in `schema` mean a
        duplicate is a *signal* -- either a retry (read the existing document)
        or a genuine collision (a bug). Silently overwriting would destroy
        whichever one was real.
        """
        try:
            result = self.collection.insert_one(self._encode(doc))
        except DuplicateKeyError as exc:
            raise AlreadyExists(
                f"{self.collection_name}: {doc.get('_id')!r} already exists"
            ) from exc
        return result.inserted_id

    def upsert(self, doc: Mapping[str, Any]) -> bool:
        """Insert or replace by `_id`. Returns True if a new document was made.

        For genuinely idempotent recomputation only -- a daily snapshot rebuilt
        from the same inputs, for example. Never for anything that records a
        decision or a trade.
        """
        encoded = self._encode(doc)
        result = self.collection.replace_one(
            {"_id": encoded["_id"]}, encoded, upsert=True
        )
        return result.upserted_id is not None

    def update(self, _id: str, changes: Mapping[str, Any]) -> bool:
        """`$set` the given fields. Returns True if a document matched."""
        result = self.collection.update_one(
            {"_id": _id}, {"$set": self._encode(changes)}
        )
        return result.matched_count > 0

    def delete(self, _id: str) -> bool:
        return self.collection.delete_one({"_id": _id}).deleted_count > 0

    # --------------------------------------------------------------- read ---

    def get(self, _id: str) -> dict[str, Any] | None:
        return self._decode(self.collection.find_one({"_id": _id}))

    def exists(self, _id: str) -> bool:
        return self.collection.count_documents({"_id": _id}, limit=1) > 0

    def find(
        self,
        filter: Mapping[str, Any] | None = None,
        *,
        sort: list[tuple[str, int]] | None = None,
        limit: int = 0,
        projection: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        cursor = self.collection.find(filter or {}, projection)
        if sort:
            cursor = cursor.sort(sort)
        if limit:
            cursor = cursor.limit(limit)
        return [self._decode(d) for d in cursor]  # type: ignore[misc]

    def iterate(
        self,
        filter: Mapping[str, Any] | None = None,
        *,
        sort: list[tuple[str, int]] | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Streaming read, for scans that should not be held in memory."""
        cursor = self.collection.find(filter or {})
        if sort:
            cursor = cursor.sort(sort)
        for doc in cursor:
            yield self._decode(doc)  # type: ignore[misc]

    def count(self, filter: Mapping[str, Any] | None = None) -> int:
        return self.collection.count_documents(filter or {})
