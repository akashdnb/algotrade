# The shadow portfolio

> **Status: LIVE since 2026-09-02.** `shadow_momentum_v1_001` exists in
> `algotrade_paper`, forked from `paper_momentum_v1_001`, reconciled clean on
> all five checks. The two books are currently **identical** — see
> "What it says today".

The backtest assumes every signal is taken. You will not take every signal.
The shadow is the book that does, so the difference between them is a number
rather than a feeling.

- `paper` vs `shadow` = **what your discretion and your fills cost**
- `shadow` vs NIFTYBEES = **whether the strategy justifies itself at all**

Design §3.2. It is one of the ten acceptance criteria (§7) and one of the
pieces of evidence blocking scale-up.

---

## What it is

A second portfolio, same strategy, same frozen params hash, that **auto-fills
every instruction at the reference price** the moment the recommendation is
generated. Nobody decides anything on it. Ever.

That obedience is the entire measurement. The instant you skip a trade, delay
one, or get a worse price than the recommendation assumed, the two books part
and the gap is the cost.

---

## Why it was forked rather than created

A portfolio created from cash today would have bought all thirty names at
today's prices while the book beside it already held them. The difference
between the two would then be dominated by one book paying entry costs the
other had already paid — arithmetic about the start date, not about anything
you did.

So `jobs/fork_portfolio.py` copies: every rebalance run, recommendation,
execution, ledger entry and snapshot, re-keyed to the new portfolio id.
Holdings are **not** copied — they are rebuilt from the copied executions and
then reconciled, which is what makes a successful fork proof that the copy
reproduces the source book rather than merely resembling it.

### The window this depended on, and why it is now closed

Copying is only honest while the source book is itself a mechanical replay of
what the system instructed. On 2026-09-02 it was: all 30 paper executions were
auto-fills at the reference price, with no broker order ids and no slippage.
Paper had not yet diverged from perfect obedience, so the fork was free — the
shadow started as an exact copy and lost nothing.

`fork.discretion_evidence` enforces this and **refuses** a fork once the source
contains any fill with a broker order id or a price away from the reference.
Copying a hand-entered fill would put the same human decision on both sides of
the comparison and quietly cancel it out, making the shadow understate exactly
what it exists to measure.

`--allow-divergent-source` overrides the refusal. If you use it, the gap is
only meaningful from the fork date forward, not from inception.

---

## How it runs

| When | What happens |
|---|---|
| Every weekday, `daily.yml` | `value_portfolio --all-active` values **every** active portfolio from one shared panel load |
| Month end, `rebalance.yml` | The matrix now covers every due portfolio; the shadow generates a recommendation like any other |
| Immediately after that | The shadow auto-fills at the reference price and re-values. **No Telegram alert** — an alert asking you to act on a book you must never touch is noise |

Three things had to change for that to be true, and they are worth knowing
because each was a latent bug for any second portfolio, not just this one:

1. **`generate_recommendations --check-only` emitted a single portfolio id.**
   `rebalance.yml` has advertised *"blank = every active one"* and driven a
   matrix from that output since it was written — but the job fed it one id.
   A second portfolio would have been skipped every month with nothing in the
   logs to say so. It now returns every active portfolio that is due.
2. **`daily.yml` valued only `DEFAULT_PORTFOLIO_ID`.** A control group with no
   NAV measures nothing.
3. **`job_runs` is keyed `(job_name, business_date)`.** Two portfolios valued
   on the same date collided: the second read as "already succeeded", recorded
   nothing, and a failure valuing it would have left no trace at all.
   Non-default portfolios now get their own job name,
   `value_portfolio:<portfolio_id>`, and their own gap history. The default
   keeps the bare name so its existing rows — and `missing_dates`, which
   queries by name — stay continuous.

---

## Reading it

The dashboard's **History** page grows an "Against the shadow" panel whenever
the active portfolio has a counterpart. Both lines are **unit NAV rebased to
100 at the first session they share**.

Three deliberate choices in that sentence:

- **Unit NAV, not NAV.** A contribution buys units at the prevailing price and
  does not move unit NAV, so the books stay comparable even if funded
  differently. Comparing NAV would report a deposit as outperformance.
- **Rebased, not dual-axis.** Two y-scales can be chosen to show any
  relationship you like.
- **From the first shared session, not from inception.** A shadow forked today
  has had no time to differ; dating the comparison to inception would blame
  discretion for a history it was never involved in.

### The gap is two things, and the page separates them

`paper − shadow` is **discretion *and* slippage together** — a decision and the
market, which are not the same thing and should never be reported as one
number called "the cost of your judgement".

The separation works because **the shadow's own slippage is exactly zero by
construction**: it fills at the reference price, so any difference between the
prices in the two books belongs entirely to the one you trade. `slippage_bps`
is recorded on every execution, so summing it over the shared window and
dividing by the opening NAV puts it in the same units as the gap. What is left
is discretion:

> Of that −2.10% gap, **−0.35%** is slippage — the prices you got rather than
> the ones the recommendation assumed — and **−1.75%** is discretion: the
> trades you declined, delayed or resized.

**An attribution, not an identity.** It ignores compounding and uses the
opening NAV as the single denominator, so the two parts sum to the gap by
construction rather than because the arithmetic of the two books says they
must. Good to about a basis point over a month; the page says so and it should
not be read further.

Nothing is shown until a real fill exists. With every execution still at its
reference price the split is 0/0/0, and printing that would suggest discretion
had been measured and found free.

### What it says today

Twelve shared sessions (2026-08-20 to 09-04), both books returning +1.1770%,
gap **0.00%**, and the panel explicitly refuses to call that a measurement:

> The two books are still identical: nothing has been filled away from the
> instruction yet, so there is no divergence to report. A gap of zero here is
> an absence of evidence, not evidence that discretion is free.

The first real number arrives at the **first September rebalance you execute by
hand**. Until then the panel is a wiring check.

---

## Operating it

```bash
# Is the shadow keeping up?
python -m jobs.value_portfolio --all-active --reconcile-only

# Which portfolios does the monthly matrix see?
python -m jobs.generate_recommendations --check-only

# Fork again from scratch (drop the shadow's documents first)
python -m jobs.fork_portfolio --into shadow_momentum_v1_001 --dry-run
```

### Rules

- **Never record a fill against the shadow by hand.** Not once, not to fix
  something. A shadow with a human decision in it is not a control group, and
  there is no way to tell afterwards which fills were yours.
- **Do not give it a different contribution schedule.** Unit NAV makes the
  comparison survive that, but there is no reason to spend the robustness.
- **If it fails to value, the paper book still does.** `--all-active` isolates
  each portfolio: the shadow is valued last and a failure there is reported and
  skipped rather than aborting the run. The exit code still carries it.

### Deleting it

Dropping the shadow means deleting its documents from `rebalance_runs`,
`recommendations`, `executions`, `cash_transactions`, `portfolio_snapshots`,
`holdings` and `portfolios` — all keyed by `portfolio_id`. Nothing else
references it. Note that a re-fork afterwards will be **refused** if paper has
diverged in the meantime, which is the gate working as intended.
