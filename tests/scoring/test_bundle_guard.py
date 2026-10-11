from __future__ import annotations

import os
import subprocess
import sys
import warnings
from pathlib import Path

import numpy as np
import pytest
from sklearn.exceptions import InconsistentVersionWarning

from readmission_risk.bundles.manifest import sha256_hex
from readmission_risk.models.data import (
    LABEL_SIDE_COLUMNS,
    gold_fingerprint,
    load_gold_table_with_metadata,
    prepare_features,
)
from readmission_risk.models.tracking import load_logged_model
from readmission_risk.scoring import bundle_guard
from readmission_risk.scoring.bundle_guard import (
    installed_versions,
    load_bundle_model,
    parse_python_major_minor,
    validate_bundle,
)
from tests.scoring.helpers import (
    OTHER_PATCH_PYTHON,
    forbid_model_loading,
    tamper_bundle,
)


@pytest.fixture(scope="module")
def loaded_gold(scoring_env):
    return load_gold_table_with_metadata(scoring_env.gold_dir)


def test_installed_versions_keys_match_manifest_and_training_tags(scoring_env):
    tags = scoring_env.client().get_run(scoring_env.lr_run_id).data.tags
    versions = installed_versions()
    assert set(versions) == {"sklearn_version", "xgboost_version", "mlflow_version", "pandas_version", "numpy_version"}
    assert set(versions) == set(scoring_env.manifest()["library_versions"])
    for name, installed in versions.items():
        assert tags[name] == installed  # a rename in training.py cannot silently disable guard 8


