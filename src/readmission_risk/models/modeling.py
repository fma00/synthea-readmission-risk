"""Model builders: a LogisticRegression baseline and a calibrated XGBoost challenger, plus the
patient-grouped cross-validation splits the calibration wrapper needs.
See notes/eg-new-feature/model-training-2026-09-19.md (Interfaces > modeling.py).
"""

from __future__ import annotations

import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import Pipeline
from xgboost import XGBClassifier

from .features import build_linear_preprocessor, build_tree_preprocessor

XGB_PARAMS: dict = {
    "n_estimators": 300,
    "max_depth": 4,
    "learning_rate": 0.05,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "min_child_weight": 5,
    "reg_lambda": 1.0,
    "objective": "binary:logistic",
    "eval_metric": "logloss",
    "tree_method": "hist",
    # a fixed thread count for run-to-run determinism; the data is ~10k rows so the cost is negligible
    "n_jobs": 1,
}
# scale_pos_weight is deliberately NOT set (it would distort predicted probabilities, and calibration is a
# project priority). random_state is injected from the seed.

_CALIBRATION_METHODS = ("sigmoid", "isotonic")


def build_logistic_regression(seed: int) -> Pipeline:
    """class_weight is left at its default None -- reweighting would break calibration."""
    return Pipeline(
        [
            ("preprocess", build_linear_preprocessor()),
            ("model", LogisticRegression(C=1.0, solver="lbfgs", max_iter=2000, random_state=seed)),
        ]
    )


def make_grouped_cv_splits(
    y: np.ndarray, groups: np.ndarray, n_splits: int
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Deterministic (unshuffled) GroupKFold splits as positional indices into y/groups. Raises
    ValueError if there are fewer distinct groups than n_splits, or if ANY fold's fit part or
    calibration part holds a single class (CalibratedClassifierCV cannot calibrate on a one-class fold)."""
    y = np.asarray(y)
    groups = np.asarray(groups)
    n_groups = len(np.unique(groups))
    if n_groups < n_splits:
        raise ValueError(f"Need at least {n_splits} distinct patients for {n_splits} calibration folds; got {n_groups}")
    splits = list(GroupKFold(n_splits=n_splits).split(np.zeros(len(y)), y, groups))
    for fold, (fit_idx, cal_idx) in enumerate(splits):
        if len(np.unique(y[fit_idx])) < 2:
            raise ValueError(f"Calibration fold {fold}: the fit part contains a single class")
        if len(np.unique(y[cal_idx])) < 2:
            raise ValueError(f"Calibration fold {fold}: the calibration part contains a single class")
    return splits


def validate_cv_splits(cv_splits: list[tuple[np.ndarray, np.ndarray]], groups: np.ndarray) -> None:
    """`groups` MUST be the patient ids of the rows of the frame about to be fit, in fit order. Guards the
    positional coupling of build_calibrated_xgboost: if X_train were reordered or filtered after the splits
    were built, the same patient could land on both sides of a fold, silently reintroducing the patient-level
    leakage the design claims to prevent. Check (a) runs first and includes a length check, so a wrong-length
    `groups` raises ValueError before any indexing (never IndexError)."""
    groups = np.asarray(groups)
    n_rows = len(groups)
    all_val = np.concatenate([np.asarray(val) for _, val in cv_splits]) if cv_splits else np.array([], dtype=int)
    if len(all_val) != n_rows or not np.array_equal(np.sort(all_val), np.arange(n_rows)):
        raise ValueError(
            "Calibration (validation) index sets must together cover every row exactly once "
            f"(rows={n_rows}, validation indices={len(all_val)})"
        )
    for fold, (fit_idx, val_idx) in enumerate(cv_splits):
        fit_idx = np.asarray(fit_idx)
        val_idx = np.asarray(val_idx)
        if len(np.intersect1d(fit_idx, val_idx)):
            raise ValueError(f"Calibration fold {fold}: fit and validation index sets overlap")
        shared = np.intersect1d(groups[fit_idx], groups[val_idx])
        if len(shared):
            raise ValueError(
                f"Calibration fold {fold}: {len(shared)} patient(s) appear in both the fit and validation parts"
            )


def build_calibrated_xgboost(
    seed: int, cv_splits: list[tuple[np.ndarray, np.ndarray]], method: str = "sigmoid"
) -> CalibratedClassifierCV:
    """CalibratedClassifierCV around a (preprocess -> XGBClassifier) pipeline, with the supplied
    patient-grouped folds and ensemble=True: the returned model averages n_splits (base fit + calibrator)
    pairs, each fitted on (n_splits-1)/n_splits of the training rows -- no single model sees all of them.
    Default "sigmoid": isotonic regression overfits below roughly 1,000 positives, and with ensemble=True each
    calibrator is fitted on only one fold's worth of rows (about a fifth of the training set).

    cv_splits are positional indices, so this model MUST be fit on exactly the (X, y) rows the splits were
    built from; sklearn cannot check that, so call validate_cv_splits immediately before fitting."""
    if method not in _CALIBRATION_METHODS:
        raise ValueError(f"calibration method must be one of {_CALIBRATION_METHODS}; got {method!r}")
    base = Pipeline(
        [
            ("preprocess", build_tree_preprocessor()),
            ("model", XGBClassifier(**XGB_PARAMS, random_state=seed)),
        ]
    )
    return CalibratedClassifierCV(estimator=base, method=method, cv=cv_splits, ensemble=True)
