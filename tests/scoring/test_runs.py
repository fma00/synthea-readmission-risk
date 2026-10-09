from __future__ import annotations

from types import SimpleNamespace

import pytest

from readmission_risk.models.data import gold_fingerprint, load_gold_table_with_metadata
from readmission_risk.scoring.runs import (
    _mlmodel_python_version,
    installed_versions,
    parse_python_major_minor,
    resolve_run_id,
    validate_run,
)
from tests.scoring.helpers import (
    NO_PYTHON_TAG,
    OTHER_PATCH_PYTHON,
    RUNNING_PYTHON,
    SCORING_EXPERIMENT,
    clone_run,
)


@pytest.fixture(scope="module")
def loaded_gold(scoring_env):
    return load_gold_table_with_metadata(scoring_env.gold_dir)


def _clone(env, experiment, **kwargs):
    return clone_run(env.tracking_dir, env.lr_run_id, experiment=experiment, gold_frame=env.df, **kwargs)


def test_installed_versions_keys_match_training_tags(scoring_env):
    tags = scoring_env.client().get_run(scoring_env.lr_run_id).data.tags
    versions = installed_versions()
    assert set(versions) == {"sklearn_version", "xgboost_version", "mlflow_version", "pandas_version", "numpy_version"}
    for name, installed in versions.items():
        assert tags[name] == installed  # a rename in training.py cannot silently disable guard 8


@pytest.mark.parametrize("which", ["lr_run_id", "xgb_run_id"])
def test_valid_run_passes_all_guards(scoring_env, loaded_gold, which):
    gold, metadata = loaded_gold
    run_id = getattr(scoring_env, which)
    run = validate_run(scoring_env.tracking_dir, run_id, gold=gold, metadata=metadata)
    assert run.run_id == run_id
    assert run.model_name == ("logistic_regression" if which == "lr_run_id" else "xgboost_calibrated")
    assert run.test_start_date == "20230101"
    assert run.gold_fingerprint == gold_fingerprint(gold)
    assert int((run.partition_of == "train").sum()) == scoring_env.n_train
    assert int((run.partition_of == "test").sum()) == scoring_env.n_test
    assert run.partition_of.index.is_unique


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


def test_missing_tracking_db_is_refused_without_creating_it(scoring_env, loaded_gold, tmp_path):
    gold, metadata = loaded_gold
    absent = tmp_path / "no-store"
    empty_dir = tmp_path / "empty-store"
    empty_dir.mkdir()
    for directory in (absent, empty_dir):
        with pytest.raises(FileNotFoundError, match=r"mlflow\.db"):
            resolve_run_id(directory, SCORING_EXPERIMENT, "logistic_regression")
        with pytest.raises(FileNotFoundError, match=r"mlflow\.db"):
            validate_run(directory, scoring_env.lr_run_id, gold=gold, metadata=metadata)
    assert not absent.exists()  # nothing created: no directory ...
    assert list(empty_dir.iterdir()) == []  # ... and no empty MLflow database


def test_missing_experiment_error_lists_available_names(scoring_env):
    with pytest.raises(ValueError) as info:
        resolve_run_id(scoring_env.tracking_dir, "readmission-risk", "logistic_regression")
    assert "not found" in str(info.value) and SCORING_EXPERIMENT in str(info.value)


# --------------------------------------------------------------------------------------------- guard 14 (Python version)
# The refusals are GUARD_CASES 14-*; these are the inputs guard 14 must let through.
@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"drop_tags": NO_PYTHON_TAG}, id="P1-legacy-run-without-tag-passes-on-mlmodel-fallback"),
        pytest.param({"tags": {"python_version": OTHER_PATCH_PYTHON}, "mlmodel_python_version": OTHER_PATCH_PYTHON}, id="P2-patch-difference-passes"),
        pytest.param({"mlmodel_python_version": OTHER_PATCH_PYTHON}, id="P3-micro-disagreement-between-tag-and-mlmodel-passes"),
        pytest.param({"drop_mlmodel_python_version": True}, id="P4-tag-alone-passes-without-mlmodel-python-version"),
        pytest.param({"drop_tags": NO_PYTHON_TAG, "mlmodel_python_version": OTHER_PATCH_PYTHON}, id="P5-legacy-run-with-patch-difference-passes"),
    ],
)
def test_python_version_guard_passes(scoring_env, loaded_gold, kwargs):
    gold, metadata = loaded_gold
    run_id = _clone(scoring_env, "python-version-passes", **kwargs)
    tags = scoring_env.client().get_run(run_id).data.tags
    assert tags.get("python_version") == (None if "drop_tags" in kwargs else kwargs.get("tags", {}).get("python_version", RUNNING_PYTHON))
    assert validate_run(scoring_env.tracking_dir, run_id, gold=gold, metadata=metadata).run_id == run_id


def test_parse_python_major_minor():
    assert parse_python_major_minor("3.14.7") == (3, 14)
    assert parse_python_major_minor("3.12") == (3, 12)
    assert parse_python_major_minor("10.1.0") == (10, 1)
    for bad in ("3", "3.x", "", " 3.14.7", "3.14.7rc1", "3.14.7.1", "3.14.7\n", "\uff13.\uff11\uff14", 3.12, None):
        assert parse_python_major_minor(bad) is None, bad


def test_mlmodel_python_version_treats_non_dict_as_absent():
    for flavors in ({"python_function": "x"}, {"python_function": ["x"]}, "x", None, {}):
        assert _mlmodel_python_version(SimpleNamespace(flavors=flavors)) is None, flavors
    assert _mlmodel_python_version(SimpleNamespace(flavors={"python_function": {"python_version": "3.12.11"}})) == "3.12.11"
