# The frozen-strategy gate froze the parameters, not the data

**2026-09-05.** Closed the gap that this week's work leaned on twice.

## What was guarded, and what was not

`StrategySpec.params_hash` content-addresses nine values — version, lookback,
skip, entry, exit, universe size, universe lookback, min turnover, min coverage
— and `generate_recommendations` refuses to trade when the config no longer
hashes to what the portfolio was created with. `momentum_v1.yaml` states the
doctrine plainly:

> Editing this file does not "update" the strategy. It forks one.

But the ranking does not run on those nine numbers. It runs on a **panel**, and
what enters that panel is decided in code the hash never saw:

| Input | Where it lives |
|---|---|
| Series filter, `("EQ",)` | `io/bhavcopy.read_file` default |
| ETF denylist | `universe.DEFAULT_EXCLUSIONS` |
| Equity allowlist | `universe.KNOWN_EQUITIES` |
| Fund-name suffixes | `universe._FUND_SUFFIXES` |

Change any of them and every signal changes with the gate showing green.

## It had already happened

`DEFAULT_EXCLUSIONS` carries its own note:

> Found live on 2026-08-20: three precious-metal ETFs had entered the top 60,
> two of them inside the entry threshold of 30.

The list was edited **during live operation**. That edit was right — metal ETFs
are not the equities this strategy is meant to rank. Nothing in the system
recorded that the ranking's inputs had moved, and nothing would have refused if
the edit had been wrong.

And this week nearly repeated it: the whole BE-series question was a proposal to
change the series filter. Had the 15-year measurement come out the other way and
the filter been changed, the frozen gate would have stayed green through it.

## What was added

`services.signals.pipeline_hash()` — a second content address, over what the
ranking can **see** rather than what it does. Compared beside `params_hash` in
`generate_recommendations`, including under `--verify-frozen`, which
`rebalance.yml` runs for every matrix entry before generating anything.

Two details that are not incidental:

- **Sets are sorted before hashing.** `frozenset` iteration order is not stable
  across processes, and a gate that fires at random is a gate that gets turned
  off. `series` keeps its given order — it is an ordered filter argument, not
  a set.
- **Adoption is announced, once.** Portfolios created before the gate existed
  have no stored value, so the first run stamps the current one and says
  `not a verified match` rather than printing OK. Stamping is safe only because
  it is the pipeline these books have been running all along, and that is a
  fact about today, not a property of the mechanism.

## Verified

```
--- paper_momentum_v1_001 (first run)
pipeline hash adopted: sha256:825d7c7fa53fcc7f (first run under this gate -- not a verified match)
frozen strategy OK: momentum_v1 sha256:8e028df68da43bdd
data pipeline   OK: sha256:825d7c7fa53fcc7f

--- paper (second run)
frozen strategy OK: momentum_v1 sha256:8e028df68da43bdd
data pipeline   OK: sha256:825d7c7fa53fcc7f
```

Both books adopted the same hash, as they should — same pipeline, same code.

**Scope of that verification, stated precisely:** the adoption and match paths
were exercised against the live book. The *refusal* path was not run
end-to-end; it is a string comparison between two values both now observed, and
the hash's sensitivity to each of the four inputs is covered by nine unit tests.

## What it still does not cover

The corporate-action adjustment, the integrity rejections, and the cost model
all shape the panel too and are not in this hash. They were left out because
each is either data-driven rather than configured, or already content-addressed
elsewhere (`panel_manifest_hash`, `cost_model_hash`). If one of them ever
becomes a knob someone turns, it belongs here.
