"""Domain entities. Dataclasses plus the invariants that belong to them.

Statuses are `StrEnum` so they compare equal to the plain strings stored in
Mongo, which keeps the repository layer free of translation tables while still
giving the domain a closed set to reason about.

Money is `Decimal` throughout (see `money.py`); the repository layer converts
to `Decimal128` at the boundary and nowhere else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Final

from algotrade.domain import money
from algotrade.domain.ids import Mode

#: A holding crosses from short-term to long-term at 12 months for listed
#: equity (STCG 20% -> LTCG 12.5%). The classification is by ACQUISITION date
#: per lot, which is why lots exist at all.
LONG_TERM_DAYS: Final[int] = 365


class PortfolioStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    CLOSED = "closed"


class ExecutionMode(StrEnum):
    MANUAL = "manual"        # you place the orders; fills recorded after the fact
    AUTO_PAPER = "auto_paper"  # simulated fill at the next open


class SetStatus(StrEnum):
    """Lifecycle of a recommendation set.

    APPROVED/REJECTED are the human-in-the-loop gate (§9): the system
    recommends, you decide, and the decision is recorded before any fill is.
    EXPIRED and SUPERSEDED are retained from the original design and are not
    redundant with REJECTED -- expiry is the passage of time making a signal
    stale, superseding is a NEW revision replacing this one, and rejection is
    a deliberate human "no". Collapsing them would lose why a set was closed.
    """

    PENDING = "pending"
    NOTIFIED = "notified"
    APPROVED = "approved"                    # human accepted; awaiting fills
    REJECTED = "rejected"                    # human declined the whole set
    PARTIALLY_EXECUTED = "partially_executed"
    COMPLETED = "completed"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


class ItemStatus(StrEnum):
    PENDING = "pending"
    EXECUTED = "executed"
    PARTIAL = "partial"
    SKIPPED = "skipped"
    EXPIRED = "expired"


class Action(StrEnum):
    BUY = "buy"
    SELL = "sell"


class TaxStatus(StrEnum):
    SHORT_TERM = "short_term"
    LONG_TERM = "long_term"


# --------------------------------------------------------------- portfolio ---


@dataclass(frozen=True, slots=True)
class StrategyParams:
    """The frozen signal definition. Hashed; a change forks a portfolio (§3.3)."""

    version: str
    lookback: int
    skip: int
    entry: int
    exit: int
    cadence: str
    universe_size: int
    min_turnover: Decimal
    min_coverage: float


@dataclass(slots=True)
class Portfolio:
    portfolio_id: str
    mode: Mode
    strategy: str
    strategy_version: str
    strategy_params_hash: str
    status: PortfolioStatus = PortfolioStatus.ACTIVE
    currency: str = "INR"

    initial_capital: Decimal = money.ZERO
    monthly_contribution: Decimal = money.ZERO
    contribution_day: int = 1
    cash: Decimal = money.ZERO
    total_contributed: Decimal = money.ZERO

    #: Unit accounting makes performance contribution-neutral: a ₹20k deposit
    #: buys units at the prevailing unit NAV and therefore does not move it.
    #: Without this, every contribution looks like a gain.
    units: Decimal = money.ZERO

    #: The NIFTYBEES half: valued for reporting, never traded (see BenchmarkSleeve).
    benchmark_sleeve: "BenchmarkSleeve | None" = None
    #: Human-facing name, since the id is machine-strict. e.g. "50/50", "100% momentum".
    label: str = ""

    broker: str = "zerodha"
    execution_mode: ExecutionMode = ExecutionMode.MANUAL
    cost_model: str = ""
    account_note: str = ""
    created_at: datetime | None = None
    closed_at: datetime | None = None
    created_by_commit: str = ""

    @property
    def is_active(self) -> bool:
        return self.status == PortfolioStatus.ACTIVE


# ----------------------------------------------------------------- holdings ---


@dataclass(frozen=True, slots=True)
class Lot:
    """One acquisition. The unit of tax accounting.

    Charges are carried per lot because they are part of the cost basis, and
    the flat ₹15.34 DP charge makes cost non-linear in trade size -- averaging
    it across a position would misstate the basis of every lot in it.
    """

    lot_id: str
    acquired_on: date
    quantity: int
    price: Decimal
    charges: Decimal = money.ZERO
    source_set_id: str = ""

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError(f"lot {self.lot_id}: quantity must be > 0, got {self.quantity}")
        if self.price < 0:
            raise ValueError(f"lot {self.lot_id}: price must be >= 0, got {self.price}")

    @property
    def cost_basis(self) -> Decimal:
        """What was actually paid, charges included."""
        return money.rupees(self.price * self.quantity + self.charges)

    def days_held(self, as_of: date) -> int:
        return (as_of - self.acquired_on).days

    def tax_status(self, as_of: date) -> TaxStatus:
        """Long-term strictly AFTER 365 days.

        Exactly 365 days is still short-term: the statutory test is "held for
        more than 12 months", and rounding that boundary the generous way
        understates tax owed.
        """
        return (
            TaxStatus.LONG_TERM
            if self.days_held(as_of) > LONG_TERM_DAYS
            else TaxStatus.SHORT_TERM
        )


@dataclass(slots=True)
class Holding:
    portfolio_id: str
    symbol: str
    isin: str = ""
    lots: list[Lot] = field(default_factory=list)
    #: LIFETIME realised gain, not year-to-date.
    #:
    #: It was called `realised_pnl_ytd` until 2026-09-01 and never reset: the
    #: holdings rebuild replays the ENTIRE execution history and accumulates
    #: into it, with no year boundary anywhere. The name asserted an annual
    #: reset that did not exist, and `value_portfolio` sums it into each
    #: snapshot's `realised_pnl`, so the snapshot inherited the same claim.
    #:
    #: Renamed rather than made per-year: holdings are derived from executions,
    #: so any yearly figure is a QUERY over them, not a stored field that must
    #: be kept true. `dashboard/app/repositories/executions.py` has the Indian
    #: financial-year bounds for exactly that.
    realised_pnl: Decimal = money.ZERO
    updated_at: datetime | None = None

    @property
    def quantity(self) -> int:
        """Derived from lots, never stored independently.

        A separately-stored quantity is a second source of truth that can drift
        from the lots -- and reconciling them after the fact is impossible once
        it has.
        """
        return sum(lot.quantity for lot in self.lots)

    @property
    def cost_basis(self) -> Decimal:
        return money.sum_rupees(lot.cost_basis for lot in self.lots)

    @property
    def avg_cost(self) -> Decimal:
        qty = self.quantity
        if qty == 0:
            return money.ZERO
        return money.rupees(self.cost_basis / qty)

    @property
    def first_acquired(self) -> date | None:
        return min((lot.acquired_on for lot in self.lots), default=None)

    @property
    def is_empty(self) -> bool:
        return self.quantity == 0

    def value_at(self, price: Decimal) -> Decimal:
        return money.rupees(price * self.quantity)

    def unrealised_pnl(self, price: Decimal) -> Decimal:
        return money.rupees(self.value_at(price) - self.cost_basis)

    def tax_status(self, as_of: date) -> TaxStatus:
        """The position's status is its OLDEST lot's -- because FIFO means that
        lot is the one a sale would realise first."""
        if not self.lots:
            return TaxStatus.SHORT_TERM
        oldest = min(self.lots, key=lambda lot: lot.acquired_on)
        return oldest.tax_status(as_of)


# ---------------------------------------------------------- recommendations ---


@dataclass(slots=True)
class Item:
    """One instruction. Ordered by `seq`; sells always precede buys (§5.5)."""

    seq: int
    action: Action
    symbol: str
    quantity: int
    reference_price: Decimal
    isin: str = ""
    reason: str = ""
    estimated_charges: Decimal = money.ZERO

    status: ItemStatus = ItemStatus.PENDING
    executed_quantity: int = 0
    executed_price: Decimal | None = None
    executed_charges: Decimal = money.ZERO
    executed_at: datetime | None = None
    broker_order_id: str = ""
    skip_reason: str = ""

    @property
    def estimated_value(self) -> Decimal:
        return money.rupees(self.reference_price * self.quantity)

    @property
    def executed_value(self) -> Decimal:
        if self.executed_price is None:
            return money.ZERO
        return money.rupees(self.executed_price * self.executed_quantity)

    @property
    def is_open(self) -> bool:
        """Still actionable -- neither done nor deliberately abandoned."""
        return self.status in (ItemStatus.PENDING, ItemStatus.PARTIAL)

    @property
    def slippage_bps(self) -> float | None:
        """Signed, in the direction that costs money.

        Negative is unfavourable for BOTH sides: a sell filled below reference
        and a buy filled above it are each a loss. Reporting raw price change
        would make a bad buy look positive, which is exactly the number you do
        not want on a dashboard.
        """
        if self.executed_price is None or self.reference_price == 0:
            return None
        change = float((self.executed_price - self.reference_price) / self.reference_price)
        signed = change if self.action == Action.SELL else -change
        return signed * 10_000


@dataclass(slots=True)
class RecommendationSet:
    set_id: str
    portfolio_id: str
    rebalance_run_id: str
    period: str
    as_of_session: date
    items: list[Item] = field(default_factory=list)
    #: Symbols retained through the rebalance. Informational -- deliberately
    #: NOT Items: an Item is an instruction to trade, and a zero-delta "HOLD"
    #: item would be counted in turnover, charged for, and shown as
    #: outstanding work. The rank buffer exists so these are NOT traded.
    holds: list[str] = field(default_factory=list)
    revision: int = 1
    supersedes: str | None = None
    status: SetStatus = SetStatus.PENDING
    generated_at: datetime | None = None
    expires_on: date | None = None
    reference_nav: Decimal = money.ZERO
    reference_cash: Decimal = money.ZERO
    fill_convention: str = "next_open"
    code_commit: str = ""
    strategy_params_hash: str = ""

    @property
    def estimated_total_charges(self) -> Decimal:
        return money.sum_rupees(i.estimated_charges for i in self.items)

    @property
    def open_items(self) -> list[Item]:
        return [i for i in self.items if i.is_open]

    def counts(self) -> dict[str, int]:
        return {
            "items": len(self.items),
            "executed": sum(1 for i in self.items if i.status == ItemStatus.EXECUTED),
            "partial": sum(1 for i in self.items if i.status == ItemStatus.PARTIAL),
            "skipped": sum(1 for i in self.items if i.status == ItemStatus.SKIPPED),
            "pending": sum(1 for i in self.items if i.status == ItemStatus.PENDING),
            "expired": sum(1 for i in self.items if i.status == ItemStatus.EXPIRED),
        }

    def derive_status(self) -> SetStatus:
        """The set's status as implied by its items.

        Terminal statuses (`SUPERSEDED`, `EXPIRED`) are sticky -- they are
        decisions about the SET, not summaries of its items, and recomputing
        them from item state would silently resurrect a superseded set.
        """
        # Decisions about the SET, not summaries of its items. Recomputing
        # them from item state would resurrect a set that was deliberately
        # retired, or silently un-reject one you declined.
        if self.status in (SetStatus.SUPERSEDED, SetStatus.EXPIRED,
                           SetStatus.REJECTED):
            return self.status
        if not self.items:
            return SetStatus.COMPLETED
        c = self.counts()
        settled = c["executed"] + c["skipped"] + c["expired"]
        if settled == c["items"]:
            return SetStatus.COMPLETED
        if c["executed"] or c["partial"] or c["skipped"]:
            return SetStatus.PARTIALLY_EXECUTED
        return self.status  # PENDING or NOTIFIED -- untouched so far

    def realised_slippage_bps(self) -> float | None:
        """Value-weighted across executed legs, so a large fill dominates a
        small one. Unweighted averaging would let a ₹500 trade offset a ₹50k one."""
        legs = [
            (i.executed_value, i.slippage_bps)
            for i in self.items
            if i.executed_quantity > 0 and i.slippage_bps is not None
        ]
        total = money.sum_rupees(v for v, _ in legs)
        if not legs or total == 0:
            return None
        return sum(float(v) * bps for v, bps in legs) / float(total)


# ---------------------------------------------------------------- snapshots ---


# --------------------------------------------------------------- executions ---


@dataclass(frozen=True, slots=True)
class Execution:
    """One actual fill. Append-only, and the authoritative record of a trade.

    Separate from `Item` (the instruction) because the two have genuinely
    different cardinality and lifetimes:

    * **A single instruction can be filled by several orders** across days.
      `Item.executed_quantity` cannot represent "300 at 2402, then 200 at 2409".
    * **Tax filing queries executions, not plans.** "Every sell in FY 2026-27"
      is a `find` here; against embedded items it is an `$unwind`.

    `Item`'s execution fields survive as a maintained rollup for display, but
    this collection is the truth -- see `reconcile_item_rollup`.
    """

    execution_id: str
    portfolio_id: str
    set_id: str
    item_seq: int
    symbol: str
    action: Action
    quantity: int
    price: Decimal
    executed_at: datetime
    charges: Decimal = money.ZERO
    broker_order_id: str = ""
    #: Denormalised from the instruction so slippage needs no join.
    reference_price: Decimal = money.ZERO
    isin: str = ""

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError(f"execution {self.execution_id}: quantity must be > 0")
        if self.price <= 0:
            raise ValueError(f"execution {self.execution_id}: price must be > 0")

    @property
    def value(self) -> Decimal:
        return money.rupees(self.price * self.quantity)

    @property
    def net_value(self) -> Decimal:
        """Cash impact: a buy costs value + charges, a sell returns value - charges."""
        if self.action == Action.BUY:
            return money.rupees(self.value + self.charges)
        return money.rupees(self.value - self.charges)

    @property
    def slippage_bps(self) -> float | None:
        """Signed so that negative always means it cost money -- for both sides."""
        if self.reference_price <= 0:
            return None
        change = float((self.price - self.reference_price) / self.reference_price)
        signed = change if self.action == Action.SELL else -change
        return signed * 10_000


def reconcile_item_rollup(item: Item, executions: list[Execution]) -> list[str]:
    """Discrepancies between an item's rollup and its executions.

    The rollup exists for display and cheap queries, which means it can drift
    from the truth. This project's history (four audits, ~55 defects) argues
    for a mechanical check rather than trusting the two to stay in step.
    """
    mine = [e for e in executions if e.item_seq == item.seq]
    problems: list[str] = []

    total_qty = sum(e.quantity for e in mine)
    if total_qty != item.executed_quantity:
        problems.append(
            f"item {item.seq}: rollup says {item.executed_quantity} filled, "
            f"executions total {total_qty}"
        )
    if total_qty > item.quantity:
        problems.append(
            f"item {item.seq}: filled {total_qty} against an instruction for "
            f"{item.quantity}"
        )
    if mine and item.executed_price is not None:
        value = money.sum_rupees(e.value for e in mine)
        implied = money.rupees(value / total_qty) if total_qty else money.ZERO
        if abs(implied - item.executed_price) > Decimal("0.01"):
            problems.append(
                f"item {item.seq}: rollup price {item.executed_price} != "
                f"volume-weighted {implied}"
            )
    return problems


# ---------------------------------------------------------------- signals ---


@dataclass(frozen=True, slots=True)
class SignalRow:
    """One ranked name on one session.

    `market_cap` is deliberately absent: NSE's `sec_bhavdata_full` carries no
    shares-outstanding figure, so a market cap here would have to be fabricated
    or fetched from a source this project has not audited. `adtv` (traded
    value) is the liquidity measure the strategy actually uses (§10.4).
    """

    rank: int
    symbol: str
    feature: float
    close: Decimal | None = None
    adtv: Decimal | None = None
    isin: str = ""


@dataclass(slots=True)
class SignalSet:
    """The ranking for one session.

    Keyed by (strategy_version, date) rather than by portfolio: the ranking is
    a property of the strategy and the market, so every portfolio on the same
    strategy version shares one document instead of storing 30 identical rows
    each.
    """

    strategy_version: str
    date: date
    rows: list[SignalRow] = field(default_factory=list)
    universe_size: int = 0
    panel_manifest_hash: str = ""
    computed_at: datetime | None = None

    @property
    def signal_id(self) -> str:
        return f"{self.strategy_version}:{self.date.isoformat()}"

    def top(self, n: int) -> list[SignalRow]:
        return sorted(self.rows, key=lambda r: r.rank)[:n]

    def rank_of(self, symbol: str) -> int | None:
        for row in self.rows:
            if row.symbol == symbol:
                return row.rank
        return None


# ------------------------------------------------------- benchmark sleeve ---


@dataclass(slots=True)
class BenchmarkSleeve:
    """The NIFTYBEES half, valued but never traded.

    Held manually outside the system. Modelled here only so that total-account
    NAV is reportable and so allocation variants (100% momentum vs 50/50) are
    genuinely comparable rather than being the same book at different scale.

    Tracked as a rupee value compounding at the index return with a notional
    monthly rebalance against the momentum sleeve -- the same construction the
    research used, so live reporting and the backtest agree.

    Tax differs from the momentum sleeve and this is not incidental: §11q's F3
    found that taxing this leg annually instead of deferring LTCG understated
    the 50/50 after-tax return by 1.2pp. `cost_basis` is tracked so the deferred
    liability is computable.
    """

    enabled: bool = False
    index: str = "NIFTY_50"
    target_weight: float = 0.0
    value: Decimal = money.ZERO
    cost_basis: Decimal = money.ZERO
    last_rebalanced: date | None = None

    @property
    def unrealised_gain(self) -> Decimal:
        return money.rupees(self.value - self.cost_basis)


@dataclass(frozen=True, slots=True)
class Position:
    symbol: str
    quantity: int
    close: Decimal
    value: Decimal
    weight: float
    unrealised_pnl: Decimal = money.ZERO


@dataclass(slots=True)
class Snapshot:
    portfolio_id: str
    date: date
    nav: Decimal
    cash: Decimal
    invested_value: Decimal
    units: Decimal
    unit_nav: Decimal
    total_contributed: Decimal
    positions: list[Position] = field(default_factory=list)
    peak_unit_nav: Decimal = money.ZERO
    drawdown: float = 0.0
    benchmark_close: Decimal | None = None
    #: Value of the synthetic NIFTYBEES leg on this date, if the portfolio has
    #: one. Stored rather than recomputed so a past snapshot stays exact even
    #: after the index series is backfilled.
    benchmark_sleeve_value: Decimal | None = None
    panel_manifest_hash: str = ""
    computed_at: datetime | None = None

    @property
    def snapshot_id(self) -> str:
        return f"{self.portfolio_id}:{self.date.isoformat()}"


# -------------------------------------------------------------------- fills ---


@dataclass(frozen=True, slots=True)
class Fill:
    """A reported execution, or a deliberate skip. Input to `record_fills`."""

    seq: int
    quantity: int = 0
    price: Decimal | None = None
    charges: Decimal = money.ZERO
    order_id: str = ""
    executed_at: datetime | None = None
    skip: str = ""

    @property
    def is_skip(self) -> bool:
        return bool(self.skip)

    def __post_init__(self) -> None:
        if self.is_skip:
            return
        if self.quantity <= 0:
            raise ValueError(f"fill seq {self.seq}: quantity must be > 0 (or pass skip=...)")
        if self.price is None:
            raise ValueError(f"fill seq {self.seq}: price is required for an execution")
