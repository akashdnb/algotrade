"""Tests for the collection/index definitions (design §2).

These are data, so they can be checked without a cluster. What is verified here
is that the *guarantees* the design depends on are actually expressed --
particularly the unique indexes that make retries idempotent, and the TTL that
must never be attached to a financial record.
"""

from __future__ import annotations

import pytest

from algotrade.repositories import schema

D = schema.BY_NAME


def test_all_eleven_collections_are_defined():
    assert set(D) == {
        "portfolios", "holdings", "recommendations", "executions", "signals",
        "cash_transactions", "portfolio_snapshots", "rebalance_runs",
        "audit_logs", "job_runs", "notifications",
    }


@pytest.mark.parametrize("name", list(D))
def test_every_collection_has_a_validator_and_a_stated_retention(name):
    col = D[name]
    assert "$jsonSchema" in col.validator
    assert col.retention, f"{name} does not state its retention policy"


@pytest.mark.parametrize("name", list(D))
def test_index_names_are_unique_within_a_collection(name):
    names = [i.name for i in D[name].indexes]
    assert len(names) == len(set(names))


def test_job_runs_permits_at_most_one_success_per_job_per_business_date():
    """The exactly-once guarantee (§11.5). Without the partial filter this
    would also block legitimate retries after a failure."""
    idx = next(i for i in D["job_runs"].indexes if i.name == "one_success")
    assert idx.unique
    assert idx.keys == [("job_name", 1), ("business_date", 1)]
    assert idx.partial == {"status": {"$eq": "success"}}


def test_snapshot_and_rebalance_identity_is_carried_by_the_id_not_an_index():
    """`_id` is portfolio:date and portfolio:period respectively, so a duplicate
    insert fails on the primary key -- no secondary unique index needed, and
    adding one would just be a second thing to keep in sync."""
    snap = D["portfolio_snapshots"].validator["$jsonSchema"]
    assert "_id" in snap["required"]
    reb = D["rebalance_runs"].validator["$jsonSchema"]
    assert "_id" in reb["required"]


def test_audit_ttl_is_on_a_nullable_field_and_never_on_the_timestamp():
    """A TTL on `ts` would eventually delete the trade history this collection
    exists to preserve. It must key off an explicitly-set expires_at."""
    ttl = [i for i in D["audit_logs"].indexes if i.expire_after_seconds is not None]
    assert len(ttl) == 1
    assert ttl[0].keys == [("expires_at", 1)]
    assert ttl[0].expire_after_seconds == 0

    assert not any(
        i.expire_after_seconds is not None and i.keys[0][0] == "ts"
        for i in D["audit_logs"].indexes
    )


def test_financial_collections_have_no_ttl_at_all():
    for name in ("portfolios", "holdings", "recommendations", "executions",
                 "signals", "cash_transactions", "portfolio_snapshots",
                 "rebalance_runs"):
        assert not any(
            i.expire_after_seconds is not None for i in D[name].indexes
        ), f"{name} must never expire -- it is a financial record"


def test_operational_collections_do_expire():
    for name in ("job_runs", "notifications"):
        assert any(i.expire_after_seconds is not None for i in D[name].indexes)


def test_money_fields_are_declared_as_decimal_in_every_validator():
    """A `double` in a money field is rejected at the database, not discovered
    later as a rounding discrepancy."""
    props = D["portfolios"].validator["$jsonSchema"]["properties"]
    for f in ("initial_capital", "cash", "total_contributed", "units"):
        assert props[f] == {"bsonType": "decimal"}

    snap = D["portfolio_snapshots"].validator["$jsonSchema"]["properties"]
    for f in ("nav", "cash", "unit_nav", "peak_unit_nav"):
        assert snap[f] == {"bsonType": "decimal"}


