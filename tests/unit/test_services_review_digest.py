"""The fingerprint that stops a basket moving between reading and clicking.

Accept re-derives the basket server-side rather than trusting numbers posted
from a form -- a client that could name its own quantities could name any
quantity. But re-deriving reopens a gap: the feed moves, so the basket computed
at click time may not be the one that was rendered.

The digest closes it. Equal means what was read is what is frozen; different
means it moved and the accept is refused. Neither the client dictating values
nor the server silently substituting them.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from algotrade.domain.models import Action, Item, RecommendationSet
from algotrade.services import review


def _item(seq, action, symbol, quantity, price="100.00") -> Item:
    return Item(seq=seq, action=action, symbol=symbol, quantity=quantity,
                reference_price=Decimal(price),
                estimated_charges=Decimal("10.00"))


def _set() -> RecommendationSet:
    return RecommendationSet(
        set_id="p:2026-10:r1", portfolio_id="paper_momentum_v1_001",
        rebalance_run_id="r", period="2026-10",
        as_of_session=date(2026, 9, 30))


def _review(items):
    return review.build(
        recommendation=_set(), items=items, unpriced=[], dropped={},
        skipped={}, investable=Decimal("100000.00"),
        charge_reserve=Decimal("300.00"), deployed=Decimal("99000.00"),
        residual_cash=Decimal("1000.00"))


BASE = [_item(1, Action.SELL, "OLD", 5), _item(2, Action.BUY, "NEW", 3)]


def test_the_same_basket_digests_the_same():
    assert review.digest(_review(BASE)) == review.digest(_review(BASE))


def test_a_changed_quantity_changes_the_digest():
    """The case this exists for: a gap moves the share count between the page
    being drawn and the button being pressed."""
    moved = [_item(1, Action.SELL, "OLD", 5), _item(2, Action.BUY, "NEW", 4)]
    assert review.digest(_review(moved)) != review.digest(_review(BASE))


def test_a_changed_price_changes_the_digest():
    """The basket is a statement about these prices, so a different price is
    a different basket even at the same quantities."""
    moved = [_item(1, Action.SELL, "OLD", 5),
             _item(2, Action.BUY, "NEW", 3, price="101.00")]
    assert review.digest(_review(moved)) != review.digest(_review(BASE))


def test_a_changed_symbol_changes_the_digest():
    moved = [_item(1, Action.SELL, "OLD", 5), _item(2, Action.BUY, "OTHER", 3)]
    assert review.digest(_review(moved)) != review.digest(_review(BASE))


def test_a_changed_side_changes_the_digest():
    """Same name, same size, opposite direction -- the most dangerous
    collision a weaker fingerprint could allow."""
    moved = [_item(1, Action.BUY, "OLD", 5), _item(2, Action.BUY, "NEW", 3)]
    assert review.digest(_review(moved)) != review.digest(_review(BASE))


def test_an_extra_instruction_changes_the_digest():
    more = BASE + [_item(3, Action.BUY, "THIRD", 1)]
    assert review.digest(_review(more)) != review.digest(_review(BASE))


def test_a_missing_instruction_changes_the_digest():
    assert review.digest(_review(BASE[:1])) != review.digest(_review(BASE))


def test_an_empty_basket_has_a_stable_digest():
    assert review.digest(_review([])) == review.digest(_review([]))


def test_the_digest_is_short_enough_for_a_form_field():
    assert len(review.digest(_review(BASE))) == 16
