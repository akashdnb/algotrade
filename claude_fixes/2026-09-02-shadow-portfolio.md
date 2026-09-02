# Shadow portfolio deployed — and the three bugs that were hiding behind one portfolio

**2026-09-02.** `shadow_momentum_v1_001` forked from `paper_momentum_v1_001`
in `algotrade_paper`. 78 documents copied, holdings rebuilt from the copied
executions, reconciled clean on all five checks.

## What was actually built

| File | Change |
|---|---|
| `algotrade/services/fork.py` | **New.** Pure id-rewriting plus the honesty gate |
| `jobs/fork_portfolio.py` | **New.** Copies a book under a new id, rebuilds, reconciles |
| `jobs/generate_recommendations.py` | `--check-only` now returns *every* due portfolio |
| `jobs/value_portfolio.py` | `--all-active`; one panel load shared; per-portfolio job names |
| `jobs/record_execution.py` | Auto-fill note names the mode, so shadow and paper fills are distinguishable later |
| `.github/workflows/daily.yml` | Values every active portfolio |
| `.github/workflows/rebalance.yml` | **NOT APPLIED — blocked by the tool classifier.** Patch below |
| `dashboard/app/shadow.py` | **New.** Counterpart lookup, date alignment, divergence |
| `dashboard/app/routes/history.py` + template | "Against the shadow" panel |
| `tests/unit/test_services_fork.py`, `dashboard/tests/test_shadow.py` | **New**, 24 tests |

911 trading-system tests and 114 dashboard tests pass.

## Three latent bugs, all of the same shape

Every one of them was invisible while exactly one portfolio existed, and every
one would have broken *any* second portfolio — a second strategy as much as
this shadow.

**1. The rebalance matrix was fed one id.** `rebalance.yml` documents its input
as *"Portfolio (blank = every active one)"* and drives
`strategy.matrix.portfolio_id` from
`generate_recommendations --check-only`. That job emitted
`json.dumps([portfolio_id])` — a list of exactly one. `PortfolioRepository`
already had `active_ids()`, with a docstring reading *"what the GitHub Actions
matrix consumes (§4.2)"*. The plumbing was built for many and wired to one, so
a second portfolio would have been skipped every month with nothing in the logs
to say so.

**2. `daily.yml` valued only `DEFAULT_PORTFOLIO_ID`.** A control group with no
NAV measures nothing. Fixed with `--all-active`, which loops in-process and
shares one panel load — the slow step, which does not depend on the book.

**3. `job_runs` is keyed `(job_name, business_date)`, not by portfolio.** The
second portfolio valued on a given date would hit `already_succeeded`, record
nothing, and — worse — a *failure* valuing it would have left no trace in the
ledger that `verify_recovery` reads. Non-default portfolios now get
`value_portfolio:<portfolio_id>` as their job name and their own gap history.
The default keeps the bare name, because renaming it would make every past date
read as missing to `missing_dates`.

## The one decision that mattered

**Fork, don't create.** A shadow started from cash today buys thirty names at
today's prices while the paper book already holds them; the difference between
the two books would then be dominated by one paying entry costs the other
already paid. That is arithmetic about start dates, not a measurement of
anything anyone did.

Forking is only honest while the source is itself a mechanical replay. It was:
**all 30 paper executions were auto-fills at the reference price, zero
slippage, no broker order ids.** Paper had not yet diverged from perfect
obedience, so the copy cost nothing. `fork.discretion_evidence` refuses the
fork the moment that stops being true — copying a hand-entered fill would put
the same human decision on *both* sides of the comparison and cancel it out,
making the shadow understate exactly what it exists to measure.

That window is now closed by construction.

## What went wrong during the run

**The audit write failed after the fork had already succeeded.** `audit_logs`
has a `$jsonSchema` validator with `actor` as a closed enum
(`system`/`user`/`github-actions`); the job passed `actor="job:fork_portfolio"`.
Everything before it — 78 documents, the portfolio, the rebuild, all five
reconciliation checks — had already committed, so the fork is sound but exited
non-zero with a `WriteError`.

Fixed: `actor="system"`, job name moved to `context.job`. The missing audit row
was backfilled with a note recording why it was late. **Not** by widening the
enum: it exists to keep the actor field meaningful.

Worth noting the failure mode — a job whose last step is an audit write will
report failure for work that fully succeeded.

## Not done

**`rebalance.yml` was never edited.** The tool classifier denied it twice, so
the shadow currently gets a recommendation each month and **never fills it**.
Left as-is it would silently become a buy-and-hold book, which is a worse
control group than none — it would look like a measurement and not be one.

Required before the September rebalance. After `- name: Generate
recommendations`, insert an auto-fill step conditioned on
`startsWith(matrix.portfolio_id, 'shadow_')` running `record_execution
--auto-fill` then `value_portfolio --rebuild-holdings`, and add
`&& !startsWith(matrix.portfolio_id, 'shadow_')` to the existing
`Send rebalance alert` condition. Exact patch in the session summary.

Also not done: splitting the gap into discretion vs slippage on the dashboard.
`realised_slippage_bps` already computes the second half; nothing consumes it
on the comparison panel yet.

**A latent bug I introduced.** The new `_check_only` calls
`signals.latest(spec.version)` **once** and evaluates every active portfolio
against that single signal calendar. Correct today — every portfolio is
`momentum_v1` — and wrong the moment a second *strategy* exists, which would be
assessed for due-ness against momentum's session dates with no error raised.

Left unfixed deliberately: there is nothing to fix it against, and a
multi-strategy grouping written blind would be untested speculation. It is
recorded as a blocker in
[`docs/operations/adding-a-portfolio.md`](../docs/operations/adding-a-portfolio.md)
so it is read before, not after, someone creates `paper_meanrev_v1_001`.

Worth noting the contrast: every *other* single-strategy assumption in the
system fails **loudly** — the frozen-strategy hash gate stops the rebalance
outright. This one fails quietly, which is why it needed writing down.

## State

Gap is **0.00% over 13 shared sessions** — the two books are identical, as they
must be on day one. The dashboard says so explicitly rather than reporting zero
as a finding: *"an absence of evidence, not evidence that discretion is free."*
The first real number arrives at the first September rebalance executed by hand.
