"""`portfolio_id` parsing and validation (design §3).

Format: `{mode}_{strategy}_{version}_{seq}` -- e.g. `paper_momentum_v1_001`.

The id is a natural key carrying real meaning: it is the Mongo `_id` of the
portfolio, the partition key of every other collection, and the thing that
distinguishes real money from simulated money. Parsing it is therefore strict
-- a near-miss is rejected rather than coerced, because a typo that silently
creates a *second* portfolio is worse than a crash.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Literal

Mode = Literal["paper", "live", "shadow"]

MODES: Final[frozenset[str]] = frozenset({"paper", "live", "shadow"})

# Anchored, and `seq` is exactly three digits: zero-padding keeps ids sorting
# lexicographically in the same order they were created, which matters because
# Mongo sorts _id as a string.
_PATTERN: Final[re.Pattern] = re.compile(
    r"^(?P<mode>paper|live|shadow)"
    r"_(?P<strategy>[a-z][a-z0-9]*)"
    r"_(?P<version>v\d+)"
    r"_(?P<seq>\d{3})$"
)

MIN_SEQ: Final[int] = 1
MAX_SEQ: Final[int] = 999


class InvalidPortfolioId(ValueError):
    """Raised for anything that is not exactly a well-formed portfolio id."""


@dataclass(frozen=True, slots=True)
class PortfolioId:
    """A parsed portfolio id. Immutable, like the id itself (§3.3)."""

    mode: Mode
    strategy: str
    version: str
    seq: int

    def __str__(self) -> str:
        return f"{self.mode}_{self.strategy}_{self.version}_{self.seq:03d}"

    @property
    def is_live(self) -> bool:
        """True only for real money.

        The guard for irreversible operations. Deliberately not `mode !=
        "paper"` -- shadow is simulated too, and conflating the two would put
        a required-reviewer gate on a portfolio that does not need one while
        implying shadow trades are real.
        """
        return self.mode == "live"

    @property
    def strategy_key(self) -> str:
        """`{strategy}_{version}` -- the frozen-signal identity.

        Two portfolios share a signal only if both parts match. `momentum_v1`
        and `momentum_v2` are different strategies and must never share a
        params hash (§3.3).
        """
        return f"{self.strategy}_{self.version}"


def parse(value: str) -> PortfolioId:
    """Parse a portfolio id, or raise `InvalidPortfolioId`."""
    if not isinstance(value, str):
        raise InvalidPortfolioId(f"expected str, got {type(value).__name__}")
    match = _PATTERN.match(value)
    if match is None:
        raise InvalidPortfolioId(
            f"{value!r} is not a portfolio id "
            f"(expected {{{'|'.join(sorted(MODES))}}}_{{strategy}}_v{{n}}_{{nnn}})"
        )
    return PortfolioId(
        mode=match["mode"],  # type: ignore[arg-type]
        strategy=match["strategy"],
        version=match["version"],
        seq=int(match["seq"]),
    )


def build(*, mode: str, strategy: str, version: str, seq: int) -> str:
    """Construct a portfolio id from its parts, validating the result."""
    if not MIN_SEQ <= seq <= MAX_SEQ:
        raise InvalidPortfolioId(
            f"seq {seq} outside {MIN_SEQ}..{MAX_SEQ}; a portfolio id has three digits"
        )
    candidate = f"{mode}_{strategy}_{version}_{seq:03d}"
    parse(candidate)  # validates mode/strategy/version shape
    return candidate


def is_valid(value: str) -> bool:
    """True if `value` parses. For call sites that want a check, not an error."""
    try:
        parse(value)
    except InvalidPortfolioId:
        return False
    return True
