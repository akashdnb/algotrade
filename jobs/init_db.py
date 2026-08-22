#!/usr/bin/env python
"""Create collections, validators and indexes; verify them against the design.

Idempotent -- safe on every deploy. `--verify` makes no changes and exits
non-zero if the live database differs from `repositories/schema.py`, which is
the Phase 1 gate ("indexes verified against a real M0").

    python -m jobs.init_db --env local
    python -m jobs.init_db --env paper --dry-run
    python -m jobs.init_db --env paper --verify
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from algotrade.platform import config  # noqa: E402
from algotrade.repositories import client, schema  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--env", default=None, help="local | paper | live")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change, write nothing")
    ap.add_argument("--verify", action="store_true",
                    help="check the live database matches schema.py; no writes")
    args = ap.parse_args()

    settings = config.load(args.env, config_dir=REPO / "config")
    print(f"env={settings.env} database={settings.database}")
    print(f"uri={config._redact_uri(settings.mongodb_uri)}\n")

    db = client.connect(settings.mongodb_uri, database=settings.database)

    if args.verify:
        problems = client.verify_schema(db)
        if problems:
            print(f"FAIL -- {len(problems)} discrepancies:")
            for p in problems:
                print(f"  {p}")
            return 1
        print(f"OK -- {len(schema.ALL)} collections match schema.py")
        usage = client.stats(db)
        print(f"     {usage['storage_bytes'] / 1e6:.1f} MB storage, "
              f"{usage['objects']:,} documents")
        return 0

    changes = client.apply_schema(db, dry_run=args.dry_run)
    for name, actions in changes.items():
        print(f"{name}")
        for action in actions:
            print(f"  - {action}")

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return 0

    problems = client.verify_schema(db)
    if problems:
        print(f"\nWARNING -- {len(problems)} discrepancies after apply:")
        for p in problems:
            print(f"  {p}")
        return 1
    print(f"\nverified: {len(schema.ALL)} collections match schema.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
