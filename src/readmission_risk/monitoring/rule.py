"""The pre-registered calibration-in-the-large rule (prereg.yaml monitoring:): observed/expected with an exact (Garwood)
Poisson 95% CI on the observed count, a minimum-positives floor, and a band. The parameters are read from prereg.yaml
at runtime, never copied here. See notes/eg-new-feature/v2-slice7-monitoring-tick-ledger-2026-10-18.md (Interfaces > rule.py).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from readmission_risk.models.evaluate import observed_expected_ratio
from readmission_risk.ops.prereg import Prereg

VERDICTS = ("ok", "alert", "insufficient_data", "incomplete_cohort")


@dataclass(frozen=True)
class AlertRule:
    matured_cohort_weeks: int
    min_positives_floor: int
    band: tuple[float, float]


@dataclass(frozen=True)
class RuleResult:
    ratio: float
    ci_low: float
    ci_high: float
    verdict: str


def alert_rule_from_prereg(prereg: Prereg) -> AlertRule:
    block = prereg.monitoring
    rule = block["alert_rule"]
    expected = {
        "metric": "observed_expected_ratio",
        "ci": "exact_poisson_95_on_observed",
        "condition": "ci_excludes_1_and_point_outside_band",
    }
    for key, literal in expected.items():
        if rule[key] != literal:
            raise ValueError(f"prereg monitoring.alert_rule.{key} is {rule[key]!r}; this code implements {literal!r}")
    if block["auc"] != "report_only":
        raise ValueError(f"prereg monitoring.auc is {block['auc']!r}; this code implements 'report_only'")
    low, high = rule["band"]
    return AlertRule(
        matured_cohort_weeks=int(block["matured_cohort_weeks"]),
        min_positives_floor=int(block["min_positives_floor"]),
        band=(float(low), float(high)),
    )


def poisson_ci(observed: int) -> tuple[float, float]:
    """Exact (Garwood) 95% interval for a Poisson count."""
    from scipy.stats import chi2

    if type(observed) is not int or observed < 0:
        raise ValueError(f"observed must be an int >= 0; got {observed!r}")
    low = 0.0 if observed == 0 else float(chi2.ppf(0.025, 2 * observed) / 2)
    return low, float(chi2.ppf(0.975, 2 * observed + 2) / 2)


def ratio_and_ci(observed: int, expected: float) -> tuple[float, float, float] | None:
    """The ONLY place a ratio or CI is computed: (O/E, lo/E, hi/E), E treated as fixed; None when E <= 0."""
    if not math.isfinite(expected):
        raise ValueError(f"expected must be finite; got {expected!r}")
    if expected <= 0:
        return None
    low, high = poisson_ci(observed)
    ratio = observed_expected_ratio([observed], [expected])  # prereg metrics.definitions: sum(y) / sum(p), on the sums
    return ratio, low / expected, high / expected


def evaluate_rule(observed: int, expected: float, rule: AlertRule) -> RuleResult:
    if type(observed) is not int or observed < 0:
        raise ValueError(f"observed must be an int >= 0; got {observed!r}")
    if not math.isfinite(expected) or expected <= 0:
        raise ValueError(f"expected must be finite and > 0; got {expected!r}")
    ratio, low, high = ratio_and_ci(observed, expected)
    if observed < rule.min_positives_floor:
        verdict = "insufficient_data"
    elif (high < 1 or low > 1) and (ratio < rule.band[0] or ratio > rule.band[1]):
        verdict = "alert"
    else:
        verdict = "ok"
    return RuleResult(ratio=ratio, ci_low=low, ci_high=high, verdict=verdict)
