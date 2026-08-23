# algotrade

Systematic momentum trading for Indian equities. **Paper trading, deployed.**

The system recommends; a human executes. **No code here places an order**, and
that is a design decision, not a missing feature.

---

## What it does

Every trading day at 20:00 IST, GitHub Actions ingests NSE market data, ranks
the liquid-300 universe by 12-1 momentum, values the portfolio, reconciles the
book against its trade record, and sends a Telegram report. On the last session
of each month it generates a rebalance recommendation. You place the orders and
record the fills.

**Strategy:** 30-stock cross-sectional momentum, rank-buffered (entry 30 /
exit 60), monthly rebalance. Frozen — see `config/strategy/momentum_v1.yaml`.

**Allocation:** the system manages the momentum half of a 50/50
momentum/NIFTYBEES account. The NIFTYBEES half is held manually and is never
traded here (PROJECT.md §11o).

---

## Start here

| You want to | Read |
|---|---|
| **Decide whether to sell during a drawdown** | **[`INVESTMENT-POLICY.md`](INVESTMENT-POLICY.md) — read this first, and only this** |
| Understand why any of this | [`PROJECT.md`](PROJECT.md) — the research record |
| Run it day to day | [`docs/operations/runbook.md`](docs/operations/runbook.md) |
| Fix something broken | [`docs/operations/disaster-recovery.md`](docs/operations/disaster-recovery.md) |
| Know if it is trustworthy yet | [`docs/acceptance/10-session-gate.md`](docs/acceptance/10-session-gate.md) |
| Move to real money | [`docs/deployment/production-checklist.md`](docs/deployment/production-checklist.md) |
| Understand the architecture | [`docs/architecture/`](docs/architecture/) |

---

## Layout

```
algotrade/
  domain/         pure rules -- money, FIFO tax lots, sizing. No I/O, no pymongo.
  services/       orchestration -- signals, rebalance, execution, valuation
  repositories/   the ONLY place pymongo appears
  config/         typed settings, fail-fast validation, environment guards
  platform/       clock (IST), Telegram, dead-man switch
  ops/            backup/restore, health scoring
  acceptance/     10-session gate tooling
jobs/             thin CLI entrypoints; one per workflow step
.github/workflows/  daily, rebalance, record-execution, healthcheck, weekly-backup
data/sessions/    committed market data -- the append-only audit trail
data/runtime/     static JSON, the future dashboard's data source
```

Boundaries are enforced, not merely intended:
`tests/unit/test_domain_purity.py` fails the build if `domain/` imports a
database.

---

## Daily use

```bash
python -m algotrade.check_config          # connectivity and configuration
python -m algotrade.acceptance.status     # gate progress
python -m jobs.value_portfolio --reconcile-only
```

Recording a fill:

```bash
gh workflow run record-execution.yml \
  -f recommendation_id=paper_momentum_v1_001:2026-09:r1 \
  -f symbol=RELIANCE -f side=buy -f quantity=300 -f price=2402.75
```

---

## Two things worth knowing before trusting it

**The strategy fails two of its own pre-registered gates** —
`max_drawdown_max = 0.20` (actual −41.6%) and `oos_sharpe_min = 0.8` (actual
0.53). It is carried forward on a judgement call, recorded honestly in
PROJECT.md §11o rather than by quietly moving the threshold. Odds of success
were assessed at **~25–30%**.

**The hardest part is not technical.** §11p priced quitting during an
underperformance streak at **27–40% of terminal wealth**, and §11o found the
worst historical streak ran **1,203 days** — which dilution shortens by almost
nothing. The daily report shows drawdown and that historical worst side by
side, deliberately, so a bad stretch reads as normal rather than as breakage.

The commitments made in advance are in
[`INVESTMENT-POLICY.md`](INVESTMENT-POLICY.md). It is the most important file
in this repository, and the only one written to be read while frightened.

---

## Tests

```bash
pytest -q                                     # 860 unit tests
ALGOTRADE_TEST_MONGODB_URI='...' pytest tests/integration -q
pytest tests/integration/test_signal_parity.py -q   # the highest-value check
```

Signal parity — that the live signal reproduces the backtested one exactly —
has caught two real divergences. Run it after touching anything under
`core/`, `services/signals.py`, or `services/panel.py`.
