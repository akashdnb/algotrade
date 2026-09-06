"""Freezing a basket as the next revision of the recommendation (design §6).

Why a revision rather than a collection of its own: executions record against
a recommendation item, and `ExecutionService.record` refuses a seq it cannot
find. The resized instructions do not exist there -- a trim or a top-up has no
counterpart among that period's entries and exits -- so a basket stored
anywhere else could be approved and then never recorded.

A revision is also what this already means here. `r1 -> r2 -> r3` happened for
2026-08 with no strategy change.

What must carry forward is the DECISION; what may change is only its pricing.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from algotrade.domain.models import (
    Action,
    Item,
    RecommendationSet,
    SetStatus,
)
from algotrade.services import review

NOW = datetime(2026, 10, 1, 9, 20, tzinfo=timezone.utc)


def _item(seq, action, symbol, quantity, price="100.00") -> Item:
    return Item(seq=seq, action=action, symbol=symbol, quantity=quantity,
                reference_price=Decimal(price),
                estimated_charges=Decimal("10.00"), reason="rank 12")


def _source(**kwargs) -> RecommendationSet:
    args = dict(
        set_id="paper_momentum_v1_001:2026-10:r1",
        portfolio_id="paper_momentum_v1_001",
        rebalance_run_id="run-1",
        period="2026-10",
        as_of_session=date(2026, 9, 30),
        items=[_item(1, Action.BUY, "NEW", 10)],
        holds=["KEPT"],
        revision=1,
        status=SetStatus.PENDING,
        strategy_params_hash="sha256:abc",
        code_commit="deadbeef",
    )
    args.update(kwargs)
    return RecommendationSet(**args)


def _review(items):
    return review.build(
        recommendation=_source(), items=items, unpriced=[], dropped={},
        skipped={}, investable=Decimal("100000.00"),
        charge_reserve=Decimal("300.00"), deployed=Decimal("99000.00"),
        residual_cash=Decimal("1000.00"))


def _frozen(items=None, **kwargs):
    source = kwargs.pop("source", _source())
    return review.as_revision(
        _review(items if items is not None else [_item(1, Action.BUY, "NEW", 9)]),
        source=source, revision=2, now=NOW, **kwargs)


# ----------------------------------------------------------- the lineage ---

def test_the_revision_supersedes_the_set_it_was_priced_from():
    got = _frozen()
    assert got.revision == 2
    assert got.supersedes == "paper_momentum_v1_001:2026-10:r1"
    assert got.set_id == "paper_momentum_v1_001:2026-10:r2"


def test_the_signal_session_is_carried_forward_unchanged():
    """Membership is frozen from it; only the prices moved. Rewriting it would
    claim the strategy ran again."""
    assert _frozen().as_of_session == date(2026, 9, 30)


def test_the_frozen_parameters_carry_forward():
    """A revision that lost the hash would pass the frozen-strategy gate by
    forgetting what it was checked against."""
    got = _frozen()
    assert got.strategy_params_hash == "sha256:abc"
    assert got.code_commit == "deadbeef"


def test_holds_carry_forward():
    """The record of what the strategy chose to KEEP is half the decision."""
    assert _frozen().holds == ["KEPT"]


def test_the_period_and_run_carry_forward():
    got = _frozen()
    assert got.period == "2026-10"
    assert got.rebalance_run_id == "run-1"


# -------------------------------------------------------- what it becomes ---

def test_the_revision_is_approved_not_pending():
    """It exists because a human read it and signed off; `pending` would mean
    nobody had."""
    assert _frozen().status == SetStatus.APPROVED


def test_the_fill_convention_is_the_next_open():
    """What the resizing actually did, stated on the record rather than
    implied by the prices."""
    assert _frozen().fill_convention == "next_open"


def test_the_items_are_the_resized_instructions():
    got = _frozen(items=[_item(1, Action.SELL, "OLD", 5),
                         _item(2, Action.BUY, "NEW", 3)])
    assert [(i.seq, str(i.action), i.symbol, i.quantity) for i in got.items] == [
        (1, "sell", "OLD", 5), (2, "buy", "NEW", 3)]


def test_the_reference_price_is_the_open_it_was_sized_at():
    """Executions compare fills against this, so slippage is measured against
    the price actually approved rather than a stale close."""
    got = _frozen(items=[_item(1, Action.BUY, "NEW", 3, price="224.17")])
    assert got.items[0].reference_price == Decimal("224.17")


def test_a_trim_with_no_counterpart_in_the_source_is_still_an_item():
    """The reason this is a revision at all: a trim has no matching entry or
    exit in the period's recommendation, so it could never be recorded
    against one."""
    got = _frozen(items=[_item(1, Action.SELL, "KEPT", 1)])
    assert [i.symbol for i in got.items] == ["KEPT"]
    assert got.items[0].seq == 1


def test_generated_at_records_when_it_was_frozen():
    assert _frozen().generated_at == NOW


def test_an_empty_basket_produces_a_revision_with_no_items():
    """Not an error: a rebalance where nothing needs trading is a real
    outcome, and the revision records that it was checked."""
    got = _frozen(items=[])
    assert got.items == []
    assert got.status == SetStatus.APPROVED
