#!/usr/bin/env python
"""Back up MongoDB, verify the export, and optionally restore it.

Atlas M0 has no automated backups. This is the only one that will exist.

    python -m jobs.backup                          # write and verify
    python -m jobs.backup --out backups/           # choose a directory
    python -m jobs.backup --inspect <file>         # what is in it
    python -m jobs.backup --restore <file> --into algotrade_scratch
    python -m jobs.backup --restore <file> --into algotrade_paper --force

The verify step runs immediately after writing: a backup nobody has ever read
is a hypothesis, not a backup.

Exit codes: 0 written and verified, 1 verification or restore failed,
2 configuration error.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from algotrade.config import settings as settings_mod  # noqa: E402
from algotrade.config import validation  # noqa: E402
from algotrade.ops import backup as backup_ops  # noqa: E402
from algotrade.platform import clock  # noqa: E402
from algotrade.repositories import client  # noqa: E402

log = logging.getLogger("jobs.backup")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=Path("backups"))
    ap.add_argument("--keep", type=int, default=12,
                    help="local backups to retain (default: 12 weeks)")
    ap.add_argument("--inspect", type=Path, default=None)
    ap.add_argument("--restore", type=Path, default=None)
    ap.add_argument("--into", default=None,
                    help="target database for a restore")
    ap.add_argument("--force", action="store_true",
                    help="allow restoring into a non-empty database")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")

    # --- inspect needs no database ------------------------------------------
    if args.inspect:
        info = backup_ops.inspect(args.inspect)
        print(f"taken_at   {info['taken_at']}")
        print(f"database   {info['database']}")
        print(f"documents  {info['documents']:,}")
        for name, count in sorted(info["counts"].items()):
            print(f"  {name:<22} {count:>7,}")
        return 0

    s = settings_mod.load()
    report = validation.validate(s)
    blocking = [f for f in report.errors if f.key.startswith("MONGODB")]
    if blocking:
        for finding in blocking:
            print(finding, file=sys.stderr)
        return 2

    # --- restore --------------------------------------------------------------
    if args.restore:
        target = args.into or s.mongodb_database
        # The one operation here that can destroy data, so it is explicit
        # about which database it is about to overwrite.
        print(f"restore    {args.restore}")
        print(f"into       {target}")
        if target in ("algotrade_prod",) and not args.force:
            print("refusing to restore into prod without --force", file=sys.stderr)
            return 2
        db = client.connect(s.mongodb_uri, database=target)
        try:
            restored = backup_ops.restore_database(
                db, args.restore, allow_non_empty=args.force,
                dry_run=args.dry_run)
        except backup_ops.RestoreRefused as exc:
            print(f"\nREFUSED: {exc}", file=sys.stderr)
            return 1
        for name, count in sorted(restored.items()):
            if count:
                print(f"  {name:<22} {count:>7,}")
        if args.dry_run:
            print("\n--dry-run: nothing written.")
            return 0
        problems = backup_ops.verify_roundtrip(db, args.restore)
        if problems:
            print(f"\nrestore verification FAILED ({len(problems)}):",
                  file=sys.stderr)
            for p in problems:
                print(f"  {p}", file=sys.stderr)
            return 1
        print("\nrestore verified against the backup file")
        return 0

    # --- backup ---------------------------------------------------------------
    now = clock.SystemClock().now()
    db = client.connect(s.mongodb_uri, database=s.mongodb_database)
    destination = args.out / backup_ops.filename_for(now, s.mongodb_database)

    print(f"database   {s.mongodb_database}")
    print(f"output     {destination}")

    result = backup_ops.export_database(db, destination=destination, taken_at=now)
    print(f"\n{result.summary()}")

    problems = backup_ops.verify_roundtrip(db, destination)
    if problems:
        print(f"\nVERIFICATION FAILED ({len(problems)}):", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1
    print("verified against the live database")

    removed = backup_ops.prune(args.out, keep=args.keep)
    if removed:
        print(f"pruned {len(removed)} old backup(s), keeping {args.keep}")

    if (out := __import__("os").environ.get("GITHUB_OUTPUT")):
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"backup_path={destination}\n")
            fh.write(f"backup_name={destination.name}\n")
            fh.write(f"documents={result.documents}\n")
            fh.write(f"bytes={result.bytes_written}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
