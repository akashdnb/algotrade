"""Tests for pre-registration, trial counting and Sharpe deflation (§8)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from algotrade.backtest import registry as reg

D = date


@pytest.fixture
def log(tmp_path: Path) -> Path:
    return tmp_path / "trials.jsonl"


# --- registration ------------------------------------------------------------


def test_register_appends_and_numbers_trials(log: Path):
    a = reg.register("mom_v1", "12-1 momentum persists", {"lookback": 252}, log=log)
    b = reg.register("pead_v1", "post-earnings drift", {"window": 60}, log=log)
    assert (a.trial_id, b.trial_id) == (1, 2)
    assert reg.trial_count(log) == 2


def test_registration_captures_gates_and_hypothesis(log: Path):
    gates = reg.Gates(oos_sharpe_min=1.0)
    t = reg.register(
        "x",
        "a stated hypothesis",
        {"p": 1},
        gates=gates,
        alpha_decay_note="slow: monthly rebalance is fine",
        registered_on=D(2026, 8, 21),
        log=log,
    )
    stored = json.loads(log.read_text().splitlines()[0])
    assert stored["hypothesis"] == "a stated hypothesis"
    assert stored["gates"]["oos_sharpe_min"] == 1.0
    assert stored["registered_on"] == "2026-08-21"
    assert "monthly" in stored["alpha_decay_note"]
    assert t.result is None


def test_log_is_append_only(log: Path):
    """A committed append-only file cannot be quietly edited when the trial
    count becomes embarrassing."""
    reg.register("a", "h", {}, log=log)
    first = log.read_text()
    reg.register("b", "h", {}, log=log)
    assert log.read_text().startswith(first)


def test_result_is_a_separate_record(log: Path):
    """Registration order must stay provable, so results append rather than
    mutate the original line."""
    t = reg.register("a", "h", {}, log=log)
    reg.record_result(t.trial_id, {"sharpe": 0.9}, log=log)
    lines = log.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["name"] == "a"
    assert json.loads(lines[1])["result"]["sharpe"] == 0.9


def test_result_for_unregistered_trial_is_refused(log: Path):
    with pytest.raises(ValueError, match="never registered"):
        reg.record_result(99, {"sharpe": 1.0}, log=log)


def test_trial_count_on_missing_log(log: Path):
    assert reg.trial_count(log) == 0


# --- holdout budget ---------------------------------------------------------


def test_holdout_budget_is_tracked_separately(log: Path):
    reg.register("a", "h", {}, dataset="dev", log=log)
    reg.register("b", "h", {}, dataset="dev", log=log)
    assert reg.holdout_looks_remaining(log) == reg.HOLDOUT_LOOK_BUDGET
    reg.register("c", "h", {}, dataset="holdout", log=log)
    assert reg.holdout_looks_remaining(log) == reg.HOLDOUT_LOOK_BUDGET - 1


def test_dev_trials_are_unlimited(log: Path):
    """Exploration is not rationed; only confirmation is (§8.3)."""
    for i in range(50):
        reg.register(f"t{i}", "h", {}, dataset="dev", log=log)
    assert reg.trial_count(log, dataset="dev") == 50


def test_exhausted_holdout_budget_refuses(log: Path):
    """A sixth look is not a fresh unbiased estimate, so it is refused."""
    for i in range(reg.HOLDOUT_LOOK_BUDGET):
        reg.register(f"h{i}", "h", {}, dataset="holdout", log=log)
    with pytest.raises(reg.HoldoutExhausted, match="budget"):
        reg.register("one-too-many", "h", {}, dataset="holdout", log=log)


def test_budget_is_configurable(log: Path):
    reg.register("h0", "h", {}, dataset="holdout", log=log, budget=1)
    with pytest.raises(reg.HoldoutExhausted):
        reg.register("h1", "h", {}, dataset="holdout", log=log, budget=1)


# --- deflation: the §8.2 table ----------------------------------------------


@pytest.mark.parametrize(
    ("n_trials", "years", "expected"),
    [
        (5, 5, 0.80),
        (10, 5, 0.96),
        (100, 5, 1.36),
        (1_000, 5, 1.66),
        (10_000, 5, 1.92),
        (5, 10, 0.57),
        (100, 10, 0.96),
        (1_000, 10, 1.18),
    ],
)
def test_conservative_threshold_matches_the_documented_table(
    n_trials: int, years: float, expected: float
):
    """Reproduces the §8.2 table exactly -- the doc and the code must agree."""
    got = reg.expected_max_noise_sharpe(n_trials, years, method="conservative")
    assert got == pytest.approx(expected, abs=0.01)


def test_a_single_trial_has_no_haircut():
    assert reg.expected_max_noise_sharpe(1, 5) == 0.0


def test_gumbel_is_lower_than_conservative():
    """Gumbel is the more accurate estimator; conservative sets a higher bar.

    For N=100 over 5 years: ~1.13 against ~1.36.
    """
    n, years = 100, 5.0
    gumbel = reg.expected_max_noise_sharpe(n, years, method="gumbel")
    conservative = reg.expected_max_noise_sharpe(n, years, method="conservative")
    assert gumbel < conservative
    assert gumbel == pytest.approx(1.13, abs=0.05)


def test_threshold_rises_with_trials_and_falls_with_data():
    assert reg.expected_max_noise_sharpe(1_000, 5) > reg.expected_max_noise_sharpe(10, 5)
    assert reg.expected_max_noise_sharpe(100, 10) < reg.expected_max_noise_sharpe(100, 5)


def test_the_headline_case_from_the_doc():
    """§8.2's point: with 100 trials on 5 years, noise clears the 0.8 gate."""
    noise = reg.expected_max_noise_sharpe(100, 5)
    assert noise > reg.Gates().oos_sharpe_min


