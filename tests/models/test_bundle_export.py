from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from readmission_risk.bundles.manifest import (
    compute_bundle_id,
    file_entry,
    manifest_bytes,
    sha256_hex,
)
from readmission_risk.models.bundle_export import _mlmodel_python_version, export_bundle
from readmission_risk.models.data import (
    FEATURE_COLUMNS,
    LABEL_SIDE_COLUMNS,
    prepare_features,
)
from readmission_risk.models.tracking import load_logged_model
from tests.models.helpers import clone_run


def _clone(env, experiment, **kwargs):
    return clone_run(env.tracking_dir, env.lr_run_id, experiment=experiment, gold_frame=env.df, **kwargs)


def _manifest(uri: str) -> dict:
    return json.loads((Path(uri) / "manifest.json").read_text())


def _logged_model_dir(env, run_id: str) -> Path:
    """The logged model's directory, found independently of the exporter (through the MLflow client)."""
    from urllib.parse import urlparse
    from urllib.request import url2pathname

    model_id = env.client().get_run(run_id).data.tags["model_uri"].split("/")[-1]
    return Path(url2pathname(urlparse(env.client().get_logged_model(model_id).artifact_location).path))


@pytest.mark.parametrize("which", ["lr", "xgb"])
def test_manifest_records_the_run_faithfully(scoring_env, which):
    env = scoring_env
    run_id, uri = getattr(env, f"{which}_run_id"), getattr(env, f"{which}_bundle_uri")
    run = env.client().get_run(run_id)
    tags, params = run.data.tags, run.data.params
    manifest = _manifest(uri)
    assert manifest["schema_version"] == 1 and manifest["portable"] is None
    assert manifest["model_name"] == tags["model_name"] == ("logistic_regression" if which == "lr" else "xgboost_calibrated")
    assert manifest["gold_label_definition"] == tags["gold_label_definition"]
    assert manifest["gold_fingerprint"] == tags["gold_fingerprint"]
    assert manifest["gold_fingerprint_definition"] == "pandas-csv-sha256-v1"
    assert manifest["params"] == {k: params[k] for k in ("reference_date", "readmission_window_days", "test_start_date")}
    assert manifest["library_versions"] == {
        k: tags[k] for k in ("sklearn_version", "xgboost_version", "mlflow_version", "pandas_version", "numpy_version")
    }
    assert manifest["python_version"] == tags["python_version"]
    mlmodel = (_logged_model_dir(env, run_id) / "MLmodel").read_text()
    assert f"python_version: {manifest['mlmodel_python_version']}" in mlmodel
    assert manifest["feature_columns"] == list(FEATURE_COLUMNS)
    assert [s["name"] for s in manifest["signature"]] == list(FEATURE_COLUMNS)
    assert all(s["required"] is True and s["type"] in ("double", "string") for s in manifest["signature"])
    assert manifest["mlflow"] == {
        "run_id": run_id,
        "run_status": "FINISHED",
        "run_lifecycle_stage": "active",
        "model_logged_by_run_id": run_id,
        "model_uri": tags["model_uri"],
    }
    assert manifest["git"] == {"commit": tags.get("mlflow.source.git.commit"), "dirty": tags.get("git_dirty")}
    assert manifest["bundle_id"] == compute_bundle_id(manifest) == Path(uri).name


def test_model_and_split_bytes_are_copied_verbatim(scoring_env):
    env = scoring_env
    model = (Path(env.lr_bundle_uri) / "model.pkl").read_bytes()
    assert model == (_logged_model_dir(env, env.lr_run_id) / "model.pkl").read_bytes()
    assert (Path(env.lr_bundle_uri) / "split_assignment.csv").read_text() == env.assignment_csv()
    files = _manifest(env.lr_bundle_uri)["files"]
    assert files["model.pkl"] == file_entry("model.pkl", model)


def test_export_is_deterministic_across_roots(scoring_env, tmp_path):
    env = scoring_env
    a = export_bundle(env.tracking_dir, env.lr_run_id, tmp_path / "a")
    b = export_bundle(env.tracking_dir, env.lr_run_id, tmp_path / "b")
    assert a.bundle_id == b.bundle_id == Path(env.lr_bundle_uri).name
    for name in ("manifest.json", "model.pkl", "split_assignment.csv"):
        assert (Path(a.bundle_uri) / name).read_bytes() == (Path(b.bundle_uri) / name).read_bytes(), name
    assert a.manifest_sha256 == sha256_hex((Path(a.bundle_uri) / "manifest.json").read_bytes())


def test_reexport_into_same_root_is_idempotent(scoring_env):
    env = scoring_env
    again = export_bundle(env.tracking_dir, env.lr_run_id, env.bundles_root)
    assert again.created is False and again.bundle_uri == env.lr_bundle_uri


