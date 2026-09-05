"""Re-sizing a decided membership at the execution open (design §6).

This is option B: the frozen artifact is the **weight**, the quantity is a
function of the execution price. `backtest.engine` computes weights at close(D)
and fills at `open(D+1)`, so sizing on close(D) prices applies the wrong price
vector.

The two properties that matter:

* `resize` must not re-decide membership -- the signal is frozen;
* `resize` and `decide` must produce the same quantities from the same prices,
  or the review screen and the recommendation would disagree about the book.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from algotrade.domain.models import Holding, Lot
from algotrade.services.rebalance import RebalanceService

AS_OF = date(2026, 10, 1)


def _service(**kwargs) -> RebalanceService:
    args = dict(entry=3, exit_=6, charge_reserve_bps=30)
    args.update(kwargs)
    return RebalanceService(**args)


def _holding(symbol: str, quantity: int, price="100.00") -> Holding:
    return Holding(portfolio_id="p", symbol=symbol,
                   lots=[Lot(lot_id=f"{symbol}:1", quantity=quantity,
                             price=Decimal(price),
                             acquired_on=date(2026, 9, 1))])


def _prices(**kwargs) -> dict[str, Decimal]:
    return {k: Decimal(v) for k, v in kwargs.items()}


# ------------------------------------------------------------- membership ---

def test_membership_is_taken_as_given_not_recomputed():
    """The signal is frozen at close(D). Only the prices move."""
    got = _service().resize(
        target=["AAA", "BBB"], holdings=[], cash=Decimal("100000.00"),
        prices=_prices(AAA="100.00", BBB="200.00", CCC="50.00"),
        ranks={}, as_of=AS_OF)
    assert set(got.target_quantities) == {"AAA", "BBB"}
    assert "CCC" not in got.target_quantities


def test_entering_and_leaving_are_derived_from_what_is_held():
    got = _service().resize(
        target=["AAA", "BBB"], holdings=[_holding("CCC", 10)],
        cash=Decimal("100000.00"),
        prices=_prices(AAA="100.00", BBB="200.00", CCC="50.00"),
        ranks={}, as_of=AS_OF)
    assert got.entering == ["AAA", "BBB"]
    assert got.leaving == ["CCC"]
    assert got.holding == []


def test_a_held_name_that_stays_in_target_is_holding_not_entering():
    got = _service().resize(
        target=["AAA"], holdings=[_holding("AAA", 10)],
        cash=Decimal("10000.00"), prices=_prices(AAA="100.00"),
        ranks={}, as_of=AS_OF)
    assert got.holding == ["AAA"]
    assert got.entering == []


# ----------------------------------------------------------------- sizing ---

def test_quantities_follow_the_prices_they_are_given():
    """The whole point of B: a different price vector, a different count."""
    cheap = _service().resize(
        target=["AAA"], holdings=[], cash=Decimal("100000.00"),
        prices=_prices(AAA="100.00"), ranks={}, as_of=AS_OF)
    dear = _service().resize(
        target=["AAA"], holdings=[], cash=Decimal("100000.00"),
        prices=_prices(AAA="200.00"), ranks={}, as_of=AS_OF)
    assert cheap.target_quantities["AAA"] > dear.target_quantities["AAA"]


def test_a_gapped_up_price_buys_fewer_shares_for_the_same_weight():
    """Frozen QUANTITIES would have spent more rupees on the gap; frozen
    weights hold the target weight instead, which is what the engine does."""
    flat = _service().resize(
        target=["AAA"], holdings=[], cash=Decimal("100000.00"),
        prices=_prices(AAA="100.00"), ranks={}, as_of=AS_OF)
    gapped = _service().resize(
        target=["AAA"], holdings=[], cash=Decimal("100000.00"),
        prices=_prices(AAA="102.00"), ranks={}, as_of=AS_OF)
    flat_value = flat.target_quantities["AAA"] * Decimal("100.00")
    gap_value = gapped.target_quantities["AAA"] * Decimal("102.00")
    assert abs(flat_value - gap_value) < Decimal("200.00")


def test_held_stock_value_counts_toward_what_is_investable():
    """A rebalance redeploys the whole book, not just idle cash."""
    with_book = _service().resize(
        target=["AAA"], holdings=[_holding("BBB", 100)],
        cash=Decimal("0.00"), prices=_prices(AAA="100.00", BBB="100.00"),
        ranks={}, as_of=AS_OF)
    assert with_book.target_quantities.get("AAA", 0) > 0


def test_an_unpriced_target_is_dropped_with_a_reason():
    got = _service().resize(
        target=["AAA", "BBB"], holdings=[], cash=Decimal("100000.00"),
        prices=_prices(AAA="100.00"), ranks={}, as_of=AS_OF)
    assert "BBB" in got.dropped
    assert "no price" in got.dropped["BBB"]


def test_a_charge_reserve_is_held_back():
    got = _service(charge_reserve_bps=30).resize(
        target=["AAA"], holdings=[], cash=Decimal("100000.00"),
        prices=_prices(AAA="100.00"), ranks={}, as_of=AS_OF)
    assert got.charge_reserve > 0
    assert got.investable < Decimal("100000.00")


def test_deployed_plus_residual_equals_investable():
    got = _service().resize(
        target=["AAA", "BBB"], holdings=[], cash=Decimal("100000.00"),
        prices=_prices(AAA="333.33", BBB="777.77"), ranks={}, as_of=AS_OF)
    assert got.deployed + got.residual_cash == got.investable


# ------------------------------------------------- agreement with decide ---

def test_resize_matches_decide_when_given_the_same_prices_and_membership():
    """One arithmetic, two entry points. If these diverge, the review screen
    and the recommendation disagree about what the book should be -- and both
    look authoritative."""
    from algotrade.domain.models import SignalRow, SignalSet

    signals = SignalSet(
        strategy_version="v1", date=AS_OF,
        rows=[SignalRow(symbol=s, rank=i + 1, feature=1.0)
              for i, s in enumerate(["AAA", "BBB", "CCC", "DDD"])],
    )
    holdings = [_holding("AAA", 10)]
    cash = Decimal("100000.00")
    prices = _prices(AAA="100.00", BBB="200.00", CCC="50.00", DDD="75.00")

    service = _service()
    decided = service.decide(holdings=holdings, signals=signals, cash=cash,
                             prices=prices, as_of=AS_OF)
    resized = service.resize(target=decided.target_symbols, holdings=holdings,
                             cash=cash, prices=prices, ranks=decided.ranks,
                             as_of=AS_OF)

    assert resized.target_quantities == decided.target_quantities
    assert resized.investable == decided.investable
    assert resized.charge_reserve == decided.charge_reserve
    assert resized.deployed == decided.deployed
