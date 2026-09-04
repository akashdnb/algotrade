#!/usr/bin/env python
"""Pack the historical bhavcopy archive for the `market-archive-v1` release.

`daily.yml` restores `data/archive` from an Actions cache and, on a miss,
downloads `archive.tar.zst` from this release. **The release has never
existed** and the cache is 192 bytes, so every miss silently fell through to
`continue-on-error` and CI carried on with the committed sessions alone --
2024 onward, against a strategy validated on 2010-2026. See
`claude_fixes/2026-09-04-archive-release-missing.md`.

    python scripts/pack_archive.py --out /tmp/archive.tar.zst
    python scripts/pack_archive.py --out /tmp/archive.tar.zst --verify

**Raw CSVs, never the curated Parquet.** Curated is EQ-filtered and
corporate-action adjusted; archiving it would freeze both decisions forever and
make questions like "what does excluding SERIES == 'BE' cost us?" permanently
unanswerable for history. Answering that one is what
`measure_series_filter.py` needed these files for.

**Directory order is load-bearing.** `archive.discover` searches in order and
later wins, and within one root it walks `sorted(rglob(...))`. `backfill/`
holds repaired files for sessions the integrity guard rejected -- its earliest
is 2019-10-28, the first rejected date in the curated manifest -- so it must
override the mirror. The numeric prefixes below are what preserve that through
a flatten-and-repack.
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import tarfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from algotrade.io.archive import discover  # noqa: E402

#: (source, name inside the tar). Sorted order decides precedence, so the
#: prefixes are not decoration -- swapping them would let a known-bad session
#: shadow the file that repairs it.
SOURCES: tuple[tuple[Path, str], ...] = (
    (REPO / "data" / "raw" / "nse-data-bank" / "data", "1-nse-data-bank"),
    (REPO / "data" / "raw" / "backfill", "2-backfill"),
)
PATTERNS = ("sec_bhavdata_full_*.csv", "cm*bhav.csv")


def build(out: Path) -> Path:
    tar_path = out.with_suffix("") if out.suffix == ".zst" else out
    total = 0
    with tarfile.open(tar_path, "w") as tar:
        for source, arc in SOURCES:
            if not source.is_dir():
                raise SystemExit(f"missing source: {source}")
            files = sorted(p for pattern in PATTERNS
                           for p in source.rglob(pattern))
            print(f"  {arc:<18} {len(files):>5} files")
            for path in files:
                tar.add(path, arcname=f"{arc}/{path.name}")
                total += 1
    print(f"tar: {total:,} files, {tar_path.stat().st_size / 1e6:,.0f} MB")

    print("compressing (zstd -12 -T0) ...", flush=True)
    subprocess.run(["zstd", "-12", "-T0", "-f", "-q", str(tar_path),
                    "-o", str(out)], check=True)
    tar_path.unlink()
    print(f"done: {out} — {out.stat().st_size / 1e6:,.0f} MB")
    return out


def verify(archive: Path) -> int:
    """Extract and prove the archive rebuilds the panel it is supposed to.

    Publishing an archive nobody has round-tripped is how you discover on the
    day you need it that it was missing a directory.
    """
    import tempfile

    expected = discover([REPO / "data" / "raw" / "nse-data-bank" / "data",
                         REPO / "data" / "raw" / "backfill"])
    with tempfile.TemporaryDirectory() as tmp:
        print("extracting ...", flush=True)
        subprocess.run(["tar", "--use-compress-program=unzstd", "-xf",
                        str(archive), "-C", tmp], check=True)
        got = discover([Path(tmp)])

    print(f"  sessions in source  : {len(expected):,}")
    print(f"  sessions in archive : {len(got):,}")
    if set(expected) != set(got):
        missing = sorted(set(expected) - set(got))[:5]
        print(f"MISMATCH -- missing {len(set(expected) - set(got))} dates, "
              f"e.g. {missing}", file=sys.stderr)
        return 1

    # Precedence: the repaired file must still win where both sources have one.
    repaired = {d for d in discover([REPO / "data" / "raw" / "backfill"])}
    overlap = repaired & set(discover(
        [REPO / "data" / "raw" / "nse-data-bank" / "data"]))
    wrong = [d for d in overlap if "2-backfill" not in str(got[d])]
    print(f"  contested dates     : {len(overlap)} "
          f"({'backfill wins all' if not wrong else f'WRONG for {wrong[:3]}'})")
    return 1 if wrong else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, required=True,
                    help="destination .tar.zst")
    ap.add_argument("--verify", action="store_true",
                    help="round-trip the archive and check it rebuilds")
    args = ap.parse_args()

    out = build(args.out)
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    print(f"sha256: {digest}")

    if args.verify and verify(out) != 0:
        return 1
    print(f"\nPublish with:\n"
          f"  gh release create market-archive-v1 {out} \\\n"
          f"    --title 'Market data archive' --notes 'raw NSE bhavcopies'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
