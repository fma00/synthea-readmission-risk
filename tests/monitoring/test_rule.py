"""The pre-registered O/E rule (v2 slice 7)."""

from __future__ import annotations

from pathlib import Path

import pytest

from readmission_risk.monitoring.rule import (
    AlertRule,
    alert_rule_from_prereg,
    evaluate_rule,
    poisson_ci,
    ratio_and_ci,
)
from readmission_risk.ops.prereg import load_prereg

ROOT = Path(__file__).resolve().parents[2]
RULE = AlertRule(matured_cohort_weeks=13, min_positives_floor=20, band=(0.80, 1.25))


def test_rule_is_read_from_the_committed_prereg():
    assert alert_rule_from_prereg(load_prereg(ROOT / "prereg.yaml")) == RULE


def test_garwood_reference_values():
    assert poisson_ci(0) == (0.0, pytest.approx(3.6889, abs=5e-5))
    low, high = poisson_ci(20)
    assert low == pytest.approx(12.2165, abs=5e-5) and high == pytest.approx(30.8884, abs=5e-5)
    low, high = ratio_and_ci(40, 40.0)[1:]
    assert round(low, 4) == 0.7144 and round(high, 4) == 1.3617  # the M5 base row's values


def test_floor_boundary():
    assert evaluate_rule(19, 5.0, RULE).verdict == "insufficient_data"  # ratio 3.8, CI excludes 1: still not an alert
    assert evaluate_rule(20, 5.0, RULE).verdict == "alert"


@pytest.mark.parametrize(
    ("observed", "expected", "verdict"),
    [
        (60, 30.0, "alert"),  # ratio 2.0, CI excludes 1
        (21, 15.0, "ok"),  # ratio 1.4 outside the band, but the CI includes 1
        (100, 88.0, "ok"),  # ratio 1.136 inside the band
        (20, 40.0, "alert"),  # ratio 0.5, CI excludes 1 from below
    ],
)
def test_alert_needs_both_conditions(observed, expected, verdict):
    result = evaluate_rule(observed, expected, RULE)
    assert result.verdict == verdict
    assert result.ci_low <= result.ratio <= result.ci_high


def test_band_edges_are_not_alerts():
    # a huge count makes the CI tight; a ratio exactly on an edge is not "outside the band"
    assert evaluate_rule(125000, 100000.0, RULE).verdict == "ok"
    assert evaluate_rule(80000, 100000.0, RULE).verdict == "ok"


def test_value_errors():
    for observed, expected in ((-1, 1.0), (1, 0.0), (1, float("nan")), (1, -2.0)):
        with pytest.raises(ValueError):
            evaluate_rule(observed, expected, RULE)
    assert ratio_and_ci(3, 0.0) is None
    with pytest.raises(ValueError):
        ratio_and_ci(3, float("inf"))
    with pytest.raises(ValueError):
        poisson_ci(1.5)
