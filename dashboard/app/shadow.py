"""Pairing a book with its shadow, and reading the gap between them.

A shadow portfolio follows every instruction exactly, at the reference price,
with nobody deciding anything. The book beside it is the one you actually
trade. The difference between their unit NAVs is therefore the total cost of
being human about it -- the trades you declined, the ones you delayed, and the
prices you got instead of the ones the recommendation assumed.

**Unit NAV, never NAV.** A contribution buys units at the prevailing price and
does not move unit NAV, so the two books stay comparable even if they are
funded differently. Comparing NAV would report a deposit as outperformance.

**The gap is discretion AND slippage together.** They are different things --
one is a decision, the other is the market -- and this page must not imply it
has separated them. `executions.slippage_bps` is recorded per fill, so the
split is recoverable after the fact; until it is shown, the number is labelled
as the pair.
"""

from __future__ import annotations

from typing import Any

#: `mode_strategy_vN_nnn`. Two portfolios are counterparts when everything but
#: the mode matches: same strategy, same version, same sequence number.
_SHADOW = "shadow"


def _parts(portfolio_id: str) -> tuple[str, str] | None:
    mode, _, rest = portfolio_id.partition("_")
    return (mode, rest) if rest else None


def counterpart(portfolio_id: str, options: list[dict[str, Any]]) -> str | None:
    """The shadow of a real book, or the real book behind a shadow.

    Returns None when there is no pair, which is the normal state until a
    shadow has been forked -- the caller renders nothing rather than an empty
    chart implying a comparison exists.
    """
    parsed = _parts(portfolio_id)
    if parsed is None:
        return None
    mode, wanted = parsed

    for option in options:
        other = option.get("_id", "")
        if other == portfolio_id:
            continue
        other_parsed = _parts(other)
        if other_parsed is None:
            continue
        other_mode, other_rest = other_parsed
        if other_rest != wanted:
            continue
        # A shadow pairs with anything that is not a shadow, and vice versa.
        if (mode == _SHADOW) != (other_mode == _SHADOW):
            return other
    return None


def align(left: list[dict], right: list[dict], *, key: str = "unit_nav") -> tuple[
    list[dict], list[dict]
]:
    """Both series restricted to the dates they share, in order.

    A shadow forked mid-life has a shorter history, and charting the two
    unaligned would rebase them to different starting sessions -- which would
    invent a divergence on the very first point.
    """
    by_date = {row["date"]: row for row in right if row.get(key) is not None}
    common = [row for row in left
              if row.get(key) is not None and row.get("date") in by_date]
    return common, [by_date[row["date"]] for row in common]


def divergence(traded: list[dict], shadow: list[dict]) -> dict[str, Any] | None:
    """How far the traded book has drifted from perfect obedience.

    Both series are rebased to their first COMMON session, so the number is
    "since the fork", not "since inception" -- a shadow created today has not
    had time to differ, and reporting inception-to-date would attribute the
    traded book's whole history to discretion it never exercised.
    """
    traded, shadow = align(traded, shadow)
    if len(traded) < 2:
        return None

    def growth(series: list[dict]) -> float | None:
        first, last = float(series[0]["unit_nav"]), float(series[-1]["unit_nav"])
        return (last / first - 1.0) if first else None

    traded_return, shadow_return = growth(traded), growth(shadow)
    if traded_return is None or shadow_return is None:
        return None

    return {
        "from": traded[0]["date"],
        "to": traded[-1]["date"],
        "sessions": len(traded),
        "traded_return": traded_return,
        "shadow_return": shadow_return,
        "gap": traded_return - shadow_return,
        # True only once the books have actually parted. Before that the gap is
        # 0.0 by construction and reporting it as a measurement would suggest
        # discretion has been shown to cost nothing.
        "measured": any(
            t["unit_nav"] != s["unit_nav"] for t, s in zip(traded, shadow)
        ),
    }
