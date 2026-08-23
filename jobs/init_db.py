#!/usr/bin/env python
"""Create collections, validators and indexes; verify them against the design.

Idempotent -- safe on every deploy. `--verify` makes no changes and exits
non-zero if the live database differs from `repositories/schema.py`.

    python -m jobs.init_db
    python -m jobs.init_db --dry-run
    python -m jobs.init_db --verify

The target database comes from configuration (ENVIRONMENT / MONGODB_DATABASE),
never from a flag: a `--env` flag that disagrees with the environment the
secrets belong to is exactly the mistake the config guards exist to prevent.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from algotrade.config import settings as settings_mod  # noqa: E402
from algotrade.config import validation  # noqa: E402
from algotrade.repositories import client, schema  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change, write nothing")
    ap.add_argument("--verify", action="store_true",
                    help="check the live database matches schema.py; no writes")
    args = ap.parse_args()

    s = settings_mod.load()
    # Only the Mongo-related settings need to be valid to run this; Telegram
    # and the heartbeat are irrelevant to applying a schema, and demanding them
    # would make bootstrapping a new environment circular.
    report = validation.validate(s)
    mongo_errors = [f for f in report.errors if f.key.startswith("MONGODB")]
    if mongo_errors:
        for finding in mongo_errors:
            print(finding)
        return 2

    print(f"environment={s.environment} database={s.mongodb_database}")
    print(f"uri={settings_mod.redact_uri(s.mongodb_uri)}\n")

    db = client.connect(s.mongodb_uri, database=s.mongodb_database)

    if args.verify:
        problems = client.verify_schema(db)
        if problems:
            print(f"FAIL -- {len(problems)} discrepancies:")
            for p in problems:
                print(f"  {p}")
            return 1
        usage = client.stats(db)
        print(f"OK -- {len(schema.ALL)} collections match schema.py")
        print(f"     {usage['storage_bytes'] / 1e6:.1f} MB storage, "
              f"{usage['objects']:,} documents")
        return 0

    for name, actions in client.apply_schema(db, dry_run=args.dry_run).items():
        print(name)
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
