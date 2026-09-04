# Fifteen years of market data existed in exactly one place: this laptop

**2026-09-04. Found and fixed the same day.** Surfaced while trying to run the
BE comparison over the full history — see "What was done" at the end.

## What `daily.yml` believes

```yaml
- name: Download archive on cache miss
  if: steps.archive.outputs.cache-hit != 'true'
  continue-on-error: true
  run: |
    if gh release download market-archive-v1 --pattern 'archive.tar.zst' --dir /tmp; then
      ...
    else
      echo "::warning::no archive release; relying on committed sessions"
    fi
```

## What is actually there

```
$ gh release list
Database backups   Latest   backups   2026-08-30

$ gh cache list
market-archive-v1   192 B   2026-08-23
```

**No release. The cache is 192 bytes** — an empty directory marker, not
1.9 GB of CSVs. So on every cache miss the download fails, `continue-on-error`
swallows it, the warning scrolls past, and CI carries on with whatever is in
git.

## What is in git

`data/sessions` — **511 files, 2024 onward.**

The curated manifest names its real sources:

```json
"source_dirs": [
  ".../data/sessions",
  ".../data/raw/nse-data-bank/data",
  ".../data/raw/backfill"
]
```

`data/raw/nse-data-bank/data` (3,995 CSVs, 720 MB) and `data/raw/backfill`
(86 CSVs, 25 MB) are **not in git, not in the release, not in any cache.** They
are the entire 2010–2023 history, and they exist on one machine.

## Two consequences

**1. CI is already running on a shorter panel than the backtest.** The curated
Actions cache (41 MiB, refreshed daily) is what keeps the live system going. If
it is ever evicted — GitHub evicts caches after 7 days unused, and enforces a
10 GB repo ceiling — the next `--rebuild-panel` sees only the committed
sessions and rebuilds a **2024-onward** panel. Momentum needs 273 sessions of
warm-up, so a ~484-session panel leaves roughly ten months of usable signal. It
would keep running. The strategy YAML's `min_sessions: 320` would still pass.
Nothing would fail loudly.

**2. Every published number becomes unreproducible.** The −53.1% max drawdown,
the Sharpe figures, the pre-registered gate record — all computed on
2010–2026. Losing the laptop copy means never being able to re-derive any of
them, which for a project whose §9.2 requirement is *reproducibility* is the
sharpest version of this problem.

## What was done

**The release now exists.** `akashdnb/algotrade` is **private**, which settles
the redistribution question: the asset is visible only to the owner and
collaborators, so there is no public exchange-data publication to weigh.

`scripts/pack_archive.py` builds it — written as a script rather than a
one-liner because it will need re-running whenever the mirror grows, and
because the directory order is a correctness property rather than a detail:

```
1-nse-data-bank   3995 files
2-backfill          86 files
tar: 4,081 files, 778 MB
compressing (zstd -12 -T0) ... 247 MB
sha256: 2a9033c3aca7bc12a5e3dabe23a1305d4266715bfcaf706fb5dba689173baf0b
```

`--verify` extracts the result and re-runs `archive.discover` over it:

```
sessions in source  : 4,068
sessions in archive : 4,068
contested dates     : 13 (backfill wins all)
```

Those 13 are dates where the integrity guard rejected the mirror's file and
`backfill/` holds a repaired one. `discover` walks `sorted(rglob(...))` and
later wins, so the numeric prefixes are what keep the repaired file on top
through a flatten-and-repack. Getting that backwards would silently reinstate
thirteen known-bad sessions.

Published, then re-downloaded with the exact command `daily.yml` runs —
`sha256` matches.

### And the silent fallback is now loud

The download keeps `continue-on-error` (a transient blip should not fail a
run), but a new **Assert the archive actually restored** step fails the job if
`data/archive` holds fewer than 3,000 CSVs. That floor sits far below the
~4,000 the archive carries and far above anything the committed sessions alone
can produce, so it separates the survivable failure from the dangerous one.

### Still worth knowing

`rebalance.yml` restores the `data/archive` cache but has **no download step**,
so on a cache miss it has no archive. It does not rebuild the panel — it reads
the curated store — so it depends on the `curated-` cache instead. If that
misses too, `panel_service.load` raises rather than quietly truncating, which
is the acceptable direction, but it would stop the monthly rebalance.

**Raw CSVs, never the curated Parquet.** Curated is EQ-filtered and adjusted;
archiving it would freeze both decisions forever. Keeping the raw files is
exactly what made
[`2026-09-04-be-series-measured.md`](2026-09-04-be-series-measured.md)
answerable over fifteen years.
