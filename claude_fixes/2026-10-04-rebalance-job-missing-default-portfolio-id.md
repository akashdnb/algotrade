# The rebalance job has never actually run end-to-end -- and when it finally did, it failed

**Found 2026-10-04** while investigating the Telegram alert for run 36918818011.

## The claim

`rebalance.yml`'s `rebalance` job verifies the frozen strategy, generates
recommendations, and sends an alert -- for each portfolio in the matrix the
`check` job decided is due.

## What actually happened

Both matrix jobs (`paper_momentum_v1_001`, `shadow_momentum_v1_001`) failed at
the very first step, "Verify frozen strategy":

```
[FAIL] DEFAULT_PORTFOLIO_ID: not set
         -> e.g. paper_momentum_v1_001
```

`jobs/generate_recommendations.py:main()` calls `validation.validate(s)`
unconditionally, before it ever looks at `args.portfolio_id`. In
`ENVIRONMENT=paper`, `_check_portfolio()` (`algotrade/config/validation.py`)
raises a blocking error if `DEFAULT_PORTFOLIO_ID` is unset -- even on an
invocation like this one that passes `--portfolio-id` explicitly and never
reads the default.

The `check` job's env sets `DEFAULT_PORTFOLIO_ID: ${{ vars.DEFAULT_PORTFOLIO_ID }}`.
The `rebalance` job's env never did. That gap has existed since the config
validation layer was added (60372f1), but sat dormant: every single run of
the `rebalance` job before 2026-10-01 was skipped, because `check` returned
`due=false` every day since the workflow was written on 2026-09-14. The
Sept -> Oct period rollover made Oct 1 the first time the matrix actually
executed its steps -- and the first time this gate was exercised for real.

## The fix

Set `DEFAULT_PORTFOLIO_ID: ${{ matrix.portfolio_id }}` in the `rebalance`
job's env. Each matrix entry already knows its own portfolio id; this just
satisfies the validator with the same value the job already uses everywhere
else. No change to the Python validation logic -- `--portfolio-id` was always
the one actually driving portfolio resolution (`settings.py`'s
`default_portfolio_id` is pure fallback), so this doesn't change what runs,
only what passes config validation first.

## Lesson

A gate that nothing has ever exercised is not verified, it's untested. The
"checked daily, executed at most once per period" design (deliberately, to
survive a missed cron) also means the executing half of the pipeline can go
weeks without running for real, and any config drift introduced during that
window is invisible until the next period rolls over. Worth a smoke test that
runs the `rebalance` job's steps against a scratch portfolio without waiting
for a real due-ness window.
