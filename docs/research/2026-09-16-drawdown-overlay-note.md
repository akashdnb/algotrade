# The note that prompted §11s

**Source material, kept verbatim.** This is the outside commentary that raised
the drawdown-overlay question, preserved because the provenance of a research
question is worth as much as its answer — and because two of its claims were
checkable and one of them decided how the work was scoped.

**The answer is [`PROJECT.md` §11s](../../PROJECT.md), not here.** In short: no,
there is no overlay that buys drawdown reduction cheaply. Vol targeting cuts
drawdown in near-exact proportion to average exposure (gap ≤1.1pp across a
31-point range) and charges 2.2pp of CAGR in turnover for a benefit proportional
sizing gives free. The 35–40% band asked for below *is* reachable, at more than
half the CAGR, and still finishes below the index.

Three things about the note, read against this project's own record:

* **"Risk-managed versions can cut drawdowns roughly in half" — true here, and
  it does not help.** Vol targeting at 8% takes −53.2% to −21.3%. It also takes
  CAGR from 21.3% to 5.9%, and −6.0% versus the index.
* **The three overlays it names were already tested.** §11g killed the
  stock-level 200-DMA filter and the weighting schemes; §11h found the market
  regime filter's Sharpe lift was an artefact and R2 closed it as failed. Only
  vol targeting had ever moved drawdown, and its measurement predated two engine
  corrections — which is the one gap §11s actually closes.
* **The figures it quotes are mixed.** −53.1% is the momentum sleeve; 18.3%
  after tax is closer to the deployed 50/50 allocation, whose drawdown is
  −41.5%. Comparing one system's drawdown against another's return flatters
  neither.

It was also written without knowledge of the pre-registration discipline: the
holdout has five looks for the entire project, and §11 already records that the
2022–2026 window is compromised **for drawdown-shape statistics specifically**.
That is exactly the statistic in question, so the work below the line could only
honestly be answered on the dev set — which is how §11s ran it.

---

## Original note, unedited

Momentum is a good factor. A specific implementation of momentum can still be very painful.

Your result is not inconsistent with the academic literature.

What academic research says

The classic Indian momentum factor research found:

Annualized return ≈ 17.3%
Worst drawdown ≈ -48.5%
Longest drawdown ≈ 2.4 years

and explicitly notes that momentum crashes after major market recoveries.

Compare that to your system:

Metric	Indian Momentum Research	Your System
Return	~17.3%	18.3% after tax
Max DD	-48.5%	-53.1%
Longest DD	2.4 years	~3.0 years

Your results are actually in the same ballpark, not wildly worse. roughly double Sharpe versus a static implementation.

Recent CFA Institute research notes that traditional price momentum has experienced drawdowns as large as -88%, and that risk-managed versions can cut drawdowns roughly in half.

The key distinction

There are really three categories:

Strategy	Typical Drawdown
Pure stock momentum	40–60%+
Risk-managed stock momentum	20–40%
Multi-asset trend following	10–30%

Supported by multiple studies.

Your system is in the first bucket.

What surprises me about your result

Not the drawdown.

The thing that stands out is that your project had a pre-registered target:

Max drawdown < 20% a high-volatility equity strategy. fitting,

execution assumptions.

Ironically, many genuine momentum strategies look exactly like yours: strong returns, ugly crashes, long periods of regret. That's why AQR and other quant firms spend enormous effort on risk management overlays rather than abandoning momentum itself.

The real question isn't "Is -53% abnormal?"

The real question is:

Can you add a simple overlay (200-DMA filter, volatility targeting, market regime filter, position sizing changes, etc.) and reduce drawdown to ~35-40% without destroying the 18.3% CAGR?

That's where I'd focus next, because that's exactly the problem professional momentum managers have spent the last 20 years trying to solve.