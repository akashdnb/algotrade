"""Pre-registration, trial counting and Sharpe deflation (PROJECT.md §8).

A Sharpe ratio is uninterpretable without a denominator. Test 100 configurations
on 5 years of data and **pure noise delivers a best-of Sharpe around 1.1-1.4** —
comfortably clearing a 0.8 gate. So "I found a Sharpe 1.3 strategy" means
nothing on its own: 1.3 from 5 trials is a discovery, 1.3 from 500 is nothing.

Almost nobody records the denominator, including to themselves. This module
makes it mechanical:

* **Registration before running.** Hypothesis, parameters and pass/fail gates
  are appended to `config/trials.jsonl` and committed. Git timestamps are what
  turn "I predicted this" from a story into a fact.
* **Append-only.** A committed append-only file cannot be quietly edited when
  the trial count becomes embarrassing.
* **Holdout budget enforced.** The holdout gets 3-5 looks for the *entire
  project* (§8.3). Exhaust it and this refuses, because a fourth look is not a
  fresh unbiased estimate — the deflation table starts applying to the holdout
  too.

The hard rule this encodes: **a candidate that fails the holdout is dead. You do
not adjust and retry.** That retry is how a rigorous-looking process quietly
produces a fake result.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from statistics import NormalDist
from typing import Final, Literal

DEFAULT_LOG: Final[Path] = Path("config/trials.jsonl")

Dataset = Literal["dev", "holdout"]

# §8.3: the whole project's budget, not per hypothesis.
HOLDOUT_LOOK_BUDGET: Final[int] = 5
EULER_GAMMA: Final[float] = 0.5772156649


@dataclass(frozen=True)
class Gates:
    """Pre-registered pass/fail criteria (§8.1). Set before seeing any result."""

    oos_sharpe_min: float = 0.8
    max_drawdown_max: float = 0.20
    rolling_3m_positive_min: float = 0.65
    beats_benchmark: bool = True
    survives_dropping_best_5: bool = True
    # Above this, treat the result as a bug report rather than a discovery.
    implausible_sharpe: float = 2.5


@dataclass
class Trial:
    """One registered experiment."""

    trial_id: int
    name: str
    hypothesis: str
    params: dict
    gates: dict
    dataset: Dataset
    registered_on: str
    alpha_decay_note: str = ""
    result: dict | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)


def _read(log: Path) -> list[dict]:
    if not log.exists():
        return []
    return [
        json.loads(line)
        for line in log.read_text().splitlines()
        if line.strip()
    ]


def trial_count(log: Path | str = DEFAULT_LOG, *, dataset: Dataset | None = None) -> int:
    """How many trials have been REGISTERED. The denominator for deflation.

    Counts only registration records. `record_result` appends its own line, so
    counting every row double-counted: the repo's own log held 2 registrations
    and 2 results and reported 4, inflating the noise threshold from 0.66 to 0.80
    at 5 years -- in the module whose entire purpose is that denominator.
    """
    rows = [r for r in _read(Path(log)) if "name" in r]
    if dataset is None:
        return len(rows)
    return sum(1 for r in rows if r.get("dataset") == dataset)


def holdout_looks_remaining(
    log: Path | str = DEFAULT_LOG, *, budget: int = HOLDOUT_LOOK_BUDGET
) -> int:
    return max(0, budget - trial_count(log, dataset="holdout"))


class HoldoutExhausted(RuntimeError):
    """Raised when the holdout budget is spent (§8.3)."""


def register(
    name: str,
    hypothesis: str,
    params: dict,
    *,
    dataset: Dataset = "dev",
    gates: Gates | None = None,
    alpha_decay_note: str = "",
    registered_on: date | None = None,
    log: Path | str = DEFAULT_LOG,
    budget: int = HOLDOUT_LOOK_BUDGET,
) -> Trial:
    """Append a trial to the log. Call this *before* running anything.

    Args:
        alpha_decay_note: How fast this signal's alpha is expected to decay.
            Required in spirit for §11a's reason: a signal needing daily
            rebalancing is dead at ~30 bps and should not consume a trial.
        registered_on: Injected rather than read from the clock, so tests are
            deterministic.

    Raises:
        HoldoutExhausted: registering a holdout look with none left. A fourth or
            sixth look is not an unbiased estimate, so the refusal is the point.
    """
    log = Path(log)
    if dataset == "holdout" and holdout_looks_remaining(log, budget=budget) <= 0:
        raise HoldoutExhausted(
            f"holdout budget of {budget} looks is spent. Further looks are not "
            "unbiased estimates -- the deflation table starts applying to the "
            "holdout too (§8.3)."
        )

    trial = Trial(
        # Max over existing registrations, not a count: a count skipped ids once
        # result records were interleaved (the log read "1, 3" for two trials).
        trial_id=max(
            (r["trial_id"] for r in _read(log) if "name" in r), default=0
        ) + 1,
        name=name,
        hypothesis=hypothesis,
        params=dict(params),
        gates=asdict(gates or Gates()),
        dataset=dataset,
        registered_on=(registered_on or date.today()).isoformat(),
        alpha_decay_note=alpha_decay_note,
    )
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as fh:
        fh.write(trial.to_json() + "\n")
    return trial


def record_result(
    trial_id: int, result: dict, *, log: Path | str = DEFAULT_LOG
) -> None:
    """Append an outcome for an already-registered trial.

    Appended as a separate record rather than mutating the original line: the
    log stays append-only, so registration order remains provable.
    """
    log = Path(log)
    known = {r["trial_id"] for r in _read(log) if "trial_id" in r}
    if trial_id not in known:
        raise ValueError(f"trial {trial_id} was never registered")
    with log.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"trial_id": trial_id, "result": result}, sort_keys=True) + "\n")


def expected_max_noise_sharpe(
    n_trials: int, years: float, *, method: Literal["conservative", "gumbel"] = "conservative"
) -> float:
    """Best-of-N Sharpe expected from pure noise.

    Two estimators, deliberately both available:

    * `conservative` -- `sqrt(2 ln N / T)`, the form tabulated in §8.2. It
      overstates the maximum somewhat, which sets a *higher* bar.
    * `gumbel` -- the Gumbel-based expression used in the deflated-Sharpe
      literature. More accurate, and materially lower: for N=100 over 5 years it
      gives ~1.13 against the conservative ~1.36.

    The default is conservative on purpose. When the whole point is guarding
    against self-deception, an estimator that errs toward demanding more
    evidence is the right one.
    """
    if n_trials < 1 or years <= 0:
        return 0.0
    if n_trials == 1:
        return 0.0

    # Sampling std of an annualised Sharpe estimate near zero, over `years`.
    sigma = math.sqrt(1.0 / years)

    if method == "conservative":
        return math.sqrt(2.0 * math.log(n_trials) / years)

    normal = NormalDist()
    a = normal.inv_cdf(1.0 - 1.0 / n_trials)
    b = normal.inv_cdf(1.0 - 1.0 / (n_trials * math.e))
    return sigma * ((1.0 - EULER_GAMMA) * a + EULER_GAMMA * b)


@dataclass(frozen=True)
class Deflated:
    observed_sharpe: float
    n_trials: int
    years: float
    noise_threshold: float
    deflated_sharpe: float
    survives: bool

    def summary(self) -> str:
        verdict = "SURVIVES" if self.survives else "INDISTINGUISHABLE FROM NOISE"
        return (
            f"observed Sharpe   {self.observed_sharpe:>6.2f}\n"
            f"trials            {self.n_trials:>6,}\n"
            f"years of data     {self.years:>6.1f}\n"
            f"noise threshold   {self.noise_threshold:>6.2f}  "
            f"(best-of-{self.n_trials} from pure noise)\n"
            f"deflated Sharpe   {self.deflated_sharpe:>6.2f}\n"
            f"verdict           {verdict}"
        )


def deflate(
    observed_sharpe: float,
    *,
    n_trials: int,
    years: float,
    method: Literal["conservative", "gumbel"] = "conservative",
) -> Deflated:
    """Haircut a Sharpe by what the trial count alone could have produced.

    `deflated_sharpe` is the excess over the noise threshold. A result at or
    below zero is not evidence of anything, however good the raw number looks.
    """
    threshold = expected_max_noise_sharpe(n_trials, years, method=method)
    return Deflated(
        observed_sharpe=observed_sharpe,
        n_trials=n_trials,
        years=years,
        noise_threshold=threshold,
        deflated_sharpe=observed_sharpe - threshold,
        survives=observed_sharpe > threshold,
    )


def check_gates(metrics: dict, gates: Gates) -> dict[str, bool]:
    """Evaluate a result against pre-registered gates.

    `not_implausible` is inverted deliberately: an in-sample Sharpe above ~2.5
    at this frequency and cost structure indicates look-ahead, so a very high
    number *fails* rather than passes (§8.1).
    """
    sharpe = float(metrics.get("sharpe", 0.0))
    return {
        "oos_sharpe": sharpe >= gates.oos_sharpe_min,
        "max_drawdown": abs(float(metrics.get("max_drawdown", 1.0)))
        <= gates.max_drawdown_max,
        "rolling_3m_positive": float(metrics.get("rolling_3m_positive", 0.0))
        >= gates.rolling_3m_positive_min,
        "beats_benchmark": bool(metrics.get("beats_benchmark", False))
        or not gates.beats_benchmark,
        "survives_concentration": bool(metrics.get("survives_dropping_best_5", False))
        or not gates.survives_dropping_best_5,
        "not_implausible": sharpe <= gates.implausible_sharpe,
    }


def all_gates_pass(metrics: dict, gates: Gates) -> bool:
    return all(check_gates(metrics, gates).values())
