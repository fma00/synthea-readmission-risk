"""Discrimination + calibration metrics, patient-clustered bootstrap intervals (per model and paired),
and a reliability diagram. See notes/eg-new-feature/model-training-2026-09-19.md (Interfaces > evaluate.py).
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from matplotlib.figure import Figure
from sklearn.metrics import average_precision_score, log_loss, roc_auc_score

BOOTSTRAP_METRICS: tuple[str, ...] = (
    "roc_auc",
    "brier_score",
    "expected_calibration_error",
    "calibration_gap",
    "abs_calibration_gap",
)
# calibration_gap = mean(y_prob) - mean(y_true)  (SIGNED calibration-in-the-large; abs_calibration_gap = |gap|).
# WHY BOTH: the signed gap says which DIRECTION a model is miscalibrated (per-model interval, reported for
# direction only), but a paired difference of SIGNED gaps cannot say which model is better calibrated (two
# equal-magnitude opposite-sign gaps give a large "difference"), so calibration CLAIMS use
# expected_calibration_error and abs_calibration_gap, never the signed gap.

_LOG_LOSS_EPS = 1e-15
_SKIPPED_RESAMPLE_WARN_FRACTION = 0.05


@dataclass(frozen=True)
class EvaluationResult:
    metrics: dict[str, float]
    calibration_curve: list[dict[str, float]]


def _quantile_bin_ids(y_prob: np.ndarray, n_bins: int) -> np.ndarray:
    """Approximately equal-count bins. Edges are the unique quantiles of y_prob; if fewer than 2 unique
    edges exist (e.g. every prediction identical) ALL rows go in one bin. Bins are left-closed
    [e_i, e_{i+1}) with the last bin closed on the right. With small n or ties there can be FEWER than n_bins
    distinct edges and some bins empty, so callers must skip empty bins. pd.qcut(..., duplicates="drop")
    is deliberately not used: on constant input it returns no categories at all rather than one bin."""
    edges = np.unique(np.quantile(y_prob, np.linspace(0.0, 1.0, n_bins + 1)))
    if len(edges) < 2:
        return np.zeros(len(y_prob), dtype=int)
    return np.clip(np.searchsorted(edges, y_prob, side="right") - 1, 0, len(edges) - 2)


def _ece_and_curve(
    y_true: np.ndarray, y_prob: np.ndarray, n_bins: int
) -> tuple[float, list[dict[str, float]]]:
    ids = _quantile_bin_ids(y_prob, n_bins)
    n_total = len(y_true)
    curve: list[dict[str, float]] = []
    ece = 0.0
    for b in np.unique(ids):  # non-empty bins only, ascending
        mask = ids == b
        n_b = int(mask.sum())
        mean_pred = float(y_prob[mask].mean())
        observed = float(y_true[mask].mean())
        curve.append({"mean_predicted": mean_pred, "observed_fraction": observed, "n": n_b})
        ece += (n_b / n_total) * abs(observed - mean_pred)
    return float(ece), curve


def _validate_inputs(y_true: np.ndarray, y_prob: np.ndarray) -> None:
    if y_true.ndim != 1 or y_prob.ndim != 1:
        raise ValueError("y_true and y_prob must be 1-D")
    if len(y_true) != len(y_prob):
        raise ValueError(f"y_true and y_prob length mismatch: {len(y_true)} vs {len(y_prob)}")
    if len(y_true) == 0:
        raise ValueError("Cannot evaluate an empty prediction set")
    if not np.isin(y_true, [0, 1]).all():
        raise ValueError("y_true must contain only 0/1")
    if len(np.unique(y_true)) < 2:
        raise ValueError("y_true contains a single class; AUC and calibration are undefined")
    if not np.isfinite(y_prob).all():
        raise ValueError("y_prob contains non-finite values")
    if (y_prob < 0).any() or (y_prob > 1).any():
        raise ValueError("y_prob must lie within [0, 1]")


def evaluate_probabilities(
    y_true: np.ndarray, y_prob: np.ndarray, *, train_prevalence: float, n_bins: int = 10
) -> EvaluationResult:
    """Pure. brier_skill_score = 1 - brier_score / mean((train_prevalence - y_true)^2), i.e. skill relative to
    a constant predictor that only knows the TRAIN prevalence. expected_calibration_error =
    sum_b (n_b/N)*|observed_fraction_b - mean_predicted_b| over NON-EMPTY quantile bins. log_loss is called
    positionally with y_prob clipped to [1e-15, 1-1e-15] (sklearn 1.9 renamed the second parameter)."""
    y_true = np.asarray(y_true)
    y_prob = np.asarray(y_prob, dtype=float)
    _validate_inputs(y_true, y_prob)

    brier = float(np.mean((y_prob - y_true) ** 2))
    reference = float(np.mean((train_prevalence - y_true) ** 2))
    ece, curve = _ece_and_curve(y_true, y_prob, n_bins)
    mean_pred = float(y_prob.mean())
    observed = float(y_true.mean())
    gap = mean_pred - observed
    metrics = {
        "roc_auc": float(roc_auc_score(y_true, y_prob)),
        "pr_auc": float(average_precision_score(y_true, y_prob)),
        "brier_score": brier,
        "brier_skill_score": float(1.0 - brier / reference) if reference > 0 else float("nan"),
        "log_loss": float(log_loss(y_true, np.clip(y_prob, _LOG_LOSS_EPS, 1 - _LOG_LOSS_EPS), labels=[0, 1])),
        "expected_calibration_error": ece,
        "mean_predicted_probability": mean_pred,
        "observed_prevalence": observed,
        "calibration_gap": gap,
        "abs_calibration_gap": abs(gap),
        "n_rows": float(len(y_true)),
        "n_positive": float(y_true.sum()),
    }
    return EvaluationResult(metrics=metrics, calibration_curve=curve)


def cluster_resample_indices(groups: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """One patient-clustered resample: draws len(unique(groups)) patients WITH replacement using `rng` and
    returns the concatenated positional row indices of every drawn patient (a patient drawn twice
    contributes their rows twice; one never drawn contributes none). Rows of one patient are correlated, so
    resampling rows independently would understate the interval. Groups are enumerated in sorted-unique
    order so the result is a deterministic function of (groups, rng state)."""
    groups = np.asarray(groups)
    unique, inverse = np.unique(groups, return_inverse=True)
    order = np.argsort(inverse, kind="stable")
    counts = np.bincount(inverse, minlength=len(unique))
    starts = np.concatenate(([0], np.cumsum(counts)[:-1]))
    drawn = rng.integers(0, len(unique), size=len(unique))
    return np.concatenate([order[starts[g] : starts[g] + counts[g]] for g in drawn])


@dataclass(frozen=True)
class BootstrapResult:
    intervals: dict[str, dict[str, tuple[float, float]]]
    # intervals[model_name][metric] = (lower, upper) for metric in BOOTSTRAP_METRICS
    paired_differences: dict[str, dict[str, tuple[float, float]]]
    # paired_differences["<b>_minus_<a>"][metric] = interval of metric(b) - metric(a) on the SAME resamples
    # (signed calibration_gap: gap_b - gap_a; abs_calibration_gap: |gap_b| - |gap_a|)
    n_resamples_used: int
    n_resamples_skipped: int


def _resample_metrics(y: np.ndarray, p: np.ndarray, n_bins: int) -> dict[str, float]:
    ece, _ = _ece_and_curve(y, p, n_bins)
    gap = float(p.mean() - y.mean())
    return {
        "roc_auc": float(roc_auc_score(y, p)),
        "brier_score": float(np.mean((p - y) ** 2)),
        "expected_calibration_error": ece,
        "calibration_gap": gap,
        "abs_calibration_gap": abs(gap),
    }


def clustered_bootstrap(
    y_true: np.ndarray,
    probs: dict[str, np.ndarray],
    groups: np.ndarray,
    *,
    n_bootstrap: int,
    seed: int,
    alpha: float = 0.05,
    n_bins: int = 10,
) -> BootstrapResult:
    """Patient-clustered percentile bootstrap. Draws n_bootstrap resamples from ONE
    np.random.default_rng(seed) stream and, for EACH resample, computes every metric in BOOTSTRAP_METRICS for
    EVERY model in `probs` on the same rows -- so a paired difference is a within-resample difference, not a
    difference of two independent intervals. Resamples whose y_true holds a single class are skipped and
    counted (UserWarning containing "single-class" if more than 5% are skipped); if EVERY resample is skipped
    a ValueError is raised. Paired differences need exactly two models (a = first, b = second); otherwise
    paired_differences is {}. n_bootstrap == 0 returns an empty result."""
    if n_bootstrap == 0:
        return BootstrapResult({}, {}, 0, 0)
    y_true = np.asarray(y_true)
    groups = np.asarray(groups)
    probs = {name: np.asarray(p, dtype=float) for name, p in probs.items()}
    names = list(probs)

    rng = np.random.default_rng(seed)
    per_model: dict[str, dict[str, list[float]]] = {n: {m: [] for m in BOOTSTRAP_METRICS} for n in names}
    n_skipped = 0
    for _ in range(n_bootstrap):
        idx = cluster_resample_indices(groups, rng)
        y = y_true[idx]
        if y.min() == y.max():
            n_skipped += 1
            continue
        for name in names:
            values = _resample_metrics(y, probs[name][idx], n_bins)
            for metric in BOOTSTRAP_METRICS:
                per_model[name][metric].append(values[metric])

    n_used = n_bootstrap - n_skipped
    if n_used == 0:
        raise ValueError("every bootstrap resample was single-class; the test window is too small or degenerate")
    if n_skipped / n_bootstrap > _SKIPPED_RESAMPLE_WARN_FRACTION:
        warnings.warn(
            f"{n_skipped} of {n_bootstrap} bootstrap resamples were single-class and skipped",
            UserWarning,
            stacklevel=2,
        )

    lo_q, hi_q = 100 * alpha / 2, 100 * (1 - alpha / 2)

    def interval(values: list[float]) -> tuple[float, float]:
        lo, hi = np.percentile(np.asarray(values), [lo_q, hi_q])
        return float(lo), float(hi)

    intervals = {n: {m: interval(per_model[n][m]) for m in BOOTSTRAP_METRICS} for n in names}
    paired: dict[str, dict[str, tuple[float, float]]] = {}
    if len(names) == 2:
        a, b = names
        diffs: dict[str, tuple[float, float]] = {}
        for metric in BOOTSTRAP_METRICS:
            va = np.asarray(per_model[a][metric])
            vb = np.asarray(per_model[b][metric])
            # abs_calibration_gap is already |gap| per resample, so vb - va is |gap_b| - |gap_a|
            diffs[metric] = interval(list(vb - va))
        paired[f"{b}_minus_{a}"] = diffs
    return BootstrapResult(intervals, paired, n_used, n_skipped)


def plot_reliability_diagram(curve: list[dict[str, float]], title: str, out_path: Path) -> None:
    """Uses the matplotlib Figure API directly (no pyplot global state, safe headless). The parent
    directory of out_path must exist."""
    fig = Figure(figsize=(5, 5))
    ax = fig.subplots()
    ax.plot([0, 1], [0, 1], linestyle="--", color="grey", label="perfectly calibrated")
    ax.plot(
        [c["mean_predicted"] for c in curve],
        [c["observed_fraction"] for c in curve],
        marker="o",
        label="model",
    )
    ax.set_xlabel("Mean predicted probability")
    ax.set_ylabel("Observed fraction positive")
    ax.set_title(title)
    ax.legend(loc="upper left")
    fig.savefig(out_path, format="png", dpi=100)
