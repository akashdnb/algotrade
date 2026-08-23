# Production Checklist — paper to real money

Everything required to move from `paper_momentum_v1_001` to a live portfolio
with manual trade approval.

**This checklist is deliberately hard to pass.** PROJECT.md records four audits
and ~55 defects, every one of which produced a number that looked plausible.
The gates below exist because plausible is not the same as correct.

---

## 0. Before anything: the evidence gates

These are from PROJECT.md §11o and are **not** engineering tasks. No amount of
code quality substitutes for them.

- [ ] **6+ months of paper trading completed.** Not 10 sessions — that proves
      the plumbing. Six months exposes month-end rebalances, corporate actions,
      holidays, and at least one drawdown.
- [ ] **Real fills measured at ₹25k.** The slippage model has never met a real
      order. §11o P2 gives 4× headroom over the modelled 30 bps, which is why
      starting small is safe — but it must be *measured* before scaling.
- [ ] **Realised slippage inside the model.** `executions.realised_slippage_bps`
      over the paper period should sit inside 30 bps round trip. If it does
      not, the cost case that justified the strategy is wrong.
- [ ] **The never-sell rule written down.** §11p priced quitting during an
      underperformance streak at **27–40% of terminal wealth**. Write the rule
      before you need it, because you will not write it at −40%.
- [ ] **A drawdown tolerance chosen deliberately.** The hypothesis fails its own
      pre-registered `max_drawdown_max = 0.20` gate (momentum's is −53.1%,
      50/50's −41.6%). Record the number you will actually tolerate, and record
      that the original gate failed.
- [ ] **Accept the underperformance streak.** §11o P5: 1,203 days behind NIFTY
      in the worst historical case, and **dilution does not shorten it**
      (1,203 → 1,196 across every allocation). It can only be made shallower.

> If any box above is unticked, stop. The rest of this document is about
> whether the *system* is ready, which is the easier half.

---

## 1. Data integrity

- [ ] Corporate actions re-validated against `nsefin` 1.0.0 (PROJECT.md §12 item 3)
- [ ] **Corporate actions applied to LIVE holdings** — `io/actions.py` adjusts
      the historical panel, but a 1:2 split doubling your actual share count is
      a separate daily step that **does not exist yet**
- [ ] Delisting handling verified on a real delisting
- [ ] Signal parity green: `pytest tests/integration/test_signal_parity.py`
- [ ] ETF/fund exclusion reviewed — `suspected_funds()` flags are checked, not ignored

## 2. Database

- [ ] `algotrade_prod` created and `init_db --verify` clean
- [ ] Atlas user has `dbAdmin` on the prod database (needed to alter a validator)
- [ ] **Weekly `mongodump` → GitHub Release running** and a restore rehearsed
      at least once. M0 has no automated backups; this is the only one that
      will exist
- [ ] Storage headroom confirmed (<50 MB of 512 MB)
- [ ] Atlas alerts configured for connection failures

## 3. Environment separation

- [ ] `ENVIRONMENT=prod` used only for live
- [ ] `MONGODB_DATABASE=algotrade_prod` — validation refuses a mismatch
- [ ] GitHub Environment `prod` with a **required reviewer**
- [ ] Secrets duplicated into `prod`, not shared with `paper`
- [ ] `guard_portfolio` verified: a `live_` portfolio outside prod is refused
      ```bash
      ENVIRONMENT=paper python -m jobs.create_portfolio \
        --portfolio-id live_momentum_v1_001   # must exit 2
      ```

## 4. Portfolio

- [ ] `live_momentum_v1_001` created with **real** starting capital
- [ ] `strategy_params_hash` matches `config/strategy/momentum_v1.yaml`
- [ ] Opening cash ledger entry recorded, balance verified
- [ ] Paper portfolio **left running** as the control arm — it costs nothing
      and is the only comparison you will have
- [ ] `shadow_momentum_v1_001` created: takes every recommendation blindly, so
      `live` vs `shadow` measures **the cost of your discretion** in rupees
      (§9). Unmeasurable without it.

## 5. Execution discipline

- [ ] Every fill recorded with its **real** `broker_order_id`
- [ ] Every skip carries a typed reason — the reasons are the dataset that
      measures whether discretion helps or hurts
- [ ] Charges entered from the contract note, not estimated
- [ ] `--confirm <set-id>` required for live (already enforced)
- [ ] Reconciliation run after every execution (already automatic)

## 6. Monitoring

- [ ] Separate healthchecks.io check for prod
- [ ] Separate Telegram channel for prod, so a live alert is never mistaken
      for a paper one
- [ ] Watchdog thresholds reviewed for live
- [ ] A failure injected and recovery observed **in the prod environment**

## 7. Operational readiness

- [ ] Runbook written: what to do for each reconciliation failure
- [ ] Tax-year export verified: `executions.between(start, end)` produces what
      your filing needs, with FIFO lots and STCG/LTCG classification
- [ ] Recovery rehearsed: delete 3 sessions, run `daily.yml`, confirm repair
- [ ] Restore rehearsed: drop a scratch database, restore from dump, verify
- [ ] Someone other than you can run the recovery procedures from the docs

## 8. Explicitly NOT in scope

Not oversights — deliberate exclusions:

| Excluded | Why |
|---|---|
| Automatic order placement | No code places orders. Manual approval is the control |
| Stop losses | Not in the validated strategy. Adding one makes it untested |
| Intraday anything | Signals use closes; the strategy is monthly |
| Leverage | §11g found market-neutral unavailable at ₹2 L; leverage is a different risk profile entirely |
| Position-level discretion | The backtest assumes every signal is taken. Overriding one makes you a different strategy |

---

## 9. The sizing decision

§11o deploys **50% momentum / 50% NIFTYBEES**, with NIFTYBEES held manually
outside this system.

- [ ] The momentum sleeve is **half** the account, not all of it
- [ ] NIFTYBEES bought separately and **not** traded by this system
- [ ] You understand why: return per unit of max drawdown is flat at 0.34 from
      100% down to 50%, so the first half of the dilution is free on a
      risk-adjusted basis — while halving the depth of the shortfall during the
      three-year streak that decides whether you stay invested

**The honest reason for not deploying 100% is behavioural, not statistical.**
§11o: *"I would rather hold half the position for ten years than the full
position for three."*

---

## 10. Recorded honestly

Carried from PROJECT.md §11o so it is not lost between documents:

**This hypothesis fails two of its own pre-registered gates** —
`max_drawdown_max = 0.20` and `oos_sharpe_min = 0.8`. It is being carried
forward on the judgement that the gates were written for a market-neutral book
that §11g proved unavailable at ₹2 L capital.

**Rewriting a gate after seeing the result is precisely what §8 exists to
prevent.** The defensible position is to record the original gates as failed,
state the new ones explicitly, and note that this hypothesis was never formally
passed — not to quietly adjust the threshold.

Odds of success were assessed at **~25–30%** (§13). Deploy accordingly.
