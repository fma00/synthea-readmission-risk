from __future__ import annotations

import numpy as np
import pytest

from readmission_risk.models.evaluate import (
    BOOTSTRAP_METRICS,
    BootstrapResult,
    cluster_resample_indices,
    clustered_bootstrap,
    evaluate_probabilities,
    plot_reliability_diagram,
)


def _toy():
    y = np.array([0, 0, 1, 0, 1, 1])
    p = np.array([0.1, 0.2, 0.4, 0.6, 0.7, 0.9])
    return y, p


def test_metrics_match_hand_computed_values():
    y, p = _toy()
    # n_bins=3 PINNED: with the default 10 bins and n=6 every row lands in its own bin and ECE degenerates
    # to mean|y - p|, which would test nothing about binning.
    m = evaluate_probabilities(y, p, train_prevalence=0.5, n_bins=3).metrics
    assert m["roc_auc"] == pytest.approx(8 / 9, abs=1e-9)  # 8 of 9 (positive, negative) pairs ranked correctly
    assert m["brier_score"] == pytest.approx(0.87 / 6, abs=1e-9)
    assert m["brier_skill_score"] == pytest.approx(1 - (0.87 / 6) / 0.25, abs=1e-9)
    # quantile edges [0.1, 1/3, 0.6333, 0.9] -> bins {0.1,0.2}, {0.4,0.6}, {0.7,0.9}:
    # |0 - .15|, |.5 - .5|, |1 - .8| -> ECE = (2/6)(.15 + 0 + .2)
    assert m["expected_calibration_error"] == pytest.approx((0.15 + 0.0 + 0.2) / 3, abs=1e-9)
    assert m["calibration_gap"] == pytest.approx(2.9 / 6 - 0.5, abs=1e-9)
    assert m["abs_calibration_gap"] == pytest.approx(abs(2.9 / 6 - 0.5), abs=1e-9)
    assert m["n_rows"] == 6 and m["n_positive"] == 3


def test_ece_skips_empty_bins_with_default_bin_count():
    y, p = _toy()
    result = evaluate_probabilities(y, p, train_prevalence=0.5, n_bins=10)
    assert sum(c["n"] for c in result.calibration_curve) == 6
    assert result.metrics["expected_calibration_error"] == pytest.approx(np.mean(np.abs(y - p)), abs=1e-9)


def test_calibration_curve_bins_sum_to_n_and_are_ascending():
    rng = np.random.default_rng(0)
    p = rng.uniform(size=500)
    y = (rng.random(500) < p).astype(int)
    curve = evaluate_probabilities(y, p, train_prevalence=float(y.mean())).calibration_curve
    assert sum(c["n"] for c in curve) == 500
    means = [c["mean_predicted"] for c in curve]
    assert means == sorted(means)


def test_ece_is_zero_for_perfectly_calibrated_bins():
    # each bin's mean prediction equals its observed fraction by construction
    p = np.array([0.25] * 4 + [0.75] * 4)
    y = np.array([0, 0, 0, 1, 0, 1, 1, 1])
    m = evaluate_probabilities(y, p, train_prevalence=0.5, n_bins=2).metrics
    assert m["expected_calibration_error"] == pytest.approx(0.0, abs=1e-12)


def test_log_loss_finite_at_extreme_probabilities():
    y = np.array([0, 1, 0, 1])
    p = np.array([0.0, 1.0, 0.0, 1.0])
    assert np.isfinite(evaluate_probabilities(y, p, train_prevalence=0.5).metrics["log_loss"])


def test_evaluate_handles_constant_predictions():
    y = np.array([0, 1, 0, 0, 1, 0])
    result = evaluate_probabilities(y, np.full(6, 0.3), train_prevalence=0.4)
    assert len(result.calibration_curve) == 1
    assert result.metrics["roc_auc"] == 0.5
    assert result.metrics["expected_calibration_error"] == pytest.approx(abs(y.mean() - 0.3))


@pytest.mark.parametrize(
    "y, p",
    [
        (np.array([0, 1, 0]), np.array([0.1, 0.2])),  # length mismatch
        (np.array([]), np.array([])),  # empty
        (np.array([0, 2, 1]), np.array([0.1, 0.2, 0.3])),  # non-binary y
        (np.array([0, 0, 0]), np.array([0.1, 0.2, 0.3])),  # single class
        (np.array([0, 1, 0]), np.array([0.1, 1.2, 0.3])),  # p out of range
        (np.array([0, 1, 0]), np.array([0.1, np.nan, 0.3])),  # NaN
    ],
)
def test_evaluate_rejects_bad_inputs(y, p):
    with pytest.raises(ValueError):
        evaluate_probabilities(y, p, train_prevalence=0.5)