def test_installed_versions_never_imports_mlflow():
    code = (
        "import sys\n"
        "from readmission_risk.scoring.bundle_guard import installed_versions\n"
        "assert installed_versions()['mlflow_version'] != 'not installed'\n"
        "assert 'mlflow' not in sys.modules, 'mlflow was imported'\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr


@pytest.mark.parametrize("which", ["lr", "xgb"])
def test_valid_bundle_passes(scoring_env, loaded_gold, which):
    gold, metadata = loaded_gold
    uri = getattr(scoring_env, f"{which}_bundle_uri")
    bundle = validate_bundle(uri, gold=gold, metadata=metadata)
    assert bundle.bundle_uri == uri and bundle.bundle_id == Path(uri).name
    assert bundle.mlflow_run_id == getattr(scoring_env, f"{which}_run_id")
    assert bundle.model_name == ("logistic_regression" if which == "lr" else "xgboost_calibrated")
    assert bundle.test_start_date == "20230101"
    assert bundle.gold_fingerprint == gold_fingerprint(gold)
    assert int((bundle.partition_of == "train").sum()) == scoring_env.n_train
    assert int((bundle.partition_of == "test").sum()) == scoring_env.n_test
    assert bundle.partition_of.index.is_unique
    assert sha256_hex(bundle.model_bytes) == scoring_env.manifest(uri)["files"]["model.pkl"]["sha256"]


def test_champion_sha256_mismatch_is_refused(scoring_env, loaded_gold):
    gold, metadata = loaded_gold
    real = sha256_hex((Path(scoring_env.lr_bundle_uri) / "manifest.json").read_bytes())
    assert validate_bundle(scoring_env.lr_bundle_uri, gold=gold, metadata=metadata, expected_manifest_sha256=real)
    with pytest.raises(ValueError, match=r"^champion sha256: elsewhere/champion\.json pins manifest 0{64}"):
        validate_bundle(
            scoring_env.lr_bundle_uri, gold=gold, metadata=metadata, expected_manifest_sha256="0" * 64,
            champion_path=Path("elsewhere/champion.json"),
        )  # fmt: skip


# --------------------------------------------------------------------------------------------- guard 14 (Python version)
# The refusals are BUNDLE_CASES 14-*; these are the inputs guard 14 must let through (ported from the MLflow-run tests).
@pytest.mark.parametrize(
    "edits",
    [
        pytest.param({"python_version": None}, id="P1-legacy-run-without-tag-passes-on-mlmodel-fallback"),
        pytest.param({"python_version": OTHER_PATCH_PYTHON, "mlmodel_python_version": OTHER_PATCH_PYTHON}, id="P2-patch-difference-passes"),
        pytest.param({"mlmodel_python_version": OTHER_PATCH_PYTHON}, id="P3-micro-disagreement-between-tag-and-mlmodel-passes"),
        pytest.param({"mlmodel_python_version": None}, id="P4-tag-alone-passes-without-mlmodel-python-version"),
        pytest.param({"python_version": None, "mlmodel_python_version": OTHER_PATCH_PYTHON}, id="P5-legacy-run-with-patch-difference-passes"),
    ],
)
def test_python_version_guard_passes(scoring_env, loaded_gold, tmp_path, edits):
    gold, metadata = loaded_gold
    uri = tamper_bundle(scoring_env.lr_bundle_uri, tmp_path, set=edits)
    assert validate_bundle(uri, gold=gold, metadata=metadata).mlflow_run_id == scoring_env.lr_run_id


def test_parse_python_major_minor():
    assert parse_python_major_minor("3.14.7") == (3, 14)
    assert parse_python_major_minor("3.12") == (3, 12)
    assert parse_python_major_minor("10.1.0") == (10, 1)
    for bad in ("3", "3.x", "", " 3.14.7", "3.14.7rc1", "3.14.7.1", "3.14.7\n", "３.１４", 3.12, None):
        assert parse_python_major_minor(bad) is None, bad


# --------------------------------------------------------------------------------------------- loading
def test_load_bundle_model_round_trip(scoring_env, loaded_gold):
    gold, metadata = loaded_gold
    features = prepare_features(scoring_env.df.drop(columns=list(LABEL_SIDE_COLUMNS)))
    for which in ("lr", "xgb"):
        bundle = validate_bundle(getattr(scoring_env, f"{which}_bundle_uri"), gold=gold, metadata=metadata)
        ours = load_bundle_model(bundle).predict_proba(features)[:, 1]
        oracle = load_logged_model(scoring_env.tracking_dir, getattr(scoring_env, f"{which}_run_id")).predict_proba(features)[:, 1]
        assert np.max(np.abs(ours - oracle)) == 0  # byte-identical pickle, bit-identical scores


def test_load_bundle_model_escalates_inconsistent_version_warning(scoring_env, loaded_gold, monkeypatch):
    gold, metadata = loaded_gold
    bundle = validate_bundle(scoring_env.lr_bundle_uri, gold=gold, metadata=metadata)

    def warn_then_load(data):
        warnings.warn(InconsistentVersionWarning(estimator_name="X", current_sklearn_version="0", original_sklearn_version="1"), stacklevel=2)
        raise AssertionError("the warning was not turned into an error")

    monkeypatch.setattr(bundle_guard.cloudpickle, "loads", warn_then_load)
    with pytest.raises(InconsistentVersionWarning):
        load_bundle_model(bundle)


def test_load_bundle_model_refuses_an_object_without_predict_proba(scoring_env, loaded_gold, monkeypatch):
    gold, metadata = loaded_gold
    bundle = validate_bundle(scoring_env.lr_bundle_uri, gold=gold, metadata=metadata)
    monkeypatch.setattr(bundle_guard.cloudpickle, "loads", lambda data: object())
    with pytest.raises(RuntimeError, match="predict_proba"):
        load_bundle_model(bundle)


def test_forbid_model_loading_catches_the_real_loader(scoring_env, loaded_gold, monkeypatch):
    gold, metadata = loaded_gold
    bundle = validate_bundle(scoring_env.lr_bundle_uri, gold=gold, metadata=metadata)
    forbid_model_loading(monkeypatch)
    with pytest.raises(AssertionError, match="deserialized"):
        load_bundle_model(bundle)


def test_unreadable_bundle_file_is_a_keyed_value_error(scoring_env, loaded_gold, tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root reads files regardless of permissions")
    gold, metadata = loaded_gold
    uri = tamper_bundle(scoring_env.lr_bundle_uri, tmp_path)
    os.chmod(Path(uri) / "model.pkl", 0)
    try:
        with pytest.raises(ValueError, match=r"^files: cannot read bundle at"):
            validate_bundle(uri, gold=gold, metadata=metadata)
    finally:
        os.chmod(Path(uri) / "model.pkl", 0o644)