def test_every_declared_money_field_exists_in_its_validator():
    """Guards against MONEY_FIELDS and the validators drifting apart -- an
    encoded field the schema does not know about, or vice versa."""
    for name, fields in schema.MONEY_FIELDS.items():
        props = D[name].validator["$jsonSchema"].get("properties", {})
        for f in fields:
            assert f in props, f"{name}.{f} is encoded as money but absent from the schema"
            assert props[f] in ({"bsonType": "decimal"},
                                {"bsonType": ["decimal", "null"]}), (
                f"{name}.{f} is encoded as money but not declared decimal"
            )


def test_portfolio_id_pattern_matches_the_domain_rule():
    """The database and domain.ids must agree, or one will accept an id the
    other rejects."""
    from algotrade.domain import ids

    pattern = D["portfolios"].validator["$jsonSchema"]["properties"]["_id"]["pattern"]
    import re

    compiled = re.compile(pattern)
    for good in ("paper_momentum_v1_001", "live_momentum_v2_017", "shadow_x_v9_999"):
        assert compiled.match(good) and ids.is_valid(good)
    for bad in ("prod_momentum_v1_001", "paper_momentum_v1_1", "paper_Momentum_v1_001"):
        assert not compiled.match(bad) and not ids.is_valid(bad)


def test_business_dates_are_constrained_to_iso_strings():
    """Not BSON dates -- a trading date is a calendar fact and a timezone
    conversion can move it a day (IST is UTC+5:30)."""
    snap = D["portfolio_snapshots"].validator["$jsonSchema"]["properties"]["date"]
    assert snap["bsonType"] == "string"
    assert snap["pattern"] == r"^\d{4}-\d{2}-\d{2}$"


def test_status_enums_are_closed_sets_so_a_typo_cannot_invent_a_state():
    statuses = D["recommendations"].validator["$jsonSchema"]["properties"]["status"]
    assert set(statuses["enum"]) == {
        "pending", "notified", "approved", "rejected",
        "partially_executed", "completed", "expired", "superseded",
    }


def test_item_and_set_status_enums_match_the_domain_enums():
    from algotrade.domain.models import ItemStatus, SetStatus

    item = D["recommendations"].validator["$jsonSchema"]["properties"]["items"]
    item_enum = set(item["items"]["properties"]["status"]["enum"])
    assert item_enum == {s.value for s in ItemStatus}

    set_enum = set(
        D["recommendations"].validator["$jsonSchema"]["properties"]["status"]["enum"]
    )
    assert set_enum == {s.value for s in SetStatus}


def test_index_kwargs_render_what_pymongo_expects():
    idx = next(i for i in D["job_runs"].indexes if i.name == "one_success")
    assert idx.kwargs() == {
        "name": "one_success",
        "unique": True,
        "partialFilterExpression": {"status": {"$eq": "success"}},
    }

    ttl = next(i for i in D["notifications"].indexes if i.name == "ttl")
    assert ttl.kwargs() == {"name": "ttl", "expireAfterSeconds": 0}


# ------------------------------------------------- executions and signals ---


def test_a_broker_order_id_is_unique_per_portfolio_when_present():
    """Pasting the same fill twice is the realistic manual-entry mistake. The
    partial filter lets fills with no order id (paper mode) coexist freely."""
    idx = next(i for i in D["executions"].indexes if i.name == "order_id")
    assert idx.unique
    assert idx.partial == {"broker_order_id": {"$exists": True, "$gt": ""}}


def test_executions_carry_a_business_date_for_tax_year_queries():
    props = D["executions"].validator["$jsonSchema"]["properties"]
    assert props["executed_on"]["bsonType"] == "string"
    assert props["executed_at"]["bsonType"] == "date"
    assert any(
        i.keys == [("portfolio_id", 1), ("executed_on", -1)]
        for i in D["executions"].indexes
    )


