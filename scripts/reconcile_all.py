#!/usr/bin/env python
"""Reconcile the full action history against observed price moves (§11 task B).

Everything before this was validated on a 4-month slice. This produces the real
match rate, and the definitive `unadjusted_symbols` exclusion list that the
universe needs *before* any signal is computed -- a phantom gap sits in exactly
the extreme tail a cross-sectional strategy selects from.

Writes reports/reconciliation.md and config/exclusions.yaml. The exclusion list
is committed because it is a *decision*, not derived data.

    python scripts/reconcile_all.py
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

import polars as pl

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from algotrade.core import actions as ca  # noqa: E402
from algotrade.core import adjust as adj  # noqa: E402
from algotrade.core import integrity as ig  # noqa: E402
from algotrade.core import reconcile as rc  # noqa: E402
from algotrade.io import actions as astore  # noqa: E402
from algotrade.io import store  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--curated", type=Path, default=REPO / "data/curated")
    ap.add_argument("--actions", type=Path, default=REPO / "data/raw/actions")
    args = ap.parse_args()

    print("loading curated panel ...", flush=True)
    panel = store.read(args.curated)
    manifest = store.read_manifest(args.curated)
    print(f"  {manifest['sessions']:,} sessions, {panel.height:,} rows, "
          f"{manifest['first_session']} .. {manifest['last_session']}")

    print("loading cached actions ...", flush=True)
    raw = astore.load(args.actions)
    feed = ca.classify(raw)
    with_factor = feed.filter(pl.col("price_factor").is_not_null())
    print(f"  {raw.height:,} actions, {with_factor.height:,} imply a price factor")

    print("detecting price discontinuities ...", flush=True)
    disc = ig.find_actions(panel)
    print(f"  {disc.height:,} discontinuities")

    print("building rename chains ...", flush=True)
    price_chains = rc.resolve_chains(ig.find_renames(panel))
    isin_chains = rc.chains_from_isin(astore.isin_map(raw))
    chains = rc.merge_chains(price_chains, isin_chains)
    print(f"  {price_chains.height:,} from price continuity, "
          f"{isin_chains.height:,} from ISIN -> {chains.height:,} merged")

    print("reconciling ...", flush=True)
    result = rc.reconcile(disc, with_factor, chains=chains)

    summary = rc.summarise(result)
    print()
    print(summary)

    total = sum(result[k].height for k in ("matched", "factor_mismatch"))
    rate = result["matched"].height / max(total, 1)
    print(f"\nmatch rate among paired events: {rate:.1%}")

    windows = adj.unadjusted_windows(result)
    excluded = adj.unadjusted_symbols(result)
    print(f"unexplained moves above 1.75x: {len(excluded)} symbols, "
          f"{windows.height:,} (symbol, date) rows to drop")

    _write_reports(REPO, manifest, raw, disc, chains, price_chains, isin_chains,
                   result, summary, excluded, windows)
    print("\nwrote reports/reconciliation.md and config/exclusions.yaml")
    return 0



def _write_reports(repo, manifest, raw, disc, chains, price_chains, isin_chains,
                   result, summary, excluded, windows):
    (repo / "reports").mkdir(exist_ok=True)
    stamp = datetime.now().astimezone().replace(microsecond=0).isoformat()
    counts = {r["outcome"]: r["count"] for r in summary.to_dicts()}
    paired = counts["matched"] + counts["factor_mismatch"]

    lines = [
        "# Corporate-action reconciliation — full history",
        "",
        f"Generated {stamp}",
        "",
        "## Inputs",
        "",
        f"| Panel sessions | {manifest['sessions']:,} |",
        "|---|---|",
        f"| Panel rows | {manifest['rows']:,} |",
        f"| Date range | {manifest['first_session']} .. {manifest['last_session']} |",
        f"| Cached actions | {raw.height:,} |",
        f"| Price discontinuities | {disc.height:,} |",
        "",
        "## Rename chains",
        "",
        f"| From price continuity | {price_chains.height:,} |",
        "|---|---|",
        f"| From shared ISIN | {isin_chains.height:,} |",
        f"| Merged (price wins) | {chains.height:,} |",
        "",
        "## Outcomes",
        "",
        "| Outcome | Count |",
        "|---|---|",
    ]
    for name in ("matched", "factor_mismatch", "action_no_move", "move_no_action"):
        lines.append(f"| {name} | {counts[name]:,} |")
    lines += [
        "",
        f"**Match rate among paired events: {counts['matched'] / max(paired,1):.1%}**",
        "",
        "## Largest factor mismatches",
        "",
        "```",
        str(result["factor_mismatch"].select(
            "date", "symbol", "implied_ratio", "price_factor", "deviation"
        ).head(15)),
        "```",
        "",
        "## Unexplained moves (excluded from the universe)",
        "",
        f"{len(excluded)} symbols, {windows.height:,} (symbol, date) rows.",
        "",
        "Windows are dropped rather than whole symbols: excluding a symbol's entire",
        "history over one bad day removes ~17% of universe names, which reintroduces",
        "the selection bias this project exists to avoid.",
        "",
        "```",
        str(result["move_no_action"].select(
            "date", "symbol", "implied_ratio"
        ).head(25)),
        "```",
    ]
    (repo / "reports" / "reconciliation.md").write_text("\n".join(lines) + "\n")

    (repo / "config").mkdir(exist_ok=True)
    (repo / "config" / "exclusions.yaml").write_text(
        "# Symbols with a price move no corporate action explains (PROJECT.md §10.6).\n"
        "# A phantom gap of tens of percent lands at the top of a cross-sectional\n"
        "# ranking, so these leave the universe until each is resolved individually.\n"
        f"# Generated by scripts/reconcile_all.py on {stamp}\n"
        "# Filtering uses unadjusted_windows() on (symbol, date); this symbol list\n"
        "# is for review only -- dropping whole symbols would itself bias the universe.\n"
        "unexplained_moves:\n"
        + "".join(f"  - {s}\n" for s in excluded)
    )


if __name__ == "__main__":
    raise SystemExit(main())
