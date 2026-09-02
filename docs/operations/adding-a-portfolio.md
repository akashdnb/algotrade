# Adding a portfolio or a strategy

Two very different jobs. Adding a **portfolio** is one command. Adding a
**strategy** is about a day of work in five specific places, and until that work
is done the system refuses the attempt rather than getting it wrong.

---

## Another portfolio on the same strategy — one command

```bash
python -m jobs.create_portfolio \
  --portfolio-id paper_momentum_v1_002 \
  --label "Momentum 30 (half capital)" \
  --capital 100000
```

Everything downstream discovers it with no further changes:

| Discovers it via | How |
|---|---|
| Monthly rebalance | `generate_recommendations --check-only` returns every active portfolio that is due; `rebalance.yml` matrices over them |
| Daily valuation | `value_portfolio --all-active`, sharing one panel load |
| Reconciliation ledger | Its own `job_runs` history under `value_portfolio:<portfolio_id>` |
| Dashboard | The portfolio picker lists every active portfolio |
| Telegram | **Nothing.** Only `DEFAULT_PORTFOLIO_ID` is reported daily, by design |

Verified twice on 2026-09-02, when `shadow_momentum_v1_001` was added this way.

`create_portfolio` is idempotent, and it is an **error rather than a no-op** if
an existing portfolio disagrees about capital or strategy params — silently
accepting that would let a track record be reset by a typo.

### For a shadow, do not use this

A shadow must start from the same book, not from cash. Use
`jobs/fork_portfolio.py` — see [shadow-portfolio.md](shadow-portfolio.md).

---

## A second strategy — not yet, and it fails closed

### What already supports it

More than you would expect. The data model was built for this:

- **Portfolio ids encode the strategy**: `^(paper|live|shadow)_[a-z]+_v\d+_\d{3}$`,
  enforced in `domain/ids.py`.
- **Signals are keyed `(strategy_version, session)`** — the repository's own
  docstring says *"shared by every portfolio on that version"*.
- **Portfolios carry their own** `strategy`, `strategy_version` and
  `strategy_params_hash`; nothing assumes they agree with each other.
- **`core/features.py` is a registry of ten features** — `momentum`,
  `reversal`, `realised_vol`, `vol_contraction`, `distance_from_high`,
  `overnight_gap`, `delivery_pct`, `delivery_shock`, `volume_shock`,
  `vwap_deviation` — and the look-ahead detector runs over **all** of them, so
  a new feature cannot be added without being checked.

### What blocks it

One line:

```python
# algotrade/config/settings.py
strategy_config: Path = REPO_ROOT / "config" / "strategy" / "momentum_v1.yaml"
```

A single path, loaded as *the* global spec by six jobs
(`generate_signals`, `generate_recommendations`, `daily_report`,
`create_portfolio`, `publish_runtime`, `acceptance/status`).
`config/strategy/` contains exactly one file. The research layer is generic;
the production layer collapsed it to one strategy.

### The five changes

1. **`StrategySpec` is momentum-shaped.** Its fields are `lookback` and `skip`,
   and `SignalService.generate` hard-codes
   `features.add(panel, "momentum", ...)` followed by
   `.sort("feature", descending=True)`. A low-volatility strategy ranks
   *ascending*. This needs a feature name, a parameter dict and a sort
   direction on the spec. **This is the only real design work in the list.**
2. **A registry keyed by version** instead of one path — load
   `config/strategy/*.yaml` and resolve by `strategy_version`.
3. **`generate_signals`** iterates the distinct strategy versions of active
   portfolios rather than generating for the single global spec.
4. **`generate_recommendations`** — including the frozen-strategy gate — loads
   the spec belonging to *that portfolio's* version.
5. **`daily_report`, `acceptance/status`, `publish_runtime`** — same
   substitution.

### Until then it refuses rather than guesses

Create `paper_meanrev_v1_001` today and the monthly rebalance stops with:

```
FROZEN STRATEGY MISMATCH
  portfolio was created with sha256:...
  config now hashes to      sha256:...
```

and exits 1. That gate exists to catch an edited config; it catches this too.
The portfolio would not quietly trade momentum signals under a mean-reversion
name.

**Fixed 2026-09-02:** the due-ness check used to evaluate every portfolio
against a single signal calendar, which would have judged a mean-reversion book
by momentum's month with no error raised. `RecommendationService.due_portfolios`
now reads each portfolio's own `strategy_version` from its document and looks
the calendar up per strategy — once per version, not once per portfolio, since
several books share one. A strategy with no stored signals is reported as not
due rather than raising, so one unbuilt strategy cannot remove every other
portfolio from the month's run.

---

## The question the code cannot answer

Two strategies on one account turns a 30-name book into 60, and ₹2,00,000 into
₹1,00,000 a side — roughly ₹3,300 a position. The edge is **mid-cap
concentrated, exactly where PROJECT.md §11i says the slippage model is least
tested**, and §11o's ~4× headroom is computed for the undivided book.

So:

- **A sensitivity portfolio** (`variant_a`: same capital, one parameter
  changed, no real money) costs nothing and answers a real question about
  parameter fragility. This is what §9's table anticipated.
- **Splitting live capital across two strategies** is an allocation decision
  governed by `INVESTMENT-POLICY.md`, not by whether the code supports it.

The code being ready will not make it the right move.
