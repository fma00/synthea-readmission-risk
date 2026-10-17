"""The promotion gate (v2 PRD FR15) and the frozen-v1 acceptance (FR16) as pure functions over predictions and intervals.
Every number comes from prereg.yaml; nothing here reads files or MLflow. Design (owner decisions D1-D6, the rules, the
one-sided AUC test): notes/eg-new-feature/v2-slice6b-promotion-gate-frozen-v1-2026-10-16.md, Interfaces > S2.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

from .evaluate import clustered_bootstrap, evaluate_probabilities

ARMS: tuple[str, ...] = ("challenger", "champion", "reason_code_lookup")
SUPPORTED_BOOTSTRAP_KIND = "patient_clustered_paired_percentile"
FROZEN_REPORT_METRICS: tuple[str, ...] = ("roc_auc", "brier_score", "observed_expected_ratio")
RULE_NAMES: tuple[str, ...] = ("roc_auc_vs_champion", "roc_auc_vs_reason_code_lookup", "brier_noninferior_vs_champion")
PROMOTE, REFUSE = "PROMOTE", "REFUSE"


@dataclass(frozen=True)
class RuleOutcome:
    name: str
    statistic: str
    value: float
    threshold: float
    passed: bool


@dataclass(frozen=True)
class GateResult:
    point: dict[str, dict[str, float]]
    differences: dict[str, dict[str, tuple[float, float]]]
    rules: tuple[RuleOutcome, ...]
    decision: str
    lookup_still_wins: bool
    n_resamples_used: int
    n_resamples_skipped: int


@dataclass(frozen=True)
class FrozenModelResult:
    point: dict[str, float]
    intervals: dict[str, tuple[float, float]]
    roc_auc_ci_lower_ok: bool
    observed_expected_overlap_ok: bool
    accepted: bool


def _finite(x: float) -> bool:
    return isinstance(x, (int, float)) and math.isfinite(x)


def decide_gate(
    differences: Mapping[str, Mapping[str, tuple[float, float]]],
    point: Mapping[str, Mapping[str, float]],
    *,
    brier_margin_fraction: float,
    ci_level: float,
) -> tuple[tuple[RuleOutcome, ...], str]:
    """The three pre-registered rules, in RULE_NAMES order, and the decision: PROMOTE iff every rule passed. The AUC rules are
    one-sided (lower bound strictly > 0); the Brier rule is non-inferiority (upper bound <= margin x the champion's Brier).
    A NaN value or threshold fails its rule."""
    pct = f"{ci_level:.0%}"
    vs_champion = differences["challenger_minus_champion"]
    vs_lookup = differences["challenger_minus_reason_code_lookup"]
    brier_threshold = brier_margin_fraction * point["champion"]["brier_score"]
    specs = (
        (RULE_NAMES[0], f"lower bound of the {pct} paired CI of roc_auc(challenger) - roc_auc(champion)",
         vs_champion["roc_auc"][0], 0.0, "gt"),
        (RULE_NAMES[1], f"lower bound of the {pct} paired CI of roc_auc(challenger) - roc_auc(reason_code_lookup)",
         vs_lookup["roc_auc"][0], 0.0, "gt"),
        (RULE_NAMES[2], f"upper bound of the {pct} paired CI of brier(challenger) - brier(champion)",
         vs_champion["brier_score"][1], brier_threshold, "le"),
    )  # fmt: skip
    rules = []
    for name, statistic, value, threshold, op in specs:
        ok = _finite(value) and _finite(threshold) and (value > threshold if op == "gt" else value <= threshold)
        rules.append(RuleOutcome(name, statistic, float(value), float(threshold), bool(ok)))
    decision = PROMOTE if all(r.passed for r in rules) else REFUSE
    return tuple(rules), decision


def frozen_acceptance(
    intervals: Mapping[str, tuple[float, float]], *, roc_auc_ci_lower_min: float, oe_band: tuple[float, float]
) -> tuple[bool, bool]:
    """(the ROC-AUC CI lower bound is >= the minimum, the O/E CI overlaps the band); bounds inclusive; NaN fails."""
    auc_lo = intervals["roc_auc"][0]
    oe_lo, oe_hi = intervals["observed_expected_ratio"]
    band_lo, band_hi = oe_band
    auc_ok = _finite(auc_lo) and auc_lo >= roc_auc_ci_lower_min
    oe_ok = _finite(oe_lo) and _finite(oe_hi) and oe_lo <= band_hi and oe_hi >= band_lo
    return bool(auc_ok), bool(oe_ok)


def _check_inputs(y_true: np.ndarray, probs: Mapping[str, np.ndarray], expected_keys: tuple[str, ...]) -> None:
    if tuple(probs) != expected_keys:
        raise ValueError(f"probs must have exactly the keys {list(expected_keys)} in that order; got {list(probs)}")
    if set(np.unique(y_true)) != {0, 1}:
        raise ValueError("y_true must be binary with both classes present")
    for name, p in probs.items():
        if p.shape != y_true.shape:
            raise ValueError(f"probs[{name!r}] has shape {p.shape}; expected {y_true.shape}")
        if not np.isfinite(p).all() or (p < 0).any() or (p > 1).any():
            raise ValueError(f"probs[{name!r}] must be finite and in [0, 1]")


def evaluate_gate(
    y_true,
    groups,
    probs: Mapping[str, np.ndarray],
    *,
    train_prevalence: float,
    n_resamples: int,
    seed: int,
    ci_level: float,
    brier_margin_fraction: float,
) -> GateResult:
    """The FR15 gate on one test window. Two clustered_bootstrap calls with the same seed and groups draw the identical
    resamples, so both paired comparisons are on the same resampled patients."""
    y_true = np.asarray(y_true)
    groups = np.asarray(groups)
    probs = {k: np.asarray(v, dtype=float) for k, v in probs.items()}
    _check_inputs(y_true, probs, ARMS)
    point = {arm: evaluate_probabilities(y_true, probs[arm], train_prevalence=train_prevalence).metrics for arm in ARMS}
    alpha = 1.0 - ci_level
    runs = {
        comparator: clustered_bootstrap(
            y_true, {comparator: probs[comparator], "challenger": probs["challenger"]}, groups,
            n_bootstrap=n_resamples, seed=seed, alpha=alpha,
        )
        for comparator in ("champion", "reason_code_lookup")
    }  # fmt: skip
    used = {r.n_resamples_used for r in runs.values()}
    if len(used) != 1:
        raise RuntimeError(f"the two bootstrap calls used different resample counts {sorted(used)}; they must be identical")
    differences = {key: dict(value) for r in runs.values() for key, value in r.paired_differences.items()}
    rules, decision = decide_gate(differences, point, brier_margin_fraction=brier_margin_fraction, ci_level=ci_level)
    return GateResult(
        point=point,
        differences=differences,
        rules=rules,
        decision=decision,
        lookup_still_wins=not rules[1].passed,
        n_resamples_used=runs["champion"].n_resamples_used,
        n_resamples_skipped=runs["champion"].n_resamples_skipped,
    )


def evaluate_frozen_v1(
    y_true,
    groups,
    probs: Mapping[str, np.ndarray],
    *,
    n_resamples: int,
    seed: int,
    ci_level: float,
    roc_auc_ci_lower_min: float,
    oe_band: tuple[float, float],
) -> dict[str, FrozenModelResult]:
    """FR16: the pre-registered metrics of each frozen model with patient-clustered intervals, and its acceptance."""
    y_true = np.asarray(y_true)
    groups = np.asarray(groups)
    probs = {k: np.asarray(v, dtype=float) for k, v in probs.items()}
    _check_inputs(y_true, probs, tuple(probs))
    boot = clustered_bootstrap(y_true, probs, groups, n_bootstrap=n_resamples, seed=seed, alpha=1.0 - ci_level)
    prevalence = float(y_true.mean())
    result = {}
    for name, p in probs.items():
        metrics = evaluate_probabilities(y_true, p, train_prevalence=prevalence).metrics
        point = {m: metrics[m] for m in FROZEN_REPORT_METRICS}
        intervals = {m: boot.intervals[name][m] for m in FROZEN_REPORT_METRICS}
        auc_ok, oe_ok = frozen_acceptance(intervals, roc_auc_ci_lower_min=roc_auc_ci_lower_min, oe_band=oe_band)
        result[name] = FrozenModelResult(point, intervals, auc_ok, oe_ok, auc_ok and oe_ok)
    return result
