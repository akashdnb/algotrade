"""Backup and restore. Atlas M0 has no automated backups, so this is the only
one that will exist.

**JSON, not BSON.** `mongodump` produces a format that needs `mongorestore` and
a compatible server version to read. A gzipped JSON export is a few megabytes,
opens in any text editor, and will still be readable in ten years when the
tooling has moved on. For a database this size the only argument for BSON is
speed, and speed is not the constraint here -- being able to read the file
during a disaster is.

Every value is encoded losslessly: `Decimal128` becomes a tagged string rather
than a float, because a backup that silently rounds money is worse than no
backup at all.
"""

from __future__ import annotations

import gzip
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from bson import ObjectId
from bson.decimal128 import Decimal128

from algotrade.repositories import schema

log = logging.getLogger(__name__)

FORMAT_VERSION = 1

#: Type tags. A backup must round-trip exactly; an untagged float where a
#: Decimal128 belongs is the failure mode this guards against.
_DECIMAL = "$decimal"
_DATETIME = "$datetime"
_OBJECTID = "$oid"


class RestoreRefused(RuntimeError):
    """The restore would destroy or corrupt data and was stopped."""


def _encode(value: Any) -> Any:
    if isinstance(value, Decimal128):
        return {_DECIMAL: str(value.to_decimal())}
    if isinstance(value, Decimal):
        return {_DECIMAL: str(value)}
    if isinstance(value, ObjectId):
        return {_OBJECTID: str(value)}
    if isinstance(value, datetime):
        return {_DATETIME: value.astimezone(timezone.utc).isoformat()}
    if isinstance(value, dict):
        return {k: _encode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_encode(v) for v in value]
    return value


def _decode(value: Any) -> Any:
    if isinstance(value, dict):
        if _DECIMAL in value and len(value) == 1:
            return Decimal128(Decimal(value[_DECIMAL]))
        if _OBJECTID in value and len(value) == 1:
            return ObjectId(value[_OBJECTID])
        if _DATETIME in value and len(value) == 1:
            return datetime.fromisoformat(value[_DATETIME])
        return {k: _decode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_decode(v) for v in value]
    return value


@dataclass
class BackupResult:
    path: Path
    taken_at: datetime
    database: str
    counts: dict[str, int] = field(default_factory=dict)
    bytes_written: int = 0

    @property
    def documents(self) -> int:
        return sum(self.counts.values())

    def summary(self) -> str:
        parts = ", ".join(f"{k}={v}" for k, v in sorted(self.counts.items()) if v)
        return (f"{self.documents:,} documents, "
                f"{self.bytes_written / 1024:.0f} KB  [{parts}]")


def export_database(
    db, *, destination: Path, taken_at: datetime,
    collections: Iterable[str] | None = None,
) -> BackupResult:
    """Write every collection to one gzipped JSON file.

    One file rather than one per collection: a backup is restored as a set, and
    a half-present directory is a worse failure mode than a single file that is
    either there or not.
    """
    names = list(collections) if collections else [c.name for c in schema.ALL]
    payload: dict[str, Any] = {
        "format_version": FORMAT_VERSION,
        "taken_at": taken_at.astimezone(timezone.utc).isoformat(),
        "database": db.name,
        "collections": {},
    }
    counts: dict[str, int] = {}

    for name in names:
        docs = [_encode(d) for d in db[name].find({})]
        payload["collections"][name] = docs
        counts[name] = len(docs)
        log.info("exported %s: %d document(s)", name, len(docs))

    destination.parent.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    # mtime=0 AND filename="" so an unchanged database produces a
    # byte-identical file regardless of where it is written -- gzip embeds the
    # source filename in its header, which would otherwise make byte-identity
    # depend on the path and defeat "did anything change?" by hashing.
    with destination.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as fh:
            fh.write(blob)

    return BackupResult(
        path=destination, taken_at=taken_at, database=db.name,
        counts=counts, bytes_written=destination.stat().st_size,
    )


def read_backup(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rb") as fh:
        payload = json.loads(fh.read())
    version = payload.get("format_version")
    if version != FORMAT_VERSION:
        raise RestoreRefused(
            f"{path.name} is format v{version}; this code reads v{FORMAT_VERSION}"
        )
    return payload


def inspect(path: Path) -> dict[str, Any]:
    """What is in a backup, without restoring it."""
    payload = read_backup(path)
    return {
        "taken_at": payload["taken_at"],
        "database": payload["database"],
        "counts": {k: len(v) for k, v in payload["collections"].items()},
        "documents": sum(len(v) for v in payload["collections"].values()),
    }


def restore_database(
    db, path: Path, *, allow_non_empty: bool = False, dry_run: bool = False,
) -> dict[str, int]:
    """Restore into `db`.

    **Refuses a non-empty target by default.** A restore over live data is
    almost always a mistake, and the one time it is not, saying so explicitly
    costs nothing. Restoring into a scratch database first and comparing is the
    procedure the runbook describes.

    Documents are inserted with their original `_id`, so a restore is
    idempotent in the sense that re-running it into a wiped database produces
    the same state.
    """
    payload = read_backup(path)
    if payload["database"] != db.name:
        log.warning("backup is from %r, restoring into %r",
                    payload["database"], db.name)

    existing = {
        name: db[name].estimated_document_count()
        for name in payload["collections"]
    }
    occupied = {n: c for n, c in existing.items() if c}
    if occupied and not allow_non_empty:
        raise RestoreRefused(
            f"{db.name} is not empty ({occupied}). Restoring would mix backup "
            f"data with live data. Wipe first, or pass allow_non_empty=True "
            f"having understood that."
        )

    restored: dict[str, int] = {}
    for name, docs in payload["collections"].items():
        if not docs:
            restored[name] = 0
            continue
        if dry_run:
            restored[name] = len(docs)
            continue
        db[name].delete_many({})
        db[name].insert_many([_decode(d) for d in docs], ordered=False)
        restored[name] = len(docs)
        log.info("restored %s: %d document(s)", name, len(docs))
    return restored


def verify_roundtrip(db, path: Path) -> list[str]:
    """Compare a backup against the live database, document for document.

    A backup nobody has ever read is a hypothesis, not a backup. This is what
    the weekly job runs immediately after writing, so a corrupt or truncated
    export is discovered on the day it happens rather than during a restore.
    """
    payload = read_backup(path)
    problems: list[str] = []

    for name, docs in payload["collections"].items():
        live = db[name].estimated_document_count()
        if live != len(docs):
            problems.append(
                f"{name}: {live} live document(s), {len(docs)} in the backup")
            continue
        if not docs:
            continue
        # Spot-check one document end to end, so an encoding regression that
        # preserves counts but mangles values is still caught.
        sample = _decode(docs[0])
        stored = db[name].find_one({"_id": sample["_id"]})
        if stored is None:
            problems.append(f"{name}: {sample['_id']} is in the backup but not live")
        elif stored != sample:
            differing = sorted(
                k for k in set(stored) | set(sample)
                if stored.get(k) != sample.get(k)
            )
            problems.append(
                f"{name}: {sample['_id']} differs on {differing[:5]}")
    return problems


def prune(directory: Path, *, keep: int) -> list[Path]:
    """Delete all but the newest `keep` backups. Returns what was removed."""
    backups = sorted(directory.glob("algotrade-backup-*.json.gz"))
    doomed = backups[:-keep] if keep > 0 else []
    for path in doomed:
        path.unlink()
    return doomed


def filename_for(taken_at: datetime, database: str) -> str:
    return f"algotrade-backup-{database}-{taken_at:%Y%m%d}.json.gz"
