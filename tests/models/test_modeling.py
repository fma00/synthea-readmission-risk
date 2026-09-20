from __future__ import annotations

import numpy as np
import pytest
from sklearn.base import clone
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import roc_auc_score

from readmission_risk.models.data import GROUP_COLUMN, LABEL_COLUMN, prepare_features
from readmission_risk.models.evaluate import evaluate_probabilities
from readmission_risk.models.modeling import (
    XGB_PARAMS,
    build_calibrated_xgboost,
    build_logistic_regression,
    make_grouped_cv_splits,
    validate_cv_splits,
)
from readmission_risk.models.split import chronological_group_split
from tests.models.helpers import REFERENCE_DATE, TEST_START, make_gold_frame

# Model configuration for every test here unless stated: model seed 42, 5 calibration folds, "sigmoid".
SEED = 42
FOLDS = 5


@pytest.fixture(scope="module")
def data_2000():
    split = chronological_group_split(
        make_gold_frame(2000, seed=0), reference_date=REFERENCE_DATE, test_start_date=TEST_START
    )
    X_train, X_test = prepare_features(split.train), prepare_features(split.test)
    y_train, y_test = split.train[LABEL_COLUMN].to_numpy(), split.test[LABEL_COLUMN].to_numpy()
    groups = split.train[GROUP_COLUMN].to_numpy()
    return X_train, y_train, groups, X_test, y_test


def _calibrated(y, groups, seed=SEED):
    splits = make_grouped_cv_splits(y, groups, FOLDS)
    return build_calibrated_xgboost(seed, splits), splits


def test_logistic_regression_learns_planted_signal(data_2000):
    X_train, y_train, _, X_test, y_test = data_2000
    model = build_logistic_regression(SEED).fit(X_train, y_train)
    proba = model.predict_proba(X_test)[:, 1]
    assert ((proba >= 0) & (proba <= 1)).all()
    assert roc_auc_score(y_test, proba) > 0.7
    assert model.named_steps["model"].class_weight is None  # reweighting would break calibration


def test_grouped_cv_splits_are_patient_disjoint_and_two_class(data_2000):
    _, y_train, groups, _, _ = data_2000
    splits = make_grouped_cv_splits(y_train, groups, FOLDS)
    assert len(splits) == FOLDS
    for fit_idx, cal_idx in splits:
        assert not (set(groups[fit_idx]) & set(groups[cal_idx]))
        assert len(np.unique(y_train[fit_idx])) == 2 and len(np.unique(y_train[cal_idx])) == 2


def test_grouped_cv_splits_reject_too_few_patients_and_single_class_folds():
    y = np.array([0, 1, 0, 1, 0, 1])
    with pytest.raises(ValueError, match="distinct patients"):
        make_grouped_cv_splits(y, np.array(list("aabbcc")), 5)
    # 3 patients, 3 folds: the patient holding both positives leaves a one-class fit part in some fold
    y = np.array([1, 1, 0, 0, 0, 0])
    with pytest.raises(ValueError, match="Calibration fold"):
        make_grouped_cv_splits(y, np.array(list("aabbcc")), 3)


def test_validate_cv_splits_detects_reordered_or_mismatched_groups(data_2000):
    _, y_train, groups, _, _ = data_2000
    splits = make_grouped_cv_splits(y_train, groups, FOLDS)
    validate_cv_splits(splits, groups)  # the matching groups pass

    permuted = np.random.default_rng(0).permutation(groups)  # "X_train was reordered after the splits were built"
    with pytest.raises(ValueError, match="Calibration fold"):
        validate_cv_splits(splits, permuted)
    with pytest.raises(ValueError, match="cover every row"):  # length check first: ValueError, never IndexError
        validate_cv_splits(splits, groups[:-5])
    overlapping = [(np.concatenate([fit, val[:1]]), val) for fit, val in splits]
    with pytest.raises(ValueError, match="overlap"):
        validate_cv_splits(overlapping, groups)


def test_calibrated_xgboost_structure_and_probabilities(data_2000):
    X_train, y_train, groups, X_test, _ = data_2000
    model, _ = _calibrated(y_train, groups)
    assert isinstance(model, CalibratedClassifierCV)
    assert model.ensemble is True and model.method == "sigmoid"
    xgb = model.estimator.named_steps["model"]
    assert xgb.get_params()["n_jobs"] == XGB_PARAMS["n_jobs"] == 1
    assert xgb.get_params()["random_state"] == SEED
    assert xgb.get_params()["scale_pos_weight"] is None

    proba = model.fit(X_train, y_train).predict_proba(X_test)
    np.testing.assert_allclose(proba.sum(axis=1), 1.0)
    assert ((proba >= 0) & (proba <= 1)).all()

    with pytest.raises(ValueError, match="calibration method"):
        build_calibrated_xgboost(SEED, [], method="platt")