def test_signals_are_keyed_by_strategy_version_not_portfolio():
    """The ranking is a property of the strategy and the market. Keying it by
    portfolio would store 30 identical rows per portfolio per day."""
    props = D["signals"].validator["$jsonSchema"]["properties"]
    assert "strategy_version" in props
    assert "portfolio_id" not in props
    assert any(
        i.keys == [("strategy_version", 1), ("date", -1)]
        for i in D["signals"].indexes
    )


def test_the_benchmark_sleeve_is_optional_and_bounded():
    """Absent for a momentum-only portfolio; a weight outside 0..1 is nonsense."""
    sleeve = D["portfolios"].validator["$jsonSchema"]["properties"]["benchmark_sleeve"]
    assert sleeve["bsonType"] == ["object", "null"]
    weight = sleeve["properties"]["target_weight"]
    assert weight["minimum"] == 0 and weight["maximum"] == 1


# ------------------------------------------------------- cash ledger --------


def test_one_ledger_entry_per_execution_is_enforced_by_a_unique_index():
    """The guard against a replayed fill double-counting cash. Partial so that
    non-execution entries (contributions, adjustments) are unconstrained."""
    idx = next(i for i in D["cash_transactions"].indexes if i.name == "execution")
    assert idx.unique
    assert idx.partial == {"execution_id": {"$type": "string"}}


def test_cash_entries_are_signed_amounts_not_debit_credit_columns():
    """balance = sum(amount). One column cannot disagree with another."""
    props = D["cash_transactions"].validator["$jsonSchema"]["properties"]
    assert props["amount"] == {"bsonType": "decimal"}
    assert "debit" not in props and "credit" not in props


def test_the_ledger_covers_every_kind_of_cash_movement():
    kinds = set(D["cash_transactions"].validator["$jsonSchema"]
                ["properties"]["kind"]["enum"])
    assert kinds == {
        "opening_balance", "contribution", "withdrawal", "buy", "sell",
        "charges", "dividend", "adjustment",
    }


def test_the_ledger_never_expires():
    assert not any(i.expire_after_seconds is not None
                   for i in D["cash_transactions"].indexes)


# ------------------------------------------- the realised_pnl rename (09-01) --


def test_holdings_decode_reads_the_old_field_name():
    """`realised_pnl_ytd` was renamed to `realised_pnl` because it never reset.

    Rows written before the rename must still read correctly -- otherwise a
    lifetime gain silently becomes zero for the window between deploying and
    the next holdings rebuild.
    """
    from decimal import Decimal

    from algotrade.repositories import codec
    from algotrade.repositories.holdings import HoldingRepository

    doc = {
        "portfolio_id": "paper_momentum_v1_001", "symbol": "OLDCO",
        "lots": [], "realised_pnl_ytd": codec.to_mongo_money(Decimal("1234.50")),
    }
    holding = HoldingRepository._to_entity(None, doc)
    assert holding.realised_pnl == Decimal("1234.50")


def test_holdings_decode_prefers_the_new_field_name():
    from decimal import Decimal

    from algotrade.repositories import codec
    from algotrade.repositories.holdings import HoldingRepository

    doc = {
        "portfolio_id": "p", "symbol": "X", "lots": [],
        "realised_pnl": codec.to_mongo_money(Decimal("10.00")),
        "realised_pnl_ytd": codec.to_mongo_money(Decimal("999.00")),
    }
    assert HoldingRepository._to_entity(None, doc).realised_pnl == Decimal("10.00")


def test_a_holding_with_neither_field_reads_as_zero():
    from decimal import Decimal

    from algotrade.repositories.holdings import HoldingRepository

    doc = {"portfolio_id": "p", "symbol": "X", "lots": []}
    assert HoldingRepository._to_entity(None, doc).realised_pnl == Decimal("0")


def test_both_field_names_are_money_so_neither_becomes_a_float():
    """The old name stays declared until every stored row has been rebuilt."""
    from algotrade.repositories import schema

    assert set(schema.MONEY_FIELDS["holdings"]) == {
        "realised_pnl", "realised_pnl_ytd"}
