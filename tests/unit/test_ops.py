"""Tests for backup encoding and the health score.

The backup's one job is to round-trip losslessly. A backup that silently
rounds money is worse than no backup, because it fails only when you need it.
"""

from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from bson import ObjectId
from bson.decimal128 import Decimal128

from algotrade.ops import backup, health

NOW = datetime(2026, 10, 4, 3, 0, tzinfo=timezone.utc)


class FakeCollection:
    def __init__(self, docs=None):
        self.docs = list(docs or [])

    def find(self, query=None):
        return iter(self.docs)

    def find_one(self, query):
        return next((d for d in self.docs if d["_id"] == query["_id"]), None)

    def estimated_document_count(self):
        return len(self.docs)

    def delete_many(self, query):
        self.docs.clear()

    def insert_many(self, docs, ordered=True):
        self.docs.extend(docs)


class FakeDb:
    def __init__(self, collections=None):
        self.name = "algotrade_test"
        self._c = {k: FakeCollection(v) for k, v in (collections or {}).items()}

    def __getitem__(self, name):
        return self._c.setdefault(name, FakeCollection())


# ------------------------------------------------------------- encoding -----


def test_decimal128_round_trips_as_a_tagged_string_not_a_float():
    """A float rupee amount is wrong the moment it is written. The tag is what
    stops JSON turning Decimal128 into a double on the way out."""
    original = Decimal128(Decimal("284310.22"))
    encoded = backup._encode(original)
    assert encoded == {"$decimal": "284310.22"}
    assert json.loads(json.dumps(encoded))          # survives JSON
    assert backup._decode(encoded).to_decimal() == Decimal("284310.22")


def test_a_large_amount_keeps_every_paisa():
    original = Decimal128(Decimal("99999999.99"))
    assert backup._decode(backup._encode(original)).to_decimal() == \
        Decimal("99999999.99")


def test_object_ids_and_datetimes_round_trip():
    oid = ObjectId()
    assert backup._decode(backup._encode(oid)) == oid
    assert backup._decode(backup._encode(NOW)) == NOW


def test_nested_values_are_encoded_all_the_way_down():
    doc = {"lots": [{"price": Decimal128(Decimal("975.10"))}],
           "sleeve": {"value": Decimal128(Decimal("1.05"))}}
    decoded = backup._decode(backup._encode(doc))
    assert decoded["lots"][0]["price"].to_decimal() == Decimal("975.10")
    assert decoded["sleeve"]["value"].to_decimal() == Decimal("1.05")


def test_plain_values_are_untouched():
    doc = {"symbol": "TCS", "quantity": 47, "weight": 0.1673, "ok": True,
           "note": None}
    assert backup._encode(doc) == doc


# --------------------------------------------------------------- export -----


def _db():
    return FakeDb({
        "portfolios": [{"_id": "p1", "cash": Decimal128(Decimal("556.00"))}],
        "executions": [{"_id": "e1", "price": Decimal128(Decimal("2402.75"))}],
    })


def test_an_export_writes_gzipped_json_with_a_format_version(tmp_path: Path):
    result = backup.export_database(
        _db(), destination=tmp_path / "b.json.gz", taken_at=NOW,
        collections=["portfolios", "executions"])

    assert result.documents == 2
    with gzip.open(result.path) as fh:
        payload = json.loads(fh.read())
    assert payload["format_version"] == backup.FORMAT_VERSION
    assert payload["collections"]["portfolios"][0]["cash"] == {"$decimal": "556.00"}


def test_an_unchanged_database_produces_a_byte_identical_file(tmp_path: Path):
    """mtime=0 in the gzip header, so 'did anything change?' is answerable by
    hashing rather than by diffing 141 documents."""
    a = backup.export_database(_db(), destination=tmp_path / "a.gz", taken_at=NOW,
                               collections=["portfolios"])
    b = backup.export_database(_db(), destination=tmp_path / "b.gz", taken_at=NOW,
                               collections=["portfolios"])
    assert a.path.read_bytes() == b.path.read_bytes()


def test_inspect_reports_contents_without_restoring(tmp_path: Path):
    path = tmp_path / "b.json.gz"
    backup.export_database(_db(), destination=path, taken_at=NOW,
                           collections=["portfolios", "executions"])
    info = backup.inspect(path)
    assert info["documents"] == 2
    assert info["counts"]["portfolios"] == 1


def test_a_future_format_version_is_refused(tmp_path: Path):
    path = tmp_path / "b.json.gz"
    with gzip.open(path, "wb") as fh:
        fh.write(json.dumps({"format_version": 99, "collections": {}}).encode())
    with pytest.raises(backup.RestoreRefused, match="format v99"):
        backup.read_backup(path)


# -------------------------------------------------------------- restore -----


def test_restoring_into_a_non_empty_database_is_refused(tmp_path: Path):
    """A restore over live data is almost always a mistake, and the one time
    it is not, saying so explicitly costs nothing."""
    path = tmp_path / "b.json.gz"
    backup.export_database(_db(), destination=path, taken_at=NOW,
                           collections=["portfolios"])
    with pytest.raises(backup.RestoreRefused, match="not empty"):
        backup.restore_database(_db(), path)


