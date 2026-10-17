"""Tests for the collection/index definitions (design §2)."""

from __future__ import annotations

import pytest

from algotrade.repositories import schema

D = schema.BY_NAME


def test_all_collections_are_defined():
    assert set(D) == {
        "portfolios", "holdings", "recommendations", "executions", "signals",
        "cash_transactions", "portfolio_snapshots", "rebalance_runs",
        "audit_logs", "job_runs", "notifications", "broker_sessions", "symbol_map",
        "submissions", "corporate_actions", "zones",
    }


@pytest.mark.parametrize("name", list(D))
def test_every_collection_has_a_validator_and_a_stated_retention(name):
    col = D[name]
    assert "$jsonSchema" in col.validator
    assert col.retention