def test_calibrated_xgboost_uses_the_supplied_patient_grouped_folds(data_2000):
    """Guards the patient-level-leakage promise: if the wrapper ever used its own random folds (e.g. cv=5), a
    patient's rows could sit in both the base-fit and calibration parts and no metric-level test would notice."""
    X_train, y_train, groups, X_test, _ = data_2000
    model, splits = _calibrated(y_train, groups)
    assert model.cv is splits
    model.fit(X_train, y_train)
    assert len(model.calibrated_classifiers_) == FOLDS
    for (fit_idx, _), fold_model in zip(splits, model.calibrated_classifiers_, strict=True):
        # each fold's base estimator was fitted on EXACTLY that fold's (patient-disjoint) fit rows
        reference = clone(model.estimator).fit(X_train.iloc[fit_idx], y_train[fit_idx])
        np.testing.assert_allclose(
            fold_model.estimator.predict_proba(X_test), reference.predict_proba(X_test), atol=1e-9
        )


def test_calibrated_xgboost_learns_planted_signal(data_2000):
    X_train, y_train, groups, X_test, y_test = data_2000
    model, _ = _calibrated(y_train, groups)
    assert roc_auc_score(y_test, model.fit(X_train, y_train).predict_proba(X_test)[:, 1]) > 0.7


def test_models_are_calibrated_on_planted_signal(data_2000):
    X_train, y_train, groups, X_test, y_test = data_2000
    prevalence = float(y_train.mean())
    lr = build_logistic_regression(SEED).fit(X_train, y_train)
    xgb, _ = _calibrated(y_train, groups)
    xgb.fit(X_train, y_train)

    lr_m = evaluate_probabilities(y_test, lr.predict_proba(X_test)[:, 1], train_prevalence=prevalence).metrics
    xgb_m = evaluate_probabilities(y_test, xgb.predict_proba(X_test)[:, 1], train_prevalence=prevalence).metrics
    # Bounds set with >= 20% headroom over the worst value in a 40-seed measurement (see the design doc). If one
    # is exceeded, first re-measure across fixture seeds 0-5: only a persistent exceedance is a model defect.
    assert lr_m["expected_calibration_error"] < 0.06
    assert lr_m["abs_calibration_gap"] < 0.04
    assert lr_m["brier_skill_score"] > 0.10
    assert xgb_m["expected_calibration_error"] < 0.09
    assert xgb_m["abs_calibration_gap"] < 0.05
    assert xgb_m["brier_skill_score"] > 0.08


def test_models_are_deterministic(data_2000):
    X_train, y_train, groups, X_test, _ = data_2000
    a = build_logistic_regression(SEED).fit(X_train, y_train).predict_proba(X_test)
    b = build_logistic_regression(SEED).fit(X_train, y_train).predict_proba(X_test)
    np.testing.assert_allclose(a, b, atol=1e-12)
    xa, _ = _calibrated(y_train, groups)
    xb, _ = _calibrated(y_train, groups)
    np.testing.assert_allclose(
        xa.fit(X_train, y_train).predict_proba(X_test), xb.fit(X_train, y_train).predict_proba(X_test), atol=1e-12
    )


def test_label_permutation_canary(data_2000):
    """A model fitted on permuted labels has RANDOM coefficients, so one permuted AUC has SD ~0.067 (not the
    ~0.017 sampling error): the canary averages K=10 permutations and requires the real model to beat all of them.
    If it fails, increase K -- do not widen the tolerance beyond [0.35, 0.65]."""
    X_train, y_train, _, X_test, y_test = data_2000
    real = roc_auc_score(y_test, build_logistic_regression(SEED).fit(X_train, y_train).predict_proba(X_test)[:, 1])
    permuted = []
    for i in range(10):
        y_perm = np.random.default_rng(i).permutation(y_train)
        permuted.append(
            roc_auc_score(y_test, build_logistic_regression(SEED).fit(X_train, y_perm).predict_proba(X_test)[:, 1])
        )
    assert 0.4 <= float(np.mean(permuted)) <= 0.6
    assert real > max(permuted)


def test_label_permutation_canary_calibrated_xgboost(data_2000):
    """The calibrated wrapper's positional cv-split coupling lives on THIS path, so it needs its own canary.
    The XGBoost path's null variance was not measured at design time: if this fails, raise K, do not widen
    beyond [0.35, 0.65]."""
    X_train, y_train, groups, X_test, y_test = data_2000
    model, _ = _calibrated(y_train, groups)
    real = roc_auc_score(y_test, model.fit(X_train, y_train).predict_proba(X_test)[:, 1])
    permuted = []
    for i in range(5):
        y_perm = np.random.default_rng(i).permutation(y_train)
        splits = make_grouped_cv_splits(y_perm, groups, FOLDS)
        m = build_calibrated_xgboost(SEED, splits)
        permuted.append(roc_auc_score(y_test, m.fit(X_train, y_perm).predict_proba(X_test)[:, 1]))
    assert 0.4 <= float(np.mean(permuted)) <= 0.6
    assert real > max(permuted)
