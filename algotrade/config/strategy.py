"""The frozen strategy parameters, readable without the compute engine.

`StrategySpec` lived in `services/signals.py` until 2026-09-05, purely because
that is where it is consumed. Nothing about it needs polars -- it is nine
numbers read from a YAML file -- but importing it dragged the entire signal
pipeline in behind it.

That mattered the day the dashboard needed the rank buffer to render a review
screen: the deploy does not carry polars, so the function failed at startup and
every route returned 500. Moving the class here separates *what the strategy
is* from *how signals are computed*, so a reader of the parameters does not
have to load the machinery that uses them.

`services.signals` re-exports it, so existing imports are unaffected.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


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
    def from_yaml(cls, path: Path | str) -> "StrategySpec":
        import yaml

        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
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
        payload = "|".join(str(x) for x in (
            self.version, self.lookback, self.skip, self.entry, self.exit,
            self.universe_size, self.universe_lookback,
            self.min_turnover, self.min_coverage,
        ))
        return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()[:16]