def test_force_allows_restoring_over_live_data(tmp_path: Path):
    path = tmp_path / "b.json.gz"
    backup.export_database(_db(), destination=path, taken_at=NOW,
                           collections=["portfolios"])
    restored = backup.restore_database(_db(), path, allow_non_empty=True)
    assert restored["portfolios"] == 1


def test_a_dry_run_reports_without_writing(tmp_path: Path):
    path = tmp_path / "b.json.gz"
    backup.export_database(_db(), destination=path, taken_at=NOW,
                           collections=["portfolios"])
    target = FakeDb()
    restored = backup.restore_database(target, path, dry_run=True)
    assert restored["portfolios"] == 1
    assert target["portfolios"].docs == []


def test_a_restore_preserves_decimal_precision(tmp_path: Path):
    path = tmp_path / "b.json.gz"
    backup.export_database(_db(), destination=path, taken_at=NOW,
                           collections=["portfolios"])
    target = FakeDb()
    backup.restore_database(target, path)
    assert target["portfolios"].docs[0]["cash"].to_decimal() == Decimal("556.00")


# ------------------------------------------------------------- verify -------


def test_verification_passes_on_a_faithful_backup(tmp_path: Path):
    db = _db()
    path = tmp_path / "b.json.gz"
    backup.export_database(db, destination=path, taken_at=NOW,
                           collections=["portfolios", "executions"])
    assert backup.verify_roundtrip(db, path) == []


def test_verification_catches_a_count_mismatch(tmp_path: Path):
    db = _db()
    path = tmp_path / "b.json.gz"
    backup.export_database(db, destination=path, taken_at=NOW,
                           collections=["portfolios"])
    db["portfolios"].docs.append({"_id": "p2"})
    problems = backup.verify_roundtrip(db, path)
    assert problems and "live document" in problems[0]


def test_verification_catches_a_changed_value(tmp_path: Path):
    """A count-preserving corruption is the one a naive check would miss."""
    db = _db()
    path = tmp_path / "b.json.gz"
    backup.export_database(db, destination=path, taken_at=NOW,
                           collections=["portfolios"])
    db["portfolios"].docs[0]["cash"] = Decimal128(Decimal("9999.00"))
    problems = backup.verify_roundtrip(db, path)
    assert problems and "differs on" in problems[0]


def test_pruning_keeps_the_newest(tmp_path: Path):
    for day in ("20260901", "20260908", "20260915"):
        (tmp_path / f"algotrade-backup-db-{day}.json.gz").write_bytes(b"x")
    removed = backup.prune(tmp_path, keep=2)
    assert len(removed) == 1
    assert "20260901" in removed[0].name


# --------------------------------------------------------------- health -----


def test_a_clean_system_scores_100_and_is_green():
    h = health.score()
    assert h.score == 100 and h.status == health.GREEN
    assert h.explanation == "all checks pass"
    assert h.actionable


def test_a_reconciliation_failure_dominates_the_score():
    """The book disagrees with its trade record, so every derived number is
    wrong -- that must outrank anything else."""
    h = health.score(reconciliation_failures=1)
    assert h.score == 60
    assert "reconciliation" in h.explanation


def test_reconciliation_plus_stale_data_goes_red_and_not_actionable():
    h = health.score(reconciliation_failures=1, sessions_behind=3)
    assert h.status == health.RED
    assert not h.actionable


def test_one_job_failure_is_minor_because_the_system_self_heals():
    assert health.score(consecutive_job_failures=1).score == 95


def test_two_consecutive_failures_are_not_minor():
    """Self-healing is not working, which is a different problem."""
    assert health.score(consecutive_job_failures=2).score == 85


def test_a_missing_dead_man_switch_is_penalised_heavily():
    h = health.score(heartbeat_configured=False)
    assert h.score == 80
    assert "silent" in h.deductions[0].detail


def test_a_missing_backup_is_penalised_only_when_it_was_checked_for():
    """'Not checked' and 'checked and absent' are different facts; scoring the
    first as the second would make every caller that ignores backups look
    unhealthy."""
    assert health.score(backup_checked=True, backup_age_days=None).score == 85
    assert health.score(backup_age_days=None).score == 100      # not checked
    assert health.score(backup_checked=True, backup_age_days=3).score == 100
    assert health.score(backup_checked=True, backup_age_days=30).score == 90


def test_missing_snapshots_are_capped_so_one_problem_cannot_dominate():
    assert health.score(missing_snapshots=50).score == 85


def test_the_score_never_goes_below_zero():
    h = health.score(
        reconciliation_failures=5, sessions_behind=10,
        consecutive_job_failures=5, missing_snapshots=20,
        notification_failures=9, stale_recommendation_days=30,
        heartbeat_configured=False, backup_age_days=None, backup_checked=True)
    assert h.score == 0 and h.status == health.RED


def test_every_deduction_explains_itself():
    """A score with no explanation is a number you learn to ignore."""
    h = health.score(reconciliation_failures=1, sessions_behind=2,
                     notification_failures=3)
    assert all(d.reason for d in h.deductions)
    assert all(d.points > 0 for d in h.deductions)


def test_the_health_dict_is_json_serialisable():
    payload = health.score(sessions_behind=1).to_dict()
    assert json.dumps(payload)
    assert set(payload) >= {"score", "status", "explanation", "actionable"}
