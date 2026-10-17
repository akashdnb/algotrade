from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from algotrade.backtest import registry  # noqa: E402
from algotrade.io import store  # noqa: E402
from algotrade.zones.backtest import event_study_metrics  # noqa: E402
from algotrade.zones.config import load_configs  # noqa: E402
from algotrade.zones.pipeline import run_for_universe  # noqa: E402

DEV_END = date(2021, 12, 31)


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay zone scoring on the curated development archive")
    parser.add_argument("--end-date", type=date.fromisoformat, default=DEV_END)
    parser.add_argument("--output-dir", type=Path, default=Path("reports"))
    args = parser.parse_args()

    config = load_configs(Path("config/zones/zones_v1.yaml"))
    params = {timeframe.value: asdict(cfg) for timeframe, cfg in config.items()}
    trial = registry.register(
        name="zone_scoring_v1",
        hypothesis=(
            "ATR-normalized displacement away from a compact base, combined with "
            "volume, structure and freshness, should rank zones with stronger "
            "subsequent favourable reaction."
        ),
        params=params,
        dataset="dev",
        alpha_decay_note="Slow to moderate; event-study only.",
    )

    panel = store.read(Path("data/curated"), end=args.end_date)
    zones = []
    for day in sorted(panel["date"].unique().to_list()):
        for result in run_for_universe(panel, configs=config, as_of_date=day):
            zones.extend(result.all_zones)

    metrics = event_study_metrics(zones, panel)
    registry.record_result(trial.trial_id, metrics)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    path = args.output_dir / f"zone_validation_{args.end_date.isoformat()}.md"
    path.write_text(
        "# Zone scoring validation\n\n"
        f"Trial: {trial.trial_id}\n\n"
        "```json\n"
        + json.dumps(metrics, indent=2, default=str)
        + "\n```\n",
        encoding="utf-8",
    )
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
