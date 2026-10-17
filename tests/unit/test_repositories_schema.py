"""Tests for collection/index definitions and schema invariants."""

from __future__ import annotations

import re
from decimal import Decimal

import pytest

from algotrade.domain import ids
from algotrade.domain.models import ItemStatus, SetStatus
from algotrade.repositories import schema

D = schema.BY_NAME


def test_all_collections_are_defined():
    assert set(D) == {
        "portfolios", "holdings", "recommendations", "executions", "signals",
        "cash_transactions", "portfolio_snapshots", "rebalance_runs", "audit_logs",
        "job_runs", "notifications", "broker_sessions", "symbol_map", "submissions",
        "corporate_actions", "zones",
    }


@pytest.mark.parametrize("name", list(D))
def test_every_collection_has_validator_retention_and_unique_index_names(name):
    col = D[name]
    assert "$jsonSchema" in col.validator
    assert col.retention
    names = [i.name for i in col.indexes]
    assert len(names) == len(set(names))


def test_job_runs_exactly_once_success_guard():
    idx = next(i for i in D["job_runs"].indexes if i.name == "one_success")
    assert idx.unique and idx.keys == [("job_name", 1), ("business_date", 1)]
    assert idx.partial == {"status": {"$eq": "success"}}


def test_snapshot_and_rebalance_identity_is_carried_by_id():
    assert "_id" in D["portfolio_snapshots"].validator["$jsonSchema"]["required"]
    assert "_id" in D["rebalance_runs"].validator["$jsonSchema"]["required"]


def test_audit_ttl_is_on_expiry_field_only():
    ttl = [i for i in D["audit_logs"].indexes if i.expire_after_seconds is not None]
    assert len(ttl) == 1 and ttl[0].keys == [("expires_at", 1)] and ttl[0].expire_after_seconds == 0
    assert not any(i.expire_after_seconds is not None and i.keys[0][0] == "ts" for i in D["audit_logs"].indexes)


def test_financial_collections_never_expire():
    for name in ("portfolios", "holdings", "recommendations", "executions", "signals", "cash_transactions", "portfolio_snapshots", "rebalance_runs"):
        assert not any(i.expire_after_seconds is not None for i in D[name].indexes)


def test_operational_collections_expire():
    for name in ("job_runs", "notifications"):
        assert any(i.expire_after_seconds is not None for i in D[name].indexes)


def test_money_fields_are_decimal_and_match_declarations():
    for name, fields in schema.MONEY_FIELDS.items():
        props = D[name].validator["$jsonSchema"].get("properties", {})
        for field in fields:
            assert field in props
            assert props[field] in ({"bsonType": "decimal"}, {"bsonType": ["decimal", "null"]})
    props = D["portfolios"].validator["$jsonSchema"]["properties"]
    for field in ("initial_capital", "cash", "total_contributed", "units"):
        assert props[field] == {"bsonType": "decimal"}


def test_portfolio_id_pattern_matches_domain_rule():
    pattern = D["portfolios"].validator["$jsonSchema"]["properties"]["_id"]["pattern"]
    compiled = re.compile(pattern)
    for good in ("paper_momentum_v1_001", "live_momentum_v2_017", "shadow_x_v9_999"):
        assert compiled.match(good) and ids.is_valid(good)
    for bad in ("prod_momentum_v1_001", "paper_momentum_v1_1", "paper_Momentum_v1_001"):
        assert not compiled.match(bad) and not ids.is_valid(bad)


def test_business_dates_are_iso_strings():
    prop = D["portfolio_snapshots"].validator["$jsonSchema"]["properties"]["date"]
    assert prop["bsonType"] == "string" and prop["pattern"] == r"^\d{4}-\d{2}-\d{2}$"


def test_status_enums_match_domain():
    rec = D["recommendations"].validator["$jsonSchema"]["properties"]
    assert set(rec["status"]["enum"]) == {s.value for s in SetStatus}
    assert set(rec["items"]["items"]["properties"]["status"]["enum"]) == {s.value for s in ItemStatus}


def test_index_kwargs_render_correctly():
    idx = next(i for i in D["job_runs"].indexes if i.name == "one_success")
    assert idx.kwargs() == {"name": "one_success", "unique": True, "partialFilterExpression": {"status": {"$eq": "success"}}}
    ttl = next(i for i in D["notifications"].indexes if i.name == "ttl")
    assert ttl.kwargs() == {"name": "ttl", "expireAfterSeconds": 0}


def test_execution_and_signal_indexes_preserve_query_contracts():
    order = next(i for i in D["executions"].indexes if i.name == "order_id")
    assert order.unique and order.partial == {"broker_order_id": {"$exists": True, "$gt": ""}}
    props = D["executions"].validator["$jsonSchema"]["properties"]
    assert props["executed_on"]["bsonType"] == "string" and props["executed_at"]["bsonType"] == "date"
    signal_props = D["signals"].validator["$jsonSchema"]["properties"]
    assert "strategy_version" in signal_props and "portfolio_id" not in signal_props


def test_cash_ledger_invariants():
    idx = next(i for i in D["cash_transactions"].indexes if i.name == "execution")
    assert idx.unique and idx.partial == {"execution_id": {"$type": "string"}}
    props = D["cash_transactions"].validator["$jsonSchema"]["properties"]
    assert props["amount"] == {"bsonType": "decimal"} and "debit" not in props and "credit" not in props


def test_holdings_legacy_realised_pnl_compatibility():
    from algotrade.repositories import codec
    from algotrade.repositories.holdings import HoldingRepository

    old = {"portfolio_id": "p", "symbol": "X", "lots": [], "realised_pnl_ytd": codec.to_mongo_money(Decimal("1234.50"))}
    new = {"portfolio_id": "p", "symbol": "X", "lots": [], "realised_pnl": codec.to_mongo_money(Decimal("10.00")), "realised_pnl_ytd": codec.to_mongo_money(Decimal("999.00"))}
    assert HoldingRepository._to_entity(None, old).realised_pnl == Decimal("1234.50")
    assert HoldingRepository._to_entity(None, new).realised_pnl == Decimal("10.00")
