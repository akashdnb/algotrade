"""Forking a portfolio: an exact copy of a book under a new id.

A shadow portfolio answers one question -- *what would this strategy have
returned if every instruction had been followed exactly?* -- and it can only
answer it if it starts from the same place. A shadow started from cash today
would pay entry costs on all thirty names while the book it is compared against
already holds them, and the difference between the two would be dominated by
that rather than by any decision anyone made.

So the shadow is FORKED: every rebalance run, recommendation, execution, ledger
entry and snapshot is copied under the new id. Holdings deliberately are NOT
copied -- they are rebuilt from the copied executions, which is what proves the
copy is faithful rather than merely present.

**This is only honest while the source is itself an exact replay of what the
system instructed.** The moment a fill is entered by hand -- a different price,
a broker order id -- the source contains a human decision, and copying it into
the shadow would put that decision on *both* sides of the comparison and hide
the very thing the shadow exists to measure. `discretion_evidence` finds that
point; the caller refuses there.

Every id in this system is a pure function of the portfolio id
(`portfolio:period`, `portfolio:period:rN`, `set:seq:n`,
`portfolio:kind:discriminator`), so the rewrite is total and mechanical: no id
can embed the source portfolio and escape it.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

#: Copied in this order. `holdings` is absent on purpose -- see the module
#: docstring. `job_runs` and `audit_logs` are absent too: they record what the
#: *machinery* did, and the fork did not happen twice.
FORKED_COLLECTIONS: tuple[str, ...] = (
    "rebalance_runs",
    "recommendations",
    "executions",
    "cash_transactions",
    "portfolio_snapshots",
)

#: Fields whose value embeds a portfolio id. Only these are rewritten -- a
#: blanket string replace would also edit free-text notes, which are a record
#: of what someone wrote and not ours to alter.
ID_FIELDS: frozenset[str] = frozenset({
    "_id",
    "portfolio_id",
    "set_id",
    "rebalance_run_id",
    "recommendation_set_id",
    "execution_id",
})


class ForkRefused(RuntimeError):
    """The source cannot be copied faithfully."""


def rewrite(doc: Mapping[str, Any], *, source: str, target: str) -> dict[str, Any]:
    """One document, re-keyed from `source` to `target`.

    Replaces every occurrence rather than just a leading one: a cash entry's
    `_id` is `portfolio:kind:<execution id>` and the execution id embeds the
    portfolio a second time. Portfolio ids are `mode_strategy_vN_nnn`, which
    cannot occur inside a symbol, date or period, so there is nothing else in
    these fields for the replace to hit.
    """
    out: dict[str, Any] = {}
    for key, value in doc.items():
        if key in ID_FIELDS and isinstance(value, str):
            out[key] = value.replace(source, target)
        elif isinstance(value, list):
            out[key] = [
                rewrite(v, source=source, target=target)
                if isinstance(v, dict) else v
                for v in value
            ]
        elif isinstance(value, dict):
            out[key] = rewrite(value, source=source, target=target)
        else:
            out[key] = value
    return out


def discretion_evidence(executions: Iterable[Mapping[str, Any]]) -> list[str]:
    """Fills that prove a human deviated from the instruction.

    Empty means the source book is still a mechanical replay and a fork of it
    is a true copy of the strategy's own behaviour. Non-empty means the fork
    would carry someone's judgement into the control group.

    Two signals, either sufficient:

    - a **broker order id**, which only a real fill has; and
    - a price that is not the reference price, which is slippage or a
      deliberately different limit. Both are divergence.
    """
    out = []
    for execution in executions:
        eid = str(execution.get("_id") or execution.get("execution_id") or "?")
        symbol = execution.get("symbol", "?")
        if execution.get("broker_order_id"):
            out.append(f"{eid} ({symbol}) carries broker order "
                       f"{execution['broker_order_id']}")
            continue
        price, reference = execution.get("price"), execution.get("reference_price")
        if price is not None and reference is not None and price != reference:
            out.append(f"{eid} ({symbol}) filled at {price}, "
                       f"reference was {reference}")
    return out
