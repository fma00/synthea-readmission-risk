"""The portable export (v2 slice 6c): schema-2 bundles that the scorer accepts and that self-verify, idempotence, the
refusals (source, parity, unsupported model shapes), and the probe rule. See
notes/eg-new-feature/v2-slice6c-portable-v1-export-2026-10-17.md (Verification criteria)."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sklearn.calibration import CalibratedClassifierCV
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from xgboost import XGBClassifier

from readmission_risk.bundles.manifest import MODEL_FILENAME, SPLIT_FILENAME
from readmission_risk.models.data import (
    CATEGORICAL_FEATURES,
    load_gold_table_with_metadata,
    prepare_features,
)
from readmission_risk.models.features import (
    build_linear_preprocessor,
    build_tree_preprocessor,
)
from readmission_risk.portable import predictor
from readmission_risk.portable.export import (
    PROBE_BASE_ROWS,
    PROBE_UNSEEN,
    build_probes,
    export_portable_bundle,
    extract_portable,
)
from readmission_risk.portable.predictor import read_portable_bundle, verify_reference
from readmission_risk.scoring.bundle_guard import validate_bundle
from tests.portable.helpers import pickle_and_test_rows, portable_document
from tests.scoring.helpers import tamper_bundle

COPIED_KEYS = {"schema_version", "files", "portable", "bundle_id"}  # the only keys a schema-2 manifest may change


def _manifest(uri: str) -> dict:
    return json.loads((Path(uri) / "manifest.json").read_text())


@pytest.mark.parametrize("which", ["lr", "xgb"])
def test_export_creates_valid_schema2_bundles(which, scoring_env, portable_env):
    source = scoring_env.lr_bundle_uri if which == "lr" else scoring_env.xgb_bundle_uri
    result = portable_env.lr_result if which == "lr" else portable_env.xgb_result
    gold, metadata = load_gold_table_with_metadata(scoring_env.gold_dir)
    bundle = validate_bundle(result.bundle_uri, gold=gold, metadata=metadata)
    assert bundle.bundle_id == result.bundle_id == Path(result.bundle_uri).name

    new, old = _manifest(result.bundle_uri), _manifest(source)
    assert {k: v for k, v in new.items() if k not in COPIED_KEYS} == {k: v for k, v in old.items() if k not in COPIED_KEYS}
    assert new["schema_version"] == 2 and old["schema_version"] == 1
    for name in (MODEL_FILENAME, SPLIT_FILENAME):
        assert (Path(result.bundle_uri) / name).read_bytes() == (Path(source) / name).read_bytes()
        assert new["files"][name] == old["files"][name]

    _, X = pickle_and_test_rows(scoring_env.gold_dir, source)
    probes = build_probes(X, portable_document(result.bundle_uri))
    assert new["portable"]["parity"] == {"rows": len(X), "probe_rows": len(probes), "max_abs_diff": 0.0}
    assert (result.parity_rows, result.probe_rows, result.max_abs_diff) == (len(X), len(probes), 0.0)
    assert result.source_bundle_id == old["bundle_id"] and result.model_name == old["model_name"]
    assert len(os.listdir(result.bundle_uri)) == (5 if which == "lr" else 10)
    assert set(new["portable"]["writer_versions"]) == {"numpy", "scipy", "xgboost", "python", "platform", "blas"}


@pytest.mark.parametrize("which", ["lr", "xgb"])
def test_exported_bundle_self_verifies(which, portable_env):
    model, reference = read_portable_bundle(portable_env.lr_portable_uri if which == "lr" else portable_env.xgb_portable_uri)
    assert verify_reference(model, reference) == 0.0


def test_export_is_idempotent(scoring_env, portable_env):
    again = export_portable_bundle(scoring_env.lr_bundle_uri, scoring_env.gold_dir, portable_env.bundles_root)
    assert (again.bundle_id, again.created) == (portable_env.lr_result.bundle_id, False)


def test_export_refuses_schema2_source(scoring_env, portable_env, tmp_path):
    with pytest.raises(ValueError, match=r"^portable export: bundle [0-9a-f]{12} is already schema 2"):
        export_portable_bundle(portable_env.lr_portable_uri, scoring_env.gold_dir, tmp_path)


def test_export_refuses_gs_source(scoring_env, tmp_path):
    with pytest.raises(ValueError, match=r"^portable export: the source bundle must be a local path"):
        export_portable_bundle("gs://bucket/" + "0" * 64, scoring_env.gold_dir, tmp_path)


def test_export_source_failing_a_guard_is_refused(scoring_env, tmp_path):
    bad = tamper_bundle(scoring_env.lr_bundle_uri, tmp_path / "src", set={"params.reference_date": "20260917"})
    with pytest.raises(ValueError, match="reference_date"):
        export_portable_bundle(bad, scoring_env.gold_dir, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_export_refuses_on_parity_failure_and_writes_nothing(scoring_env, tmp_path, monkeypatch):
    real = predictor.load_portable

    class OffByOneUlp:
        def __init__(self, model):
            self.model, self.model_name = model, model.model_name

        def predict_proba(self, X):
            proba = self.model.predict_proba(X)
            proba[0, 1] = np.nextafter(proba[0, 1], 1.0)
            return proba

    monkeypatch.setattr("readmission_risk.portable.predictor.load_portable", lambda files: OffByOneUlp(real(files)))
    with pytest.raises(ValueError, match=r"^portable export: parity failed for bundle [0-9a-f]{12}: portable: reference mismatch"):
        export_portable_bundle(scoring_env.lr_bundle_uri, scoring_env.gold_dir, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_export_does_not_mutate_gold(scoring_env, tmp_path, monkeypatch):
    gold, metadata = load_gold_table_with_metadata(scoring_env.gold_dir)
    before = gold.copy(deep=True)
    # the exporter calls the loader through the module (gold_data.load_gold_table_with_metadata), so this patch reaches it
    monkeypatch.setattr("readmission_risk.models.data.load_gold_table_with_metadata", lambda _dir: (gold, metadata))
    export_portable_bundle(scoring_env.lr_bundle_uri, scoring_env.gold_dir, tmp_path / "out")
    pd.testing.assert_frame_equal(gold, before)


# --------------------------------------------------------------------------------------------- probes
def test_build_probes_formula_and_determinism(scoring_env, portable_env):
    _, X = pickle_and_test_rows(scoring_env.gold_dir, scoring_env.xgb_bundle_uri)
    document = portable_document(portable_env.xgb_portable_uri)
    probes = build_probes(X, document)
    pd.testing.assert_frame_equal(probes, build_probes(X, document))
    specs = [s for fold in document["folds"] for s in fold["preprocess"]["one_hot"]]
    per_column = {
        c: {v for s in specs if s["column"] == c for v in (*s["categories"], *s["infrequent_categories"])} for c in CATEGORICAL_FEATURES
    }
    assert len(probes) == PROBE_BASE_ROWS * sum(1 + len(values | {"MISSING"}) for values in per_column.values())
    for column, values in per_column.items():
        assert values | {PROBE_UNSEEN, "MISSING"} <= set(probes[column])
    assert list(probes.columns) == list(X.columns)


def test_build_probes_refuses_a_fitted_unseen_marker(scoring_env, portable_env):
    _, X = pickle_and_test_rows(scoring_env.gold_dir, scoring_env.lr_bundle_uri)
    document = copy.deepcopy(portable_document(portable_env.lr_portable_uri))
    document["preprocess"]["one_hot"][0]["categories"].append(PROBE_UNSEEN)
    with pytest.raises(ValueError, match=r"^portable export: probe value '__portable_probe_unseen__' is a fitted category"):
        build_probes(X, document)


# --------------------------------------------------------------------------------------------- unsupported shapes
@pytest.fixture(scope="module")
def training(scoring_env):
    X = prepare_features(scoring_env.df)
    return X, scoring_env.df["is_readmitted"].to_numpy()


def _lr(X, y, **ct_params):
    pre = build_linear_preprocessor()
    pre.set_params(**ct_params)
    return Pipeline([("preprocess", pre), ("model", LogisticRegression(max_iter=500))]).fit(X, y)


def _calibrated(X, y, *, method="sigmoid", ensemble=True, **xgb):
    base = Pipeline([("preprocess", build_tree_preprocessor()), ("model", XGBClassifier(n_estimators=5, max_depth=2, **xgb))])
    return CalibratedClassifierCV(estimator=base, method=method, cv=3, ensemble=ensemble).fit(X, y)


def _early_stopped(X, y):
    model = _calibrated(X, y)
    model.calibrated_classifiers_[0].estimator.named_steps["model"].get_booster().set_attr(best_iteration="2")
    return model


def _renamed(X, y):
    pre = build_linear_preprocessor()
    pre.transformers[0] = ("kounts", *pre.transformers[0][1:])
    return Pipeline([("preprocess", pre), ("model", LogisticRegression(max_iter=500))]).fit(X, y)


def _extra_step(X, y):
    steps = [("preprocess", build_linear_preprocessor()), ("noop", "passthrough"), ("model", LogisticRegression(max_iter=500))]
    return Pipeline(steps).fit(X, y)


def _pandas_output(X, y):
    return Pipeline([("preprocess", build_linear_preprocessor()), ("model", LogisticRegression(max_iter=500))]).set_output(
        transform="pandas"
    ).fit(X, y)


SHAPE_CASES = [
    ("encoder-drop-first", lambda X, y: _lr(X, y, cats__drop="first"), "OneHotEncoder must have"),
    ("encoder-sparse", lambda X, y: _lr(X, y, cats__sparse_output=True), "OneHotEncoder must have|dense output"),
    ("log1p-kw-args", lambda X, y: _lr(X, y, counts__log1p__kw_args={}), "log1p step"),
    ("renamed-transformer", _renamed, "transformers are"),
    ("extra-pipeline-step", _extra_step, "pipeline steps are"),
    ("pandas-output", _pandas_output, "set_output"),
    ("isotonic", lambda X, y: _calibrated(X, y, method="isotonic"), "method='sigmoid'"),
    ("not-ensemble", lambda X, y: _calibrated(X, y, ensemble=False), "ensemble=True"),
    ("early-stopped", _early_stopped, "iteration_range"),
    ("dart", lambda X, y: _calibrated(X, y, booster="dart"), "gbtree"),
]  # fmt: skip


@pytest.mark.parametrize(("case_id", "build", "message"), SHAPE_CASES, ids=[c[0] for c in SHAPE_CASES])
def test_extract_refuses_unsupported_shapes(case_id, build, message, training):
    model = build(*training)
    with pytest.raises(ValueError, match=rf"^portable export: unsupported model shape: .*({message})"):
        extract_portable(model)


def test_extract_accepts_the_real_shapes(training):
    X, y = training
    for model in (_lr(X, y), _calibrated(X, y)):
        document, boosters = extract_portable(model)
        portable = predictor.load_portable({"portable.json": json.dumps(document).encode(), **boosters})
        assert np.array_equal(portable.predict_proba(X), model.predict_proba(X))