# --- deflate ----------------------------------------------------------------


def test_same_sharpe_survives_at_5_trials_and_dies_at_500():
    """A Sharpe is uninterpretable without its denominator."""
    few = reg.deflate(1.3, n_trials=5, years=5)
    many = reg.deflate(1.3, n_trials=500, years=5)
    assert few.survives
    assert not many.survives
    assert few.deflated_sharpe > many.deflated_sharpe


def test_deflated_sharpe_is_the_excess_over_noise():
    got = reg.deflate(2.0, n_trials=100, years=5)
    assert got.deflated_sharpe == pytest.approx(2.0 - got.noise_threshold)


def test_summary_states_the_verdict():
    assert "INDISTINGUISHABLE FROM NOISE" in reg.deflate(
        0.9, n_trials=1_000, years=5
    ).summary()
    assert "SURVIVES" in reg.deflate(1.5, n_trials=5, years=5).summary()


# --- gates ------------------------------------------------------------------


def good_metrics() -> dict:
    return {
        "sharpe": 1.1,
        "max_drawdown": -0.15,
        "rolling_3m_positive": 0.70,
        "beats_benchmark": True,
        "survives_dropping_best_5": True,
        "positive_fold_fraction": 0.80,
    }


def test_all_gates_pass_on_a_good_result():
    assert reg.all_gates_pass(good_metrics(), reg.Gates())


@pytest.mark.parametrize(
    ("field", "value", "failing_gate"),
    [
        ("sharpe", 0.5, "oos_sharpe"),
        ("max_drawdown", -0.40, "max_drawdown"),
        ("rolling_3m_positive", 0.40, "rolling_3m_positive"),
        ("beats_benchmark", False, "beats_benchmark"),
        ("survives_dropping_best_5", False, "survives_concentration"),
        ("positive_fold_fraction", 0.20, "stable_across_folds"),
    ],
)
def test_each_gate_can_fail(field: str, value, failing_gate: str):
    metrics = good_metrics() | {field: value}
    result = reg.check_gates(metrics, reg.Gates())
    assert not result[failing_gate]
    assert not reg.all_gates_pass(metrics, reg.Gates())


def test_an_implausibly_high_sharpe_fails(fail_msg="§8.1: treat as a bug report"):
    """A Sharpe of 4 does not pass -- at this frequency it means look-ahead."""
    metrics = good_metrics() | {"sharpe": 4.0}
    result = reg.check_gates(metrics, reg.Gates())
    assert result["oos_sharpe"]  # clears the minimum...
    assert not result["not_implausible"]  # ...but is rejected anyway
    assert not reg.all_gates_pass(metrics, reg.Gates()), fail_msg


def test_drawdown_sign_is_handled_either_way():
    for dd in (-0.15, 0.15):
        assert reg.check_gates(good_metrics() | {"max_drawdown": dd}, reg.Gates())[
            "max_drawdown"
        ]
