# Fifteen years of market data exist in exactly one place: this laptop

**2026-09-04.** Found while trying to run the BE comparison over the full
history. Not fixed — publishing 745 MB of market data is a decision, not a
cleanup.

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

## The fix, which needs a decision first

The code path already exists and works; the release was simply never created.

```bash
tar --use-compress-program='zstd -19' -cf archive.tar.zst \
    -C data/raw/nse-data-bank/data . -C data/raw/backfill .
gh release create market-archive-v1 archive.tar.zst \
    --title "Market data archive 2010-2023" --notes "..."
```

**Decide before running it:** whether `akashdnb/algotrade` is public, and
whether NSE bhavcopy redistribution is something to do under this account. If
the repo is public this publishes 745 MB of exchange data under your name. A
private release, or an S3/R2 bucket with the URL in a secret, avoids that
question entirely — `daily.yml` would need a few lines changed either way.

Whatever the destination, **the raw CSVs must be preserved rather than the
curated Parquet.** Curated is EQ-filtered and adjusted; archiving it would
freeze both of those decisions forever and make questions like the BE one
permanently unanswerable for history. It is the reason
[`2026-09-04-be-series-measured.md`](2026-09-04-be-series-measured.md) could be
answered at all.
