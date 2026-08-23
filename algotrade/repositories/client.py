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


def live_validator(db: Database, name: str) -> dict | None:
    """The validator currently installed on a collection, if any."""
    batch = db.command("listCollections", filter={"name": name})["cursor"]["firstBatch"]
    if not batch:
        return None
    return batch[0].get("options", {}).get("validator")


def _is_privilege_error(exc: OperationFailure, action: str) -> bool:
    """Atlas reports a missing privilege as code 8000 with the action named."""
    return exc.code == 8000 and action in str(exc)


def _update_validator(db: Database, col) -> str:  # noqa: ANN001
    """Bring an existing collection's validator up to date.

    **Atlas's built-in `readWrite` role does not include `collMod`.** A user
    scoped that way can create a collection WITH a validator but cannot alter
    one afterwards -- so the first deploy succeeds and every later one fails.
    A local Docker MongoDB never shows this, because its default user is root.

    Rather than fail, this checks whether the change was needed at all. If the
    installed validator already matches, nothing has to happen and the run
    proceeds. If it genuinely differs, the error is explicit about which Atlas
    privilege is missing instead of surfacing a raw AtlasError.
    """
    try:
        db.command({
            "collMod": col.name,
            "validator": col.validator,
            "validationLevel": "strict",
            "validationAction": "error",
        })
        return "validator updated"
    except OperationFailure as exc:
        if not _is_privilege_error(exc, "collMod"):
            raise
        if live_validator(db, col.name) == col.validator:
            return "validator already correct (collMod not permitted; no change needed)"

        # An EMPTY collection with the wrong (usually absent) validator is
        # almost always one MongoDB auto-created on a stray insert before
        # init_db ever ran. Under a readWrite-only user that collection can
        # never be fixed by collMod -- but dropping and recreating it is
        # permitted, and is safe precisely because it holds nothing.
        if db[col.name].estimated_document_count() == 0:
            db[col.name].drop()
            db.create_collection(
                col.name, validator=col.validator,
                validationLevel="strict", validationAction="error",
            )
            return "recreated (was empty and unvalidated; collMod not permitted)"

        raise RuntimeError(
            f"{col.name}: the validator differs from schema.py but this database "
            f"user cannot run collMod. Atlas's built-in readWrite role excludes "
            f"it -- grant dbAdmin on this database (Atlas -> Database Access -> "
            f"Edit user -> Add role 'dbAdmin' scoped to the database), then "
            f"re-run. Original error: {exc}"
        ) from exc


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
                actions.append(_update_validator(db, col))
            else:
                actions.append("validator (dry run)")

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
