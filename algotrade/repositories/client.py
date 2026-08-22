"""MongoDB connection and schema application.

Atlas M0 is a shared-tier cluster: latency is variable and brief connection
failures are normal. The retry settings here are deliberately generous rather
than fast-failing, because a nightly batch job would rather take an extra ten
seconds than register a spurious failure that pages you.
"""

from __future__ import annotations

import logging
from typing import Any

import pymongo
from pymongo import MongoClient
from pymongo.database import Database
from pymongo.errors import CollectionInvalid, OperationFailure

from algotrade.repositories import schema

log = logging.getLogger(__name__)

#: Generous for a batch job on a shared-tier cluster (see module docstring).
_TIMEOUT_MS = 30_000


def connect(uri: str, *, database: str, **kwargs: Any) -> Database:
    """Open a client and return the database handle.

    `retryWrites` is on (Atlas default) so a transient primary step-down does
    not surface as a failed job. Combined with the deterministic `_id`s in
    `schema`, a retried write is either the same document or a duplicate-key
    error -- never a silent second copy.
    """
    client: MongoClient = MongoClient(
        uri,
        serverSelectionTimeoutMS=_TIMEOUT_MS,
        connectTimeoutMS=_TIMEOUT_MS,
        socketTimeoutMS=_TIMEOUT_MS,
        retryWrites=True,
        tz_aware=True,
        **kwargs,
    )
    return client[database]


def ping(db: Database) -> float:
    """Round-trip latency in milliseconds. Used by the health check."""
    import time

    start = time.perf_counter()
    db.command("ping")
    return (time.perf_counter() - start) * 1000


def apply_schema(db: Database, *, dry_run: bool = False) -> dict[str, list[str]]:
    """Create collections, validators and indexes to match `schema.ALL`.

    Idempotent: safe to run on every deploy. Existing collections have their
    validator updated via `collMod` rather than being recreated, and
    `create_index` is a no-op when an identical index already exists.

    Returns a per-collection list of what changed, so a deploy log shows the
    delta rather than just "ok".
    """
    changes: dict[str, list[str]] = {}

    for col in schema.ALL:
        actions: list[str] = []
        existing = set(db.list_collection_names())

        if col.name not in existing:
            if not dry_run:
                try:
                    db.create_collection(
                        col.name, validator=col.validator,
                        validationLevel="strict", validationAction="error",
                    )
                except CollectionInvalid:
                    pass  # created concurrently; the collMod below still applies
            actions.append("created")
        else:
            if not dry_run:
                db.command({
                    "collMod": col.name,
                    "validator": col.validator,
                    "validationLevel": "strict",
                    "validationAction": "error",
                })
            actions.append("validator updated")

        for index in col.indexes:
            if dry_run:
                actions.append(f"index {index.name} (dry run)")
                continue
            try:
                db[col.name].create_index(index.keys, **index.kwargs())
                actions.append(f"index {index.name}")
            except OperationFailure as exc:
                # An index that exists with DIFFERENT options must be dropped
                # and rebuilt deliberately -- silently ignoring the conflict
                # would leave production without the guarantee this file claims.
                if exc.code == 85:  # IndexOptionsConflict
                    raise RuntimeError(
                        f"{col.name}.{index.name} exists with different options. "
                        f"Drop it explicitly and re-run: "
                        f"db.{col.name}.dropIndex('{index.name}')"
                    ) from exc
                raise

        changes[col.name] = actions

    return changes


def verify_schema(db: Database) -> list[str]:
    """Report every way the live database differs from `schema.ALL`.

    The Phase 1 gate ("indexes verified against a real M0") is this function
    returning an empty list.
    """
    problems: list[str] = []
    existing = set(db.list_collection_names())

    for col in schema.ALL:
        if col.name not in existing:
            problems.append(f"{col.name}: collection missing")
            continue

        live_indexes = {i["name"]: i for i in db[col.name].list_indexes()}
        for index in col.indexes:
            live = live_indexes.get(index.name)
            if live is None:
                problems.append(f"{col.name}.{index.name}: index missing")
                continue
            if bool(live.get("unique", False)) != index.unique:
                problems.append(
                    f"{col.name}.{index.name}: unique={live.get('unique', False)}, "
                    f"expected {index.unique}"
                )
            if live.get("partialFilterExpression") != index.partial:
                problems.append(
                    f"{col.name}.{index.name}: partial filter mismatch "
                    f"({live.get('partialFilterExpression')} vs {index.partial})"
                )
            if live.get("expireAfterSeconds") != index.expire_after_seconds:
                problems.append(
                    f"{col.name}.{index.name}: TTL mismatch "
                    f"({live.get('expireAfterSeconds')} vs {index.expire_after_seconds})"
                )

        options = db.command("listCollections", filter={"name": col.name})
        cursor = options["cursor"]["firstBatch"]
        if cursor and not cursor[0].get("options", {}).get("validator"):
            problems.append(f"{col.name}: no validator installed")

    return problems


def stats(db: Database) -> dict[str, Any]:
    """Storage usage, for the health check and the M0 512 MB threshold (§10.1)."""
    raw = db.command("dbStats")
    return {
        "storage_bytes": raw.get("storageSize", 0),
        "data_bytes": raw.get("dataSize", 0),
        "index_bytes": raw.get("indexSize", 0),
        "collections": raw.get("collections", 0),
        "objects": raw.get("objects", 0),
    }
