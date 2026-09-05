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
| `.github/workflows/rebalance.yml` | Shadow auto-fill + no alert for it. Applied 2026-09-04 |
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

## Left open on the day, and how each closed

**`rebalance.yml` was not edited** — the tool classifier denied it twice, so
the shadow would have received a recommendation each month and never filled it,
silently becoming a buy-and-hold book. That is a worse control group than none:
it looks like a measurement and is not one. **Applied 2026-09-04**, and
exercised end to end in the 2026-09-05 rehearsal — 20 instructions auto-filled,
50 executions, reconciliation clean.

**The discretion/slippage split was not built.** `realised_slippage_bps`
computed half of it and nothing consumed it. **Built 2026-09-05**: the shadow
fills at the reference price, so its own slippage is zero by construction and
the whole component belongs to the traded book. First live numbers, from the
rehearsal: gap −0.6181% = slippage −0.0329% + discretion −0.5853%.

**A latent bug I introduced.** `_check_only` called
`signals.latest(spec.version)` once and evaluated every active portfolio
against that single signal calendar — correct while every portfolio is
`momentum_v1`, wrong and *silent* the moment a second strategy exists.
**Fixed 2026-09-02** by `RecommendationService.due_portfolios`, which reads
each portfolio's own `strategy_version` and looks the calendar up once per
version.

Worth keeping the contrast that made it worth writing down: every *other*
single-strategy assumption in this system fails **loudly** — the frozen-hash
gate stops the rebalance outright. That one would have failed quietly.

## State

The two books were identical at the fork, as they must be. The paper book's
first hand-executed rebalance is what makes the number mean anything, and the
dashboard says so rather than reporting zero as a finding: *"an absence of
evidence, not evidence that discretion is free."*

(The 13 shared sessions this originally reported became 12 on 2026-09-05, when
four pre-inception snapshots were deleted as fabricated — see
[`2026-09-05-snapshot-inception-and-peak.md`](2026-09-05-snapshot-inception-and-peak.md).)
