"""Corporate actions applied to the book (domain + the fold in HoldingsService).

The bug these exist for: TDPOWERSYS split 2:1 on 2026-08-24, the book kept 4
shares at Rs 1,507.50 against a market price near Rs 753, and reported -51.9%
on a position that was roughly flat. Four clean reconciliations ran over it,
because every one of them compared our data against our own data.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from fractions import Fraction

import pytest

from algotrade.domain import corporate_actions as ca
from algotrade.domain.models import Action, Execution, Holding, Lot
from algotrade.services.holdings import HoldingsService

UTC = timezone.utc


def holding(*lots: Lot, symbol: str = "X") -> Holding:
    return Holding(portfolio_id="paper_momentum_v1_001", symbol=symbol,
                   lots=list(lots))


def lot(qty: int, price: str, *, on: date = date(2026, 1, 1),
        lot_id: str = "L", charges: str = "0") -> Lot:
    return Lot(lot_id=lot_id, acquired_on=on, quantity=qty,
               price=Decimal(price), charges=Decimal(charges))


def action(ratio: Fraction, *, on: date = date(2026, 6, 1), symbol: str = "X",
           kind: str = "split") -> ca.CorporateAction:
    return ca.CorporateAction(action_id=f"a:{symbol}:{on}", symbol=symbol,
                              ex_date=on, ratio=ratio, kind=kind)


# ------------------------------------------------------- the real case ------


def test_the_tdpowersys_split_doubles_the_shares_and_halves_the_price():
    h = holding(lot(4, "1507.50", on=date(2026, 8, 23), charges="7.16"),
                symbol="TDPOWERSYS")
    new, applied = ca.apply_to_holding(
        h, action(Fraction(2), on=date(2026, 8, 24), symbol="TDPOWERSYS"))

    assert new.quantity == 8
    assert new.lots[0].price == Decimal("753.7500")
    # The whole point: the position is worth what it was worth.
    assert applied.cost_before == applied.cost_after == Decimal("6037.16")
    assert applied.cost_preserved


def test_a_split_does_not_restart_the_holding_period():
    """Scaling is not an acquisition. Resetting `acquired_on` would reprice
    long-term gains at the short-term rate."""
    bought = date(2026, 8, 23)
    h = holding(lot(4, "1507.50", on=bought), symbol="T")
    new, _ = ca.apply_to_holding(h, action(Fraction(2), symbol="T"))
    assert new.lots[0].acquired_on == bought


def test_charges_are_not_scaled():
    """They were paid in rupees and no share-count change refunds any."""
    h = holding(lot(4, "1000", charges="7.16"))
    new, _ = ca.apply_to_holding(h, action(Fraction(2)))
    assert new.lots[0].charges == Decimal("7.16")


def test_lot_identity_survives():
    h = holding(lot(4, "1000", lot_id="set:26:1"))
    new, _ = ca.apply_to_holding(h, action(Fraction(2)))
    assert new.lots[0].lot_id == "set:26:1"


# ------------------------------------- bonuses and fractional entitlement ---


def test_bonus_entitlement_is_computed_on_the_total_not_per_lot():
    """Two lots of 2 under a 1:3 bonus.

    Per lot: floor(2 x 4/3) = 2 twice -> 4 shares.  The registrar computes on
    the whole demat holding: floor(4 x 4/3) = 5.  Getting this wrong loses a
    real share.
    """
    h = holding(lot(2, "100", lot_id="A", on=date(2026, 1, 1)),
                lot(2, "100", lot_id="B", on=date(2026, 2, 1)))
    new, applied = ca.apply_to_holding(h, action(Fraction(4, 3), kind="bonus"))

    assert applied.quantity_after == 5
    assert new.quantity == 5


def test_the_leftover_share_goes_to_the_largest_remainder_and_is_stable():
    h = holding(lot(2, "100", lot_id="A", on=date(2026, 1, 1)),
                lot(2, "100", lot_id="B", on=date(2026, 2, 1)))
    first, _ = ca.apply_to_holding(h, action(Fraction(4, 3), kind="bonus"))
    again, _ = ca.apply_to_holding(h, action(Fraction(4, 3), kind="bonus"))
    # Deterministic: a rebuild has to reproduce the allocation exactly.
    assert [(l.lot_id, l.quantity) for l in first.lots] == \
           [(l.lot_id, l.quantity) for l in again.lots]
    assert sum(l.quantity for l in first.lots) == 5


def test_cost_is_preserved_per_lot_even_when_the_remainder_is_uneven():
    h = holding(lot(2, "100", lot_id="A", on=date(2026, 1, 1)),
                lot(2, "100", lot_id="B", on=date(2026, 2, 1)))
    new, applied = ca.apply_to_holding(h, action(Fraction(4, 3), kind="bonus"))
    for before, after in zip(h.lots, new.lots):
        assert before.price * before.quantity == pytest.approx(
            after.price * after.quantity, abs=Decimal("0.01")
        )
    assert applied.cost_preserved


def test_the_dropped_fraction_is_reported_rather_than_absorbed():
    """Registrars sell fractional entitlements and pay cash in lieu. That is
    not modelled, so it must not vanish silently."""
    h = holding(lot(4, "100"))
    _, applied = ca.apply_to_holding(h, action(Fraction(4, 3), kind="bonus"))
    assert applied.fractional_shares_dropped == Fraction(1, 3)


# ------------------------------------------------ precision and refusal -----


def test_an_inexact_price_keeps_the_cost_basis_exact():
    """1,507.50 across 7 shares is 215.3571..., which paise cannot hold.

    At two places the basis drifts past the 0.01 that `verify` compares
    stored against derived at, so a correct book would report itself corrupt.
    """
    h = holding(lot(1, "1507.50"))
    new, applied = ca.apply_to_holding(h, action(Fraction(7)))
    assert new.lots[0].price == Decimal("215.3571")
    assert applied.cost_preserved


def test_a_reverse_split_is_refused_rather_than_applied_backwards():
    with pytest.raises(ca.InapplicableAction, match="below 1"):
        action(Fraction(1, 2))


def test_an_action_for_another_symbol_is_refused():
    h = holding(lot(4, "100"), symbol="AAA")
    with pytest.raises(ca.InapplicableAction, match="is for BBB"):
        ca.apply_to_holding(h, action(Fraction(2), symbol="BBB"))


def test_ratio_from_factor_recovers_the_exact_rational():
    """`core.actions` returns floats. Flooring 1.3333333333333333 where 4/3
    was meant destroys a share."""
    assert ca.ratio_from_factor(2.0) == Fraction(2)
    assert ca.ratio_from_factor(1.0 + 1 / 3) == Fraction(4, 3)
    assert ca.ratio_from_factor(10.0) == Fraction(10)


# ------------------------------------------------- the fold in derive() -----


def execution(symbol: str, qty: int, price: str, on: date,
              action_: Action = Action.BUY, seq: int = 1) -> Execution:
    return Execution(
        execution_id=f"{symbol}:{on}:{seq}", portfolio_id="paper_momentum_v1_001",
        set_id="s1", item_seq=seq, symbol=symbol, action=action_,
        quantity=qty, price=Decimal(price),
        executed_at=datetime(on.year, on.month, on.day, 10, 0, tzinfo=UTC),
    )


def service() -> HoldingsService:
    return HoldingsService(executions=None, holdings=None)


def test_derive_scales_a_position_held_across_the_ex_date():
    result = service().derive(
        [execution("T", 4, "1507.50", date(2026, 8, 23))],
        portfolio_id="paper_momentum_v1_001",
        actions=[action(Fraction(2), on=date(2026, 8, 24), symbol="T")],
    )
    assert result.holdings[0].quantity == 8
    assert result.actions_replayed == 1
    assert not result.anomalies


def test_shares_bought_ON_the_ex_date_are_not_scaled():
    """They were bought at the already-adjusted price. Ordering alone has to
    settle this -- an action sorts before every execution on its ex-date."""
    result = service().derive(
        [execution("T", 4, "753.75", date(2026, 8, 24))],
        portfolio_id="paper_momentum_v1_001",
        actions=[action(Fraction(2), on=date(2026, 8, 24), symbol="T")],
    )
    assert result.holdings[0].quantity == 4
    assert result.actions_replayed == 0


def test_a_buy_before_and_a_buy_on_the_ex_date_are_treated_differently():
    result = service().derive(
        [execution("T", 4, "1507.50", date(2026, 8, 23), seq=1),
         execution("T", 2, "753.75", date(2026, 8, 24), seq=2)],
        portfolio_id="paper_momentum_v1_001",
        actions=[action(Fraction(2), on=date(2026, 8, 24), symbol="T")],
    )
    # 4 -> 8 scaled, plus 2 bought after the action = 10.
    assert result.holdings[0].quantity == 10


def test_an_action_on_a_symbol_not_held_is_ignored_quietly():
    result = service().derive(
        [execution("T", 4, "100", date(2026, 8, 23))],
        portfolio_id="paper_momentum_v1_001",
        actions=[action(Fraction(2), on=date(2026, 8, 24), symbol="OTHER")],
    )
    assert result.holdings[0].quantity == 4
    assert not result.anomalies


def test_a_position_sold_before_the_ex_date_is_not_scaled():
    result = service().derive(
        [execution("T", 4, "100", date(2026, 8, 20), seq=1),
         execution("T", 4, "110", date(2026, 8, 21), Action.SELL, seq=2)],
        portfolio_id="paper_momentum_v1_001",
        actions=[action(Fraction(2), on=date(2026, 8, 24), symbol="T")],
    )
    assert result.holdings == []
    assert result.actions_replayed == 0


def test_replaying_twice_gives_the_same_book():
    """The fold has to stay a pure function of stored events -- `daily.yml`
    rebuilds from scratch every session."""
    args = dict(
        portfolio_id="paper_momentum_v1_001",
        actions=[action(Fraction(2), on=date(2026, 8, 24), symbol="T")],
    )
    execs = [execution("T", 4, "1507.50", date(2026, 8, 23))]
    first = service().derive(execs, **args)
    second = service().derive(execs, **args)
    assert [(h.symbol, h.quantity, h.cost_basis) for h in first.holdings] == \
           [(h.symbol, h.quantity, h.cost_basis) for h in second.holdings]


def test_derive_without_actions_behaves_exactly_as_before():
    """Every existing call site passes no actions; none of them may change."""
    execs = [execution("T", 4, "1507.50", date(2026, 8, 23))]
    assert service().derive(execs, portfolio_id="p").holdings[0].quantity == 4