def test_exported_model_loads_without_mlflow(scoring_env, tmp_path):
    env = scoring_env
    features = prepare_features(env.df.drop(columns=list(LABEL_SIDE_COLUMNS)))
    features_path = tmp_path / "features.parquet"
    features.to_parquet(features_path)
    out = tmp_path / "scores.json"
    code = (
        "import json, sys\n"
        "import cloudpickle, pandas as pd\n"
        f"model = cloudpickle.loads(open({str(Path(env.xgb_bundle_uri) / 'model.pkl')!r}, 'rb').read())\n"
        f"scores = model.predict_proba(pd.read_parquet({str(features_path)!r}))[:, 1]\n"
        "assert 'mlflow' not in sys.modules, 'mlflow was imported'\n"
        f"json.dump([float(s) for s in scores], open({str(out)!r}, 'w'))\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
    oracle = load_logged_model(env.tracking_dir, env.xgb_run_id).predict_proba(features)[:, 1]
    assert json.loads(out.read_text()) == [float(s) for s in oracle]  # max abs diff 0


# --------------------------------------------------------------------------------------------- refusals
def test_missing_store_is_refused_without_creating_it(tmp_path):
    with pytest.raises(FileNotFoundError, match=r"mlflow\.db"):
        export_bundle(tmp_path / "no-store", "0" * 32, tmp_path / "bundles")
    assert not (tmp_path / "no-store").exists() and not (tmp_path / "bundles").exists()


@pytest.mark.parametrize(
    "kwargs, message",
    [
        pytest.param({"drop_tags": ("model_uri",)}, "no model_uri tag", id="no-model-uri"),
        pytest.param({"tags": {"model_uri": "models:/m-doesnotexist"}}, "cannot read the logged model", id="ex-10-bad-model-uri"),
        pytest.param({"drop_artifacts": ("split_assignment.csv",)}, "split_assignment.csv", id="no-split-artifact"),
        pytest.param({"drop_artifacts": ("feature_columns.json",)}, "feature_columns.json", id="ex-9-no-artifact"),
        pytest.param({"features_json": "not json"}, "is not JSON", id="ex-9-not-json"),
        pytest.param({"features_json": "[1, 2]"}, "not a JSON object", id="features-not-an-object"),
        pytest.param({"mlmodel_sklearn_serialization_format": "skops"}, "cloudpickle", id="skops-format"),
        pytest.param({"mlmodel_sklearn_code": "code"}, "code paths", id="code-paths"),
    ],
)
def test_export_refuses_what_it_cannot_read(scoring_env, tmp_path, kwargs, message):
    run_id = _clone(scoring_env, "export-refusals", **kwargs)
    with pytest.raises(ValueError, match=message):
        export_bundle(scoring_env.tracking_dir, run_id, tmp_path / "bundles")
    assert not (tmp_path / "bundles").exists()


def test_unknown_run_is_refused(scoring_env, tmp_path):
    with pytest.raises(ValueError, match="run not found"):
        export_bundle(scoring_env.tracking_dir, "0" * 32, tmp_path / "bundles")


# --------------------------------------------------------------------------------------------- faithful recording
def _export_clone(env, tmp_path, **kwargs) -> dict:
    run_id = _clone(env, "export-faithful", **kwargs)
    return _manifest(export_bundle(env.tracking_dir, run_id, tmp_path / "bundles").bundle_uri)


def test_missing_python_version_tag_is_recorded_as_null(scoring_env, tmp_path):
    manifest = _export_clone(scoring_env, tmp_path, drop_tags=("python_version",))
    assert manifest["python_version"] is None and manifest["mlmodel_python_version"] is not None


def test_non_dict_python_function_flavor_is_recorded_as_null(scoring_env, tmp_path):
    # ex-GUARD_CASE 14-non-dict-python-function-flavor: the exporter treats it as absent; the scorer then refuses
    manifest = _export_clone(scoring_env, tmp_path, drop_tags=("python_version",), mlmodel_python_function_flavor="x")
    assert manifest["mlmodel_python_version"] is None


def test_yaml_float_python_version_is_recorded_as_a_number(scoring_env, tmp_path):
    manifest = _export_clone(scoring_env, tmp_path, mlmodel_python_version=3.1)
    assert manifest["mlmodel_python_version"] == 3.1 and type(manifest["mlmodel_python_version"]) is float


def test_missing_features_key_and_status_are_recorded(scoring_env, tmp_path):
    manifest = _export_clone(scoring_env, tmp_path, features_json='{"nofeatures": 1}', status="FAILED")
    assert manifest["feature_columns"] is None and manifest["mlflow"]["run_status"] == "FAILED"
    assert manifest_bytes(manifest)  # still a well-formed, self-consistent manifest


def test_mlmodel_python_version_treats_non_dict_as_absent():
    for flavors in ({"python_function": "x"}, {"python_function": ["x"]}, "x", None, {}):
        assert _mlmodel_python_version(SimpleNamespace(flavors=flavors)) is None, flavors
    assert _mlmodel_python_version(SimpleNamespace(flavors={"python_function": {"python_version": "3.12.11"}})) == "3.12.11"


def test_export_restores_the_tracking_uri(scoring_env, tmp_path):
    import mlflow

    mlflow.set_tracking_uri(f"file://{tmp_path / 'elsewhere'}")
    before = mlflow.get_tracking_uri()
    export_bundle(scoring_env.tracking_dir, scoring_env.lr_run_id, tmp_path / "bundles")
    assert mlflow.get_tracking_uri() == before
    assert os.path.isdir(tmp_path / "bundles")


def test_export_uses_rowhash_when_tag_present(scoring_env, tmp_path):
    """v2 slice 5: a run trained on version-2 gold carries the Spark-computed fingerprint; its bundle names spark-rowhash-v1.
    The untagged scoring_env runs keep pandas-csv-sha256-v1 (test_manifest_records_the_run_faithfully is that oracle)."""
    from readmission_risk.models.constants import ROWHASH_TAG

    env = scoring_env
    run_id = _clone(env, "rowhash-tagged", tags={ROWHASH_TAG: "b" * 64})
    manifest = _manifest(export_bundle(env.tracking_dir, run_id, tmp_path / "bundles").bundle_uri)
    assert manifest["gold_fingerprint_definition"] == "spark-rowhash-v1"
    assert manifest["gold_fingerprint"] == "b" * 64