def test_cluster_resample_indices_returns_whole_patients_and_is_deterministic():
    groups = np.array(["a"] * 5 + ["b"] * 2 + ["c"] * 3 + ["d"] * 1)  # unequal sizes
    idx = cluster_resample_indices(groups, np.random.default_rng(7))
    sizes = {"a": 5, "b": 2, "c": 3, "d": 1}
    drawn_groups, counts = np.unique(groups[idx], return_counts=True)
    for g, n in zip(drawn_groups, counts, strict=True):
        assert n % sizes[g] == 0  # only WHOLE patients' rows, possibly repeated
        # and the repeated copies are exactly that patient's own row positions
        assert set(idx[groups[idx] == g]) == set(np.flatnonzero(groups == g))
    np.testing.assert_array_equal(idx, cluster_resample_indices(groups, np.random.default_rng(7)))


def _well_behaved(n_patients=200, seed=0):
    rng = np.random.default_rng(seed)
    groups = np.repeat(np.arange(n_patients), 3)
    latent = rng.normal(size=len(groups))
    y = (rng.random(len(groups)) < 1 / (1 + np.exp(-latent))).astype(int)
    p_a = 1 / (1 + np.exp(-latent))  # well calibrated
    p_b = np.clip(p_a + rng.normal(scale=0.15, size=len(groups)), 0.001, 0.999)  # noisier ranker
    return y, groups, p_a, p_b


def test_clustered_bootstrap_deterministic_and_covers_point_estimate():
    y, groups, p_a, p_b = _well_behaved()
    kwargs = {"n_bootstrap": 100, "seed": 3}
    r1 = clustered_bootstrap(y, {"a": p_a, "b": p_b}, groups, **kwargs)
    r2 = clustered_bootstrap(y, {"a": p_a, "b": p_b}, groups, **kwargs)
    assert r1 == r2 and isinstance(r1, BootstrapResult)
    assert set(r1.intervals["a"]) == set(BOOTSTRAP_METRICS) and len(BOOTSTRAP_METRICS) == 5
    point = evaluate_probabilities(y, p_a, train_prevalence=float(y.mean())).metrics
    for metric in ("roc_auc", "brier_score", "calibration_gap"):  # ECE / |gap| excluded: biased under resampling
        lo, hi = r1.intervals["a"][metric]
        assert lo <= point[metric] <= hi, metric


def test_clustered_bootstrap_paired_difference_is_within_resample():
    y, groups, p_a, p_b = _well_behaved()
    same = clustered_bootstrap(y, {"a": p_a, "b": p_a.copy()}, groups, n_bootstrap=60, seed=1)
    for metric, interval in same.paired_differences["b_minus_a"].items():
        assert interval == (0.0, 0.0), metric  # independent intervals could never give exactly (0, 0)
    better = clustered_bootstrap(y, {"worse": p_b, "better": p_a}, groups, n_bootstrap=100, seed=1)
    lo, _ = better.paired_differences["better_minus_worse"]["roc_auc"]
    assert lo > 0  # the strictly better ranker's paired AUC interval excludes 0


def test_clustered_bootstrap_zero_and_skipped_resamples():
    y, groups, p_a, _ = _well_behaved()
    assert clustered_bootstrap(y, {"a": p_a}, groups, n_bootstrap=0, seed=0) == BootstrapResult({}, {}, 0, 0)
    # 1 positive row among 40 patients: most resamples miss it entirely -> skipped
    y2 = np.zeros(40, dtype=int)
    y2[0] = 1
    with pytest.warns(UserWarning, match="single-class"):
        result = clustered_bootstrap(y2, {"a": np.linspace(0.1, 0.9, 40)}, np.arange(40), n_bootstrap=200, seed=0)
    assert result.n_resamples_skipped > 0.05 * 200
    assert result.n_resamples_used + result.n_resamples_skipped == 200


def test_clustered_bootstrap_all_resamples_single_class_raises():
    y = np.array([0, 0, 0, 0, 1])
    groups = np.arange(5)
    # a tiny n_bootstrap with a seed for which every draw misses the lone positive
    for seed in range(200):
        try:
            clustered_bootstrap(y, {"a": np.linspace(0.1, 0.9, 5)}, groups, n_bootstrap=1, seed=seed)
        except ValueError as exc:
            assert "single-class" in str(exc)
            return
    pytest.fail("no seed produced an all-single-class bootstrap")


def test_reliability_diagram_writes_nonempty_png(tmp_path):
    y, p = _toy()
    curve = evaluate_probabilities(y, p, train_prevalence=0.5, n_bins=3).calibration_curve
    out = tmp_path / "diagram.png"
    plot_reliability_diagram(curve, "toy", out)
    assert out.stat().st_size > 0 and out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
