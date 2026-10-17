"""resolve_run_id, moved from scoring/runs.py to models/tracking.py in v2 slice 2 (the scorer reads bundles now; the
exporter and the README reporting resolve runs). Assertions carried over verbatim from tests/scoring/test_runs.py."""

from __future__ import annotations

import pytest

from readmission_risk.models.tracking import resolve_run_id
from tests.models.helpers import clone_run
from tests.scoring.helpers import SCORING_EXPERIMENT


def _clone(env, experiment, **kwargs):
    return clone_run(env.tracking_dir, env.lr_run_id, experiment=experiment, gold_frame=env.df, **kwargs)


def test_resolve_run_id_unique_and_ambiguous(scoring_env):
    env = scoring_env
    assert resolve_run_id(env.tracking_dir, SCORING_EXPERIMENT, "logistic_regression") == env.lr_run_id
    assert resolve_run_id(env.tracking_dir, SCORING_EXPERIMENT, "xgboost_calibrated") == env.xgb_run_id
    with pytest.raises(ValueError, match="model name"):
        resolve_run_id(env.tracking_dir, SCORING_EXPERIMENT, "random_forest")

    # two FINISHED runs for one model: refused, listing both ids (never "latest wins")
    first, second = _clone(env, "resolve-dup"), _clone(env, "resolve-dup")
    with pytest.raises(ValueError, match="--run-id") as info:
        resolve_run_id(env.tracking_dir, "resolve-dup", "logistic_regression")
    assert first in str(info.value) and second in str(info.value)

    # a FAILED run with the same tag does not count (a dropped status filter would see two)
    finished, _failed = _clone(env, "resolve-mixed"), _clone(env, "resolve-mixed", status="FAILED")
    assert resolve_run_id(env.tracking_dir, "resolve-mixed", "logistic_regression") == finished

    # a soft-deleted run does not count; an experiment with no XGBoost run has none to resolve
    deleted = _clone(env, "resolve-deleted-run")
    env.client().delete_run(deleted)
    with pytest.raises(ValueError, match="no FINISHED run"):
        resolve_run_id(env.tracking_dir, "resolve-deleted-run", "logistic_regression")
    with pytest.raises(ValueError, match="no FINISHED run"):
        resolve_run_id(env.tracking_dir, "resolve-mixed", "xgboost_calibrated")

    # a deleted experiment
    _clone(env, "resolve-deleted-experiment")
    env.client().delete_experiment(env.client().get_experiment_by_name("resolve-deleted-experiment").experiment_id)
    with pytest.raises(ValueError, match="deleted"):
        resolve_run_id(env.tracking_dir, "resolve-deleted-experiment", "logistic_regression")


def test_missing_tracking_db_is_refused_without_creating_it(tmp_path):
    absent = tmp_path / "no-store"
    empty_dir = tmp_path / "empty-store"
    empty_dir.mkdir()
    for directory in (absent, empty_dir):
        with pytest.raises(FileNotFoundError, match=r"mlflow\.db"):
            resolve_run_id(directory, SCORING_EXPERIMENT, "logistic_regression")
    assert not absent.exists()  # nothing created: no directory ...
    assert list(empty_dir.iterdir()) == []  # ... and no empty MLflow database


def test_missing_experiment_error_lists_available_names(scoring_env):
    with pytest.raises(ValueError) as info:
        resolve_run_id(scoring_env.tracking_dir, "readmission-risk", "logistic_regression")
    assert "not found" in str(info.value) and SCORING_EXPERIMENT in str(info.value)
