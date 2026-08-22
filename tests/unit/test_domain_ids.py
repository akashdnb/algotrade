"""Tests for portfolio_id parsing and validation (design §3)."""

from __future__ import annotations

import pytest

from algotrade.domain import ids


def test_a_well_formed_id_parses_into_its_parts():
    pid = ids.parse("paper_momentum_v1_001")
    assert pid.mode == "paper"
    assert pid.strategy == "momentum"
    assert pid.version == "v1"
    assert pid.seq == 1
    assert str(pid) == "paper_momentum_v1_001"


@pytest.mark.parametrize("value", ["paper", "live", "shadow"])
def test_all_three_modes_are_accepted(value):
    assert ids.parse(f"{value}_momentum_v1_001").mode == value


@pytest.mark.parametrize(
    "value",
    [
        "prod_momentum_v1_001",      # unknown mode
        "paper_momentum_1_001",      # version missing the v
        "paper_momentum_v1_1",       # seq not zero-padded to 3
        "paper_momentum_v1_0001",    # seq too long
        "paper_Momentum_v1_001",     # uppercase strategy
        "paper_momentum_v1",         # missing seq
        "paper__v1_001",             # empty strategy
        "",
        "paper_momentum_v1_001 ",    # trailing space
    ],
)
def test_malformed_ids_are_rejected(value):
    with pytest.raises(ids.InvalidPortfolioId):
        ids.parse(value)


def test_build_round_trips_through_parse():
    built = ids.build(mode="live", strategy="momentum", version="v1", seq=7)
    assert built == "live_momentum_v1_001".replace("001", "007")
    assert ids.parse(built).seq == 7


def test_build_rejects_a_sequence_that_cannot_be_represented():
    with pytest.raises(ids.InvalidPortfolioId):
        ids.build(mode="live", strategy="momentum", version="v1", seq=1000)
    with pytest.raises(ids.InvalidPortfolioId):
        ids.build(mode="live", strategy="momentum", version="v1", seq=0)


def test_is_valid_does_not_raise():
    assert ids.is_valid("paper_momentum_v1_001")
    assert not ids.is_valid("nonsense")


def test_is_live_distinguishes_real_money_from_everything_else():
    """The guard that gates destructive or irreversible operations."""
    assert ids.parse("live_momentum_v1_001").is_live
    assert not ids.parse("paper_momentum_v1_001").is_live
    assert not ids.parse("shadow_momentum_v1_001").is_live


def test_strategy_key_pairs_strategy_with_its_frozen_version():
    """Two portfolios share a signal only if BOTH strategy and version match --
    v1 and v2 are different strategies that must never share a params hash."""
    assert ids.parse("paper_momentum_v1_001").strategy_key == "momentum_v1"
    assert ids.parse("live_momentum_v2_003").strategy_key == "momentum_v2"
