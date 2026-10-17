"""models/gate.py (v2 slice 6b, FR15/FR16): the pure rules on hand-built intervals (exact boundaries) and the full gate on
seeded synthetic test windows. No files, no MLflow."""

from __future__ import annotations

import numpy as np
import pytest

from readmission_risk.models.evaluate import cluster_resample_indices
from readmission_risk.models.gate import (
    ARMS,
    PROMOTE,
    REFUSE,
    RULE_NAMES,
    decide_gate,
    evaluate_frozen_v1,
    evaluate_gate,
    frozen_acceptance,
)

GATE_KW = {"n_resamples": 200, "seed": 20261012, "ci_level": 0.95, "brier_margin_fraction": 0.05}


def _differences(auc_champ=(0.01, 0.03), auc_lookup=(0.01, 0.03), brier_champ=(-0.001, 0.0001)):
    return {
        "challenger_minus_champion": {"roc_auc": auc_champ, "brier_score": brier_champ},
        "challenger_minus_reason_code_lookup": {"roc_auc": auc_lookup, "brier_score": (0.0, 0.0)},
    }


POINT = {"champion": {"brier_score": 0.02}}  # Brier threshold = 0.05 * 0.02 = 0.001


def _decide(**kwargs):
    return decide_gate(_differences(**kwargs), POINT, brier_margin_fraction=0.05, ci_level=0.95)


def test_all_rules_pass_promotes_and_statistics_name_the_ci_level():
    rules, decision = _decide()
    assert decision == PROMOTE and [r.name for r in rules] == list(RULE_NAMES) and all(r.passed for r in rules)
    assert rules[0].statistic == "lower bound of the 95% paired CI of roc_auc(challenger) - roc_auc(champion)"
    assert rules[2].statistic == "upper bound of the 95% paired CI of brier(challenger) - brier(champion)"
    assert rules[2].threshold == pytest.approx(0.001)


@pytest.mark.parametrize(("lower", "passed"), [(0.0, False), (1e-9, True), (-0.01, False)])
def test_auc_rule_is_one_sided_and_strict(lower, passed):
    rules, decision = _decide(auc_champ=(lower, 0.05))
    assert rules[0].passed is passed and decision == (PROMOTE if passed else REFUSE)


@pytest.mark.parametrize(("upper", "passed"), [(0.001, True), (0.0010001, False)])
def test_brier_rule_is_inclusive_non_inferiority(upper, passed):
    rules, decision = _decide(brier_champ=(-0.002, upper))
    assert rules[2].passed is passed and [r.passed for r in rules[:2]] == [True, True]
    assert decision == (PROMOTE if passed else REFUSE)


def test_nan_fails_its_rule():
    rules, decision = _decide(auc_lookup=(float("nan"), 0.1))
    assert rules[1].passed is False and decision == REFUSE


def test_frozen_acceptance_boundaries():
    def acc(auc_lo, oe):
        return frozen_acceptance({"roc_auc": (auc_lo, 0.9), "observed_expected_ratio": oe},
                                 roc_auc_ci_lower_min=0.80, oe_band=(0.80, 1.25))  # fmt: skip

    assert acc(0.7999, (0.9, 1.1)) == (False, True)
    assert acc(0.80, (0.9, 1.1)) == (True, True)
    assert acc(0.85, (0.70, 0.7999))[1] is False
    assert acc(0.85, (0.70, 0.80))[1] is True
    assert acc(0.85, (1.25, 1.40))[1] is True
    assert acc(0.85, (1.2501, 1.40))[1] is False
    assert acc(float("nan"), (float("nan"), 1.0)) == (False, False)


# --- the full gate on synthetic windows ----------------------------------------------------------------------------------


