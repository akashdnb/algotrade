"""Tests for portfolio creation.

Idempotency is the property under test: re-running the bootstrap must be safe,
but a re-run asking for DIFFERENT terms must fail loudly rather than be
ignored -- silently accepting it would let a typo reset a track record.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from algotrade.domain import ids, money
from algotrade.domain.models import BenchmarkSleeve, PortfolioStatus
from algotrade.services.portfolio import PortfolioConflict, PortfolioService
from tests.unit.fakes import FakeAudit, FakeRepo

NOW = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
R = money.rupees
HASH = "sha256:abc123"


def _service() -> tuple[PortfolioService, FakeRepo, FakeAudit]:
    repo, audit = FakeRepo(), FakeAudit()
    return PortfolioService(repository=repo, audit=audit), repo, audit


def _create(service, **over):
    kwargs = dict(
        portfolio_id="paper_momentum_v1_001", label="Paper Momentum 30",
        strategy_params_hash=HASH, initial_capital=R("200000.00"), now=NOW,
    )
    kwargs.update(over)
    return service.create(**kwargs)


def test_a_new_portfolio_starts_as_all_cash():
    """No holdings; the first rebalance is what deploys the capital."""
    service, _, _ = _service()
    result = _create(service)
    p = result.portfolio

    assert result.created
    assert p.cash == R("200000.00")
    assert p.total_contributed == R("200000.00")
    assert p.status == PortfolioStatus.ACTIVE


def test_units_are_issued_at_the_base_nav():
    """200,000 at a base unit NAV of 100 = 2,000 units, carried to three
    decimals so later contributions do not lose precision."""
    service, _, _ = _service()
    p = _create(service).portfolio
    assert p.units == Decimal("2000.000")


def test_re_creating_is_a_no_op_not_a_second_portfolio():
    service, repo, _ = _service()
    first = _create(service)
    second = _create(service)

    assert first.created and not second.created
    assert len(repo.docs) == 1


def test_different_capital_on_a_re_run_is_a_conflict():
    service, _, _ = _service()
    _create(service)
    with pytest.raises(PortfolioConflict, match="initial capital"):
        _create(service, initial_capital=R("500000.00"))


def test_changed_strategy_params_are_a_conflict_not_an_update():
    """A parameter change forks a NEW portfolio. Mutating this one would make
    its history describe two different strategies."""
    service, _, _ = _service()
    _create(service)
    with pytest.raises(PortfolioConflict, match="forks a NEW portfolio"):
        _create(service, strategy_params_hash="sha256:different")


def test_a_malformed_id_is_rejected():
    service, _, _ = _service()
    with pytest.raises(ids.InvalidPortfolioId):
        _create(service, portfolio_id="PAPER_MAIN")


def test_the_id_and_the_strategy_argument_must_agree():
    service, _, _ = _service()
    with pytest.raises(PortfolioConflict, match="strategy"):
        _create(service, strategy="lowvol")


def test_strategy_version_is_derived_from_the_id():
    """Signals are keyed by strategy_version, so it must match the id or a
    portfolio would read another strategy's ranking."""
    service, repo, _ = _service()
    _create(service)
    assert repo.docs["paper_momentum_v1_001"]["strategy_version"] == "momentum_v1"


def test_mode_is_derived_and_is_live_is_never_stored():
    """A stored is_live flag could contradict the id, and then neither would
    be trustworthy."""
    service, repo, _ = _service()
    _create(service)
    doc = repo.docs["paper_momentum_v1_001"]
    assert doc["mode"] == "paper"
    assert "is_live" not in doc
    assert ids.parse(doc["_id"]).is_live is False


def test_creation_is_audited():
    service, _, audit = _service()
    _create(service)
    assert "portfolio.created" in audit.actions()


def test_a_no_op_re_creation_is_not_audited_again():
    service, _, audit = _service()
    _create(service)
    _create(service)
    assert audit.actions().count("portfolio.created") == 1


def test_a_benchmark_sleeve_is_stored_when_requested():
    service, repo, _ = _service()
    _create(service, benchmark_sleeve=BenchmarkSleeve(
        enabled=True, index="NIFTY_50", target_weight=0.5))
    sleeve = repo.docs["paper_momentum_v1_001"]["benchmark_sleeve"]
    assert sleeve["enabled"] and sleeve["target_weight"] == 0.5


def test_no_sleeve_is_stored_for_a_momentum_only_portfolio():
    service, repo, _ = _service()
    _create(service)
    assert "benchmark_sleeve" not in repo.docs["paper_momentum_v1_001"]


def test_get_returns_none_for_an_unknown_portfolio():
    service, _, _ = _service()
    assert service.get("paper_momentum_v1_999") is None


def test_a_concurrent_creation_resolves_to_the_stored_document():
    """Two runners racing: the database is the arbiter, so the loser re-reads
    and validates rather than retrying or overwriting."""
    service, repo, _ = _service()

    original_get = repo.get
    calls = {"n": 0}

    def racing_get(_id):
        # Report "absent" on the first look, so create() proceeds to insert,
        # but populate the store underneath it in the meantime.
        calls["n"] += 1
        if calls["n"] == 1:
            repo.docs[_id] = {
                "_id": _id, "mode": "paper", "strategy": "momentum",
                "strategy_version": "momentum_v1", "strategy_params_hash": HASH,
                "status": "active", "cash": R("200000.00"),
                "initial_capital": R("200000.00"), "units": Decimal("2000.000"),
            }
            return None
        return original_get(_id)

    repo.get = racing_get
    result = service.create(
        portfolio_id="paper_momentum_v1_001", label="x",
        strategy_params_hash=HASH, initial_capital=R("200000.00"), now=NOW,
    )
    assert not result.created
    assert len(repo.docs) == 1
