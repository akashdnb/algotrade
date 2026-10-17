from __future__ import annotations
import argparse, json
from decimal import Decimal
from pathlib import Path
from .config import load_configs


def render_zones_summary(results):
    lines=["Zones daily summary"]
    for result in results:
        lines.append(f"{result.symbol} · ₹{result.current_price} · " + ", ".join(f"{tf.value}:{len(zones)}" for tf,zones in result.zones.items()))
    return "\n".join(lines)


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--symbol"); parser.add_argument("--date"); parser.add_argument("--json",action="store_true")
    parser.parse_args()
    print(json.dumps({"status":"report requires a live ZoneRepository context"}) if parser.parse_args().json else "Zone report CLI is read-only; use the job output for the latest snapshot.")
    return 0

if __name__ == "__main__": raise SystemExit(main())