def _window(seed=0, n_patients=800):
    rng = np.random.default_rng(seed)
    rows = rng.integers(1, 3, size=n_patients)
    groups = np.repeat(np.arange(n_patients), rows)
    risk = rng.uniform(0, 1, size=len(groups))
    y = (rng.uniform(0, 1, size=len(groups)) < 0.05 + 0.4 * risk**3).astype(int)
    noise = rng.normal(0, 1, size=len(groups))
    good = np.clip(0.05 + 0.4 * risk**3, 0.001, 0.999)
    weak = np.clip(0.05 + 0.4 * np.clip(risk + 0.8 * noise, 0, 1) ** 3, 0.001, 0.999)
    return y, groups, good, weak


def _gate(probs, y, groups):
    return evaluate_gate(y, groups, probs, train_prevalence=float(y.mean()), **GATE_KW)


def test_clearly_better_challenger_promotes():
    y, groups, good, weak = _window()
    result = _gate({"challenger": good, "champion": weak, "reason_code_lookup": weak.copy()}, y, groups)
    assert result.decision == PROMOTE and not result.lookup_still_wins
    assert set(result.point) == set(ARMS) and "observed_expected_ratio" in result.point["challenger"]
    assert set(result.differences) == {"challenger_minus_champion", "challenger_minus_reason_code_lookup"}


def test_challenger_equal_to_the_champion_is_refused():
    y, groups, good, _ = _window()
    result = _gate({"challenger": good, "champion": good.copy(), "reason_code_lookup": good * 0.5}, y, groups)
    assert result.decision == REFUSE and result.rules[0].value == 0.0 and not result.rules[0].passed


def test_lookup_better_than_the_challenger_is_refused():
    y, groups, good, weak = _window()
    result = _gate({"challenger": weak, "champion": weak * 0.9, "reason_code_lookup": good}, y, groups)
    assert result.decision == REFUSE and result.lookup_still_wins


def test_constant_challenger_is_refused():
    y, groups, good, _ = _window()
    constant = np.full(len(y), float(y.mean()))
    result = _gate({"challenger": constant, "champion": good, "reason_code_lookup": good.copy()}, y, groups)
    assert result.decision == REFUSE and not result.rules[0].passed and not result.rules[1].passed


def test_both_bootstrap_calls_draw_identical_resamples():
    _, groups, _, _ = _window()
    a, b = np.random.default_rng(20261012), np.random.default_rng(20261012)
    for _ in range(3):
        assert np.array_equal(cluster_resample_indices(groups, a), cluster_resample_indices(groups, b))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p, y: {k: p[k] for k in ("champion", "challenger", "reason_code_lookup")},  # wrong key order
        lambda p, y: {**p, "challenger": p["challenger"] + 1.0},  # out of range
        lambda p, y: {**p, "challenger": p["challenger"][:-1]},  # wrong length
    ],
)
def test_bad_inputs_raise(mutate):
    y, groups, good, weak = _window()
    probs = {"challenger": good, "champion": weak, "reason_code_lookup": weak.copy()}
    with pytest.raises(ValueError):
        _gate(mutate(probs, y), y, groups)


def test_single_class_labels_raise():
    y, groups, good, weak = _window()
    with pytest.raises(ValueError, match="both classes"):
        _gate({"challenger": good, "champion": weak, "reason_code_lookup": weak}, np.zeros_like(y), groups)


def test_evaluate_frozen_v1_reports_the_three_metrics_and_acceptance():
    y, groups, good, weak = _window()
    result = evaluate_frozen_v1(y, groups, {"logistic_regression": good, "xgboost_calibrated": weak}, n_resamples=100,
                                seed=20261012, ci_level=0.95, roc_auc_ci_lower_min=0.50, oe_band=(0.80, 1.25))  # fmt: skip
    assert list(result) == ["logistic_regression", "xgboost_calibrated"]
    lr = result["logistic_regression"]
    assert set(lr.point) == set(lr.intervals) == {"roc_auc", "brier_score", "observed_expected_ratio"}
    assert lr.accepted == (lr.roc_auc_ci_lower_ok and lr.observed_expected_overlap_ok)
    assert lr.intervals["roc_auc"][0] <= lr.point["roc_auc"] <= lr.intervals["roc_auc"][1]
