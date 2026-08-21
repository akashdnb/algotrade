"""Transaction costs and slippage (PROJECT.md §3).

Costs are the deciding variable in whether any strategy passes the gates, so
this is a first-class component with its own versioned rate card rather than a
hardcoded 0.1% somewhere in the engine.

Three properties of the Indian retail cost structure shape everything downstream:

**Delivery is ~2.8x more expensive than intraday.** STT is 0.1% on *both* sides
for delivery versus 0.025% sell-only for intraday. A swing hold settles as
delivery, so that is the card that applies. On Rs 20,000 the round trip is
Rs 59.84 (~30 bps) against Rs 21.26 (~10.6 bps) intraday.

**DP charges are flat per scrip per sell**, so they scale inversely with
position size: 15.3 bps on Rs 10,000 but 1.5 bps on Rs 1,00,000. This is what
caps position count at Rs 2L -- twenty Rs 10,000 positions pay 15.3 bps in DP
alone per rotation versus 6.1 bps for eight of Rs 25,000.

**Brokerage is min(percentage, cap)**, so cost is not linear in trade value and
cannot be modelled as a single bps figure.

Pure module. The rate card is loaded by the caller and passed in.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

Side = Literal["buy", "sell"]

DEFAULT_CONFIG: Final[Path] = Path("config/costs.yaml")


@dataclass(frozen=True)
class RateCard:
    """Statutory and broker charges for one product type."""

    name: str
    brokerage_pct: float
    brokerage_cap: float | None
    stt_buy_pct: float
    stt_sell_pct: float
    txn_pct: float
    sebi_pct: float
    stamp_buy_pct: float
    gst_pct: float
    dp_per_scrip: float

    @classmethod
    def from_dict(cls, name: str, raw: dict) -> RateCard:
        return cls(
            name=name,
            brokerage_pct=float(raw["brokerage_pct"]),
            brokerage_cap=(
                None if raw.get("brokerage_cap") is None else float(raw["brokerage_cap"])
            ),
            stt_buy_pct=float(raw["stt_buy_pct"]),
            stt_sell_pct=float(raw["stt_sell_pct"]),
            txn_pct=float(raw["txn_pct"]),
            sebi_pct=float(raw["sebi_pct"]),
            stamp_buy_pct=float(raw["stamp_buy_pct"]),
            gst_pct=float(raw["gst_pct"]),
            dp_per_scrip=float(raw["dp_per_scrip"]),
        )


@dataclass(frozen=True)
class SlippageModel:
    """Spread and market-impact assumptions.

    Unlike `RateCard` these are *estimates*, not published rates. They must be
    recalibrated against real fills in Phase 0c (§9); until then, backtest
    slippage is an assumption rather than a measurement.
    """

    spread_bps_by_tier: dict[str, float]
    impact_coefficient: float
    max_participation: float


@dataclass(frozen=True)
class Charges:
    """Itemised charges for a single order."""

    brokerage: float
    stt: float
    txn: float
    sebi: float
    stamp: float
    gst: float
    dp: float

    @property
    def total(self) -> float:
        return (
            self.brokerage + self.stt + self.txn + self.sebi + self.stamp
            + self.gst + self.dp
        )

    def bps_of(self, value: float) -> float:
        return 0.0 if value <= 0 else self.total / value * 10_000


def load_rate_cards(path: str | Path = DEFAULT_CONFIG) -> dict[str, RateCard]:
    """Load every product's rate card from the versioned config."""
    import yaml

    raw = yaml.safe_load(Path(path).read_text())
    return {
        name: RateCard.from_dict(name, raw[name])
        for name in ("delivery", "intraday")
        if name in raw
    }


def load_slippage(path: str | Path = DEFAULT_CONFIG) -> SlippageModel:
    import yaml

    raw = yaml.safe_load(Path(path).read_text())["slippage"]
    return SlippageModel(
        spread_bps_by_tier=dict(raw["spread_bps_by_tier"]),
        impact_coefficient=float(raw["impact_coefficient"]),
        max_participation=float(raw["max_participation"]),
    )


def order_charges(value: float, side: Side, card: RateCard) -> Charges:
    """Statutory and broker charges for one order.

    GST applies only to brokerage, SEBI and exchange charges -- not to STT or
    stamp duty, which are taxes in their own right.

    DP is charged on sells only, flat per scrip regardless of quantity, which is
    why it must be applied per order rather than folded into a bps figure.
    """
    if value <= 0:
        return Charges(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    brokerage = value * card.brokerage_pct
    if card.brokerage_cap is not None:
        brokerage = min(brokerage, card.brokerage_cap)

    buying = side == "buy"
    stt = value * (card.stt_buy_pct if buying else card.stt_sell_pct)
    txn = value * card.txn_pct
    sebi = value * card.sebi_pct
    stamp = value * card.stamp_buy_pct if buying else 0.0
    gst = (brokerage + sebi + txn) * card.gst_pct
    dp = 0.0 if buying else card.dp_per_scrip

    return Charges(brokerage, stt, txn, sebi, stamp, gst, dp)


def round_trip_charges(value: float, card: RateCard) -> Charges:
    """Charges for buying and later selling the same rupee value."""
    buy = order_charges(value, "buy", card)
    sell = order_charges(value, "sell", card)
    return Charges(
        brokerage=buy.brokerage + sell.brokerage,
        stt=buy.stt + sell.stt,
        txn=buy.txn + sell.txn,
        sebi=buy.sebi + sell.sebi,
        stamp=buy.stamp + sell.stamp,
        gst=buy.gst + sell.gst,
        dp=buy.dp + sell.dp,
    )


def slippage_bps(
    value: float,
    adtv: float,
    daily_vol: float,
    tier: str,
    model: SlippageModel,
) -> float:
    """One-way slippage in bps: half the spread plus market impact.

    Impact follows the square-root law, `k * vol * sqrt(participation)`, which is
    the standard empirical form. Participation is the order's share of average
    daily turnover.

    Args:
        daily_vol: Daily return volatility as a fraction (0.02 = 2%).
        tier: Liquidity tier key into the spread table.
    """
    half_spread = model.spread_bps_by_tier.get(tier, model.spread_bps_by_tier["small"]) / 2
    if adtv <= 0 or value <= 0:
        return half_spread
    participation = min(value / adtv, 1.0)
    impact = model.impact_coefficient * daily_vol * math.sqrt(participation)
    return half_spread + impact * 10_000


def max_tradeable_value(adtv: float, model: SlippageModel) -> float:
    """Largest order the participation cap permits against this turnover.

    Backtests that ignore this fill orders no real participant could, which is
    the single easiest way to manufacture returns in illiquid names.
    """
    return max(adtv, 0.0) * model.max_participation


def breakeven_bps(
    value: float, card: RateCard, slippage_one_way_bps: float = 0.0
) -> float:
    """Gross move needed to cover a round trip, in bps.

    The actionable number. On delivery at Rs 20,000 this is ~30 bps of charges
    before any slippage -- so a strategy whose average gross edge is under that
    is dead regardless of how good its signal looks.
    """
    charges = round_trip_charges(value, card).bps_of(value)
    return charges + 2 * slippage_one_way_bps


def dp_bps(value: float, card: RateCard) -> float:
    """DP charge as bps of position value -- the position-sizing constraint.

    Flat per scrip, so this falls as positions grow. At Rs 2L across twenty
    Rs 10,000 positions DP alone costs 15.3 bps per rotation; across eight
    Rs 25,000 positions it costs 6.1 bps.
    """
    return 0.0 if value <= 0 else card.dp_per_scrip / value * 10_000
