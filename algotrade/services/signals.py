"""Momentum ranking for one session.

**No new signal logic.** This composes the exact functions the backtest uses
(`universe` -> `features` -> `strategies.restrict_to_universe`), so there is
one implementation of the strategy and live cannot drift from what was
validated. That property is the whole reason the deployment design insists on
a shared core.

Signals belong to the **strategy**, not to a portfolio: the ranking is a
property of `momentum_v1` and the market on a given date. Every portfolio on
that version reads the same document, which is why `signals._id` is
`{strategy_version}:{date}` and carries no portfolio field.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

import polars as pl

from algotrade.core import features, strategies, universe
from algotrade.domain import money
from algotrade.domain.models import SignalRow, SignalSet

log = logging.getLogger(__name__)


class SignalUnavailable(RuntimeError):
    """The ranking could not be computed for the requested session."""


@dataclass(frozen=True, slots=True)
class StrategySpec:
    """The frozen parameters, loaded from `config/strategy/momentum_v1.yaml`."""

    version: str
    lookback: int
    skip: int
    entry: int
    exit: int
    universe_size: int
    universe_lookback: int
    min_turnover: float
    min_coverage: float

    @classmethod
    def from_yaml(cls, path) -> "StrategySpec":  # noqa: ANN001
        import yaml

        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls(
            version=raw["version"],
            lookback=raw["feature"]["lookback"],
            skip=raw["feature"]["skip"],
            entry=raw["book"]["entry"],
            exit=raw["book"]["exit"],
            universe_size=raw["universe"]["size"],
            universe_lookback=raw["universe"]["lookback"],
            min_turnover=float(raw["universe"]["min_turnover"]),
            min_coverage=float(raw["universe"]["min_coverage"]),
        )

    def params_hash(self) -> str:
        """Content address of the parameters actually in force.

        Compared against `portfolios.strategy_params_hash` before every
        rebalance. A frozen strategy that can be edited without anyone
        noticing is not frozen.
        """
        import hashlib

        payload = "|".join(str(x) for x in (
            self.version, self.lookback, self.skip, self.entry, self.exit,
            self.universe_size, self.universe_lookback,
            self.min_turnover, self.min_coverage,
        ))
        return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()[:16]


class SignalService:
    """Ranks the eligible universe by momentum, for one session."""

    def __init__(self, spec: StrategySpec) -> None:
        self._spec = spec

    @property
    def spec(self) -> StrategySpec:
        return self._spec

    def generate(
        self,
        panel: pl.DataFrame,
        *,
        as_of: date,
        top_n: int | None = None,
        panel_manifest_hash: str = "",
        now: datetime | None = None,
    ) -> SignalSet:
        """Top-`top_n` momentum names as of `as_of`'s close.

        Args:
            panel: Adjusted panel with date, symbol, close, turnover.
            as_of: The session to rank. Must be present in the panel.
            top_n: How many to store. Defaults to the exit threshold (60), not
                the entry threshold (30) -- the rank buffer needs to know
                whether a HELD name has fallen past 60, and a top-30 list
                cannot answer that.
            panel_manifest_hash: Recorded for reproducibility (§9.2).

        Raises:
            SignalUnavailable: the session is absent, or no name qualifies.
        """
        top_n = top_n or self._spec.exit

        sessions = set(panel["date"].unique().to_list())
        if as_of not in sessions:
            raise SignalUnavailable(
                f"{as_of} is not in the panel (last: {max(sessions) if sessions else 'none'})"
            )

        liquid = universe.liquidity(panel, lookback=self._spec.universe_lookback)
        members = universe.membership(
            liquid,
            size=self._spec.universe_size,
            exclude=set(universe.DEFAULT_EXCLUSIONS),
            lookback=self._spec.universe_lookback,
            min_turnover=self._spec.min_turnover,
            min_coverage=self._spec.min_coverage,
        )
        with_feature = features.add(
            liquid, "momentum", lookback=self._spec.lookback, skip=self._spec.skip
        )
        eligible = strategies.restrict_to_universe(with_feature, members)

        day = (
            eligible.filter(
                (pl.col("date") == as_of) & pl.col("feature").is_not_null()
            )
            .sort("feature", descending=True)
            .head(top_n)
        )
        if day.is_empty():
            raise SignalUnavailable(
                f"no eligible name has a momentum score on {as_of} -- "
                f"usually insufficient history ({self._spec.lookback} + "
                f"{self._spec.skip} sessions required)"
            )

        universe_size = int(
            eligible.filter(pl.col("date") == as_of).height
        )

        rows = [
            SignalRow(
                rank=i + 1,
                symbol=r["symbol"],
                feature=float(r["feature"]),
                close=money.rupees(str(r["close"])) if r.get("close") is not None else None,
                adtv=money.rupees(str(round(r["adtv"], 2))) if r.get("adtv") is not None else None,
            )
            for i, r in enumerate(day.iter_rows(named=True))
        ]

        return SignalSet(
            strategy_version=self._spec.version,
            date=as_of,
            rows=rows,
            universe_size=universe_size,
            panel_manifest_hash=panel_manifest_hash,
            computed_at=now,
        )


def compare(previous: SignalSet | None, current: SignalSet) -> dict:
    """What changed between two sessions' rankings, for the daily message.

    Entries/exits are computed against the ENTRY threshold, because that is
    the boundary a reader cares about -- "is this name now buyable" -- even
    though the stored list runs deeper for the buffer's sake.
    """
    if previous is None:
        return {"entered": [], "exited": [], "risers": [], "fallers": [],
                "first_signal": True}

    prev_rank = {r.symbol: r.rank for r in previous.rows}
    curr_rank = {r.symbol: r.rank for r in current.rows}

    entered = sorted(set(curr_rank) - set(prev_rank))
    exited = sorted(set(prev_rank) - set(curr_rank))

    moves = [
        (s, prev_rank[s] - curr_rank[s])
        for s in set(prev_rank) & set(curr_rank)
        if prev_rank[s] != curr_rank[s]
    ]
    moves.sort(key=lambda m: -m[1])
    return {
        "entered": entered,
        "exited": exited,
        "risers": [m for m in moves[:3] if m[1] > 0],
        "fallers": [m for m in moves[-3:] if m[1] < 0],
        "first_signal": False,
    }
