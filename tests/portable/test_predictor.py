"""The portable predictor (v2 slice 6c): exact parity with the pickles, the one-hot semantics, the input contract, strict
loading of portable.json and the reference, read_portable_bundle's integrity checks, and the import rule. See
notes/eg-new-feature/v2-slice6c-portable-v1-export-2026-10-17.md (Verification criteria)."""

from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xgboost

from readmission_risk.bundles.manifest import (
    PORTABLE_PARAMS_FILENAME,
    booster_filename,
    file_entry,
)
from readmission_risk.models.constants import MODEL_NAMES
from readmission_risk.models.data import CATEGORICAL_FEATURES, FEATURE_COLUMNS
from readmission_risk.portable import predictor
from readmission_risk.portable.export import PROBE_UNSEEN, build_probes
from readmission_risk.portable.predictor import (
    OneHotSpec,
    Reference,
    load_portable,
    parse_reference,
    read_portable_bundle,
    verify_reference,
)
from tests.portable.helpers import (
    assert_same,
    load,
    pickle_and_test_rows,
    portable_document,
    portable_files,
    with_value,
)
from tests.scoring.helpers import tamper_bundle

PREDICTOR_SOURCE = Path(__file__).resolve().parents[2] / "src" / "readmission_risk" / "portable" / "predictor.py"


@pytest.fixture(scope="module")
def lr(scoring_env, portable_env):
    model, X = pickle_and_test_rows(scoring_env.gold_dir, scoring_env.lr_bundle_uri)
    return model, X, load(portable_env.lr_portable_uri)


@pytest.fixture(scope="module")
def xgb(scoring_env, portable_env):
    model, X = pickle_and_test_rows(scoring_env.gold_dir, scoring_env.xgb_bundle_uri)
    return model, X, load(portable_env.xgb_portable_uri)


def test_model_names_match_the_training_constants():
    assert (predictor.LOGISTIC_REGRESSION, predictor.XGBOOST_CALIBRATED) == MODEL_NAMES


# --------------------------------------------------------------------------------------------- parity
def test_lr_portable_matches_pickle_exactly(lr):
    pickled, X, portable = lr
    assert len(X) >= 100  # fixture precondition: a real batch, not a toy
    assert_same(portable.predict_proba(X), pickled.predict_proba(X))


def test_xgb_portable_matches_pickle_exactly(xgb):
    pickled, X, portable = xgb
    assert len(portable.folds) == 5
    assert_same(portable.predict_proba(X), pickled.predict_proba(X))


@pytest.mark.parametrize("which", ["lr", "xgb"])
def test_every_fitted_category_unseen_and_missing_match_pickle(which, request, portable_env):
    pickled, X, portable = request.getfixturevalue(which)
    uri = portable_env.lr_portable_uri if which == "lr" else portable_env.xgb_portable_uri
    probes = build_probes(X, portable_document(uri))
    assert PROBE_UNSEEN in set(probes["gender"]) and "MISSING" in set(probes["race"])
    assert_same(portable.predict_proba(probes), pickled.predict_proba(probes))


def test_one_hot_semantics():
    with_bucket = OneHotSpec("c", np.asarray(["a", "b"], dtype=object), ("z",), True)
    without = OneHotSpec("d", np.asarray(["a", "b"], dtype=object), (), False)
    values = np.asarray(["a", "b", "z", "never-seen"], dtype=object)
    assert with_bucket.output_names() == ["c_a", "c_b", "c_infrequent_sklearn"]
    assert without.output_names() == ["d_a", "d_b"]
    assert with_bucket.transform(values).tolist() == [[1, 0, 0], [0, 1, 0], [0, 0, 1], [0, 0, 1]]
    assert without.transform(values).tolist() == [[1, 0], [0, 1], [0, 0], [0, 0]]
    assert with_bucket.transform(values).dtype == np.float64


def test_input_columns_by_name_extra_ignored_missing_refused(lr):
    pickled, X, portable = lr
    shuffled = X[list(reversed(X.columns))].assign(extra=1.0)
    assert_same(portable.predict_proba(shuffled), pickled.predict_proba(X))
    with pytest.raises(ValueError, match=r"^portable: input is missing column\(s\) \['gender'\]"):
        portable.predict_proba(X.drop(columns=["gender"]))


def _float64_with_na(X: pd.DataFrame) -> pd.DataFrame:
    out = X.copy()
    out["age_at_index_years"] = pd.array([pd.NA, *out["age_at_index_years"].iloc[1:]], dtype="Float64")
    return out


@pytest.mark.parametrize(
    ("which", "make", "message"),
    [
        ("lr", lambda X: X.iloc[:0], "input has no rows"),
        ("lr", lambda X: {"gender": X["gender"]}, "input has no columns attribute"),
        ("lr", lambda X: pd.concat([X, X[["gender"]]], axis=1), r"input has duplicate column\(s\) \['gender'\]"),
        # pandas 2.3.3 turns pd.NA into NaN here (measured; the design expected a raise), so LR refuses it as non-finite
        ("lr", _float64_with_na, "non-finite numeric input in age_at_index_years"),
        ("lr", lambda X: with_value(X, "age_at_index_years", "abc"), "cannot convert column age_at_index_years"),
        ("lr", lambda X: with_value(X, "prior_encounter_count", -1.0, dtype=np.float64), "non-finite value after the linear transform"),
        ("lr", lambda X: with_value(X, "prior_encounter_count", -2.0, dtype=np.float64), "non-finite value after the linear transform"),
        ("lr", lambda X: with_value(X, "age_at_index_years", np.nan, dtype=np.float64), "non-finite numeric input in age_at_index_years"),
        ("xgb", lambda X: with_value(X, "age_at_index_years", np.inf, dtype=np.float64), "non-finite numeric input in age_at_index_years"),
        ("lr", lambda X: with_value(X, "gender", None), "non-string value in categorical column gender"),
        ("xgb", lambda X: with_value(X, "gender", pd.NA), "non-string value in categorical column gender"),
        ("lr", lambda X: with_value(X, "gender", 5), "non-string value in categorical column gender"),
    ],
)
def test_input_contract_refusals(which, make, message, request):
    _, X, portable = request.getfixturevalue(which)
    with pytest.raises(ValueError, match=rf"^portable: {message}"):
        portable.predict_proba(make(X))


def test_xgb_nan_numeric_is_missing_and_matches_pickle(xgb):
    pickled, X, portable = xgb
    holed = X.copy()
    holed.iloc[::3, holed.columns.get_loc("age_at_index_years")] = np.nan
    assert_same(portable.predict_proba(holed), pickled.predict_proba(holed))


def test_booster_best_iteration_attribute_is_ignored(xgb, portable_env):
    _, X, _ = xgb
    fresh = load(portable_env.xgb_portable_uri)
    before = fresh.predict_proba(X)
    fresh.folds[0].booster.set_attr(best_iteration="5")
    assert fresh.folds[0].iteration_range == (0, 0)
    assert_same(fresh.predict_proba(X), before)


def test_calibrator_scalars_are_float64(xgb):
    for fold in xgb[2].folds:
        assert type(fold.a) is np.float64 and type(fold.b) is np.float64


# --------------------------------------------------------------------------------------------- load_portable
def _first_spec(doc: dict, want_infrequent: bool) -> dict:
    specs = doc["preprocess"]["one_hot"] if "preprocess" in doc else doc["folds"][0]["preprocess"]["one_hot"]
    return next(s for s in specs if s["infrequent_column"] is want_infrequent and s["categories"])


def _tiny_booster_bytes() -> bytes:
    rng = np.random.default_rng(0)
    model = xgboost.XGBClassifier(n_estimators=2, max_depth=1).fit(rng.normal(size=(20, 3)), np.tile([0, 1], 10))
    return bytes(model.get_booster().save_raw("ubj"))


def _doc_edit(edit):
    def apply(doc, files):
        edit(doc)
        return doc, files

    return apply


def _files_edit(edit):
    def apply(doc, files):
        edit(files)
        return doc, files

    return apply


def _overlap(doc):
    spec = _first_spec(doc, want_infrequent=False)
    spec["infrequent_categories"], spec["infrequent_column"] = [spec["categories"][0]], True


LOAD_CASES = [
    ("lr", "missing-key", _doc_edit(lambda d: d.pop("linear")), "exactly the keys"),
    ("lr", "extra-key", _doc_edit(lambda d: d.update(extra=1)), "exactly the keys"),
    ("lr", "coef-length", _doc_edit(lambda d: d["linear"]["coef"].pop()), "linear.coef must be a list of"),
    ("lr", "output-names", _doc_edit(lambda d: d["output_feature_names"].__setitem__(0, "x")), "output_feature_names differ"),
    ("lr", "format-version-2", _doc_edit(lambda d: d.update(format_version=2)), "format_version must be 1"),
    ("lr", "model-name-unknown", _doc_edit(lambda d: d.update(model_name="random_forest")), "model_name must be one of"),
    ("lr", "float-as-string", _doc_edit(lambda d: d["linear"].update(intercept="NaN")), "linear.intercept must be a finite number"),
    ("lr", "infrequent-flag-without-categories", _doc_edit(lambda d: _first_spec(d, False).update(infrequent_column=True)), "infrequent_column must be true exactly"),
    ("lr", "overlapping-categories", _doc_edit(_overlap), "overlap"),
    ("xgb", "booster-absent", _files_edit(lambda f: f.pop(booster_filename(0))), "booster file portable_fold_0.ubj is missing"),
    ("xgb", "booster-corrupt", _files_edit(lambda f: f.update({booster_filename(0): b"junk"})), "is not a loadable XGBoost model"),
    ("xgb", "booster-feature-count", _files_edit(lambda f: f.update({booster_filename(0): _tiny_booster_bytes()})), "unnamed features"),
    ("xgb", "iteration-range", _doc_edit(lambda d: d["folds"][0].update(iteration_range=[0, 10])), r"iteration_range must be \[0, 0\]"),
    ("xgb", "booster-file-misnamed", _doc_edit(lambda d: d["folds"][1].update(booster_file=booster_filename(0))), "booster_file must be"),
]  # fmt: skip


@pytest.mark.parametrize(("which", "case_id", "edit", "message"), LOAD_CASES, ids=[c[1] for c in LOAD_CASES])
def test_load_portable_refuses(which, case_id, edit, message, portable_env):
    uri = portable_env.lr_portable_uri if which == "lr" else portable_env.xgb_portable_uri
    files = portable_files(uri)
    doc, files = edit(json.loads(files[PORTABLE_PARAMS_FILENAME]), files)
    files[PORTABLE_PARAMS_FILENAME] = json.dumps(doc).encode()
    with pytest.raises(ValueError, match=rf"^portable: .*{message}"):
        load_portable(files)


# --------------------------------------------------------------------------------------------- the reference
def _reference_csv(rows: list[list[str]], header=None) -> bytes:
    header = header or predictor.reference_header(FEATURE_COLUMNS)
    return ("\n".join(",".join(r) for r in [header, *rows]) + "\n").encode()


def _row(kind: str, row_id: str, number: str = "1.0", p1: str = "0.25") -> list[str]:
    cells = ["MISSING" if c in CATEGORICAL_FEATURES else number for c in FEATURE_COLUMNS]
    return [kind, row_id, *cells, repr(1 - float(p1)), p1]


def test_parse_reference_round_trip_and_refusals():
    tiny, tenth = repr(5e-324), repr(0.1)
    reference = parse_reference(
        _reference_csv([_row("test", "a", tiny), _row("test", "b", tenth), _row("probe", "probe-0")]),
        FEATURE_COLUMNS,
        CATEGORICAL_FEATURES,
    )
    assert reference.features["prior_encounter_count"].tolist() == [5e-324, 0.1, 1.0]
    assert reference.segment("probe").encounter_ids.tolist() == ["probe-0"]
    assert reference.columns == tuple(FEATURE_COLUMNS) and len(reference) == 3

    bad = {
        "header": _reference_csv([_row("test", "a")], header=["kind", "encounter_id"]),
        "no-test-row": _reference_csv([_row("probe", "probe-0")]),
        "probe-before-test": _reference_csv([_row("probe", "probe-0"), _row("test", "a")]),
        "not-ascending": _reference_csv([_row("test", "b"), _row("test", "a")]),
        "probe-ids": _reference_csv([_row("test", "a"), _row("probe", "probe-1")]),
        "non-finite": _reference_csv([_row("test", "a", "inf")]),
        "p1-out-of-range": _reference_csv([_row("test", "a", p1="1.5")]),
    }
    for name, data in bad.items():
        with pytest.raises(ValueError, match=r"^portable: reference"):
            parse_reference(data, FEATURE_COLUMNS, CATEGORICAL_FEATURES)
        assert name  # for the failure message


def test_verify_reference_detects_a_one_ulp_change(portable_env):
    model, reference = read_portable_bundle(portable_env.xgb_portable_uri)
    assert verify_reference(model, reference) == 0.0
    proba = reference.proba.copy()
    proba[0, 1] = np.nextafter(proba[0, 1], 1.0)
    moved = Reference(kinds=reference.kinds, encounter_ids=reference.encounter_ids, features=reference.features, proba=proba)
    with pytest.raises(ValueError, match=r"^portable: reference mismatch: max abs diff .* this environment does not reproduce"):
        verify_reference(model, moved)


# --------------------------------------------------------------------------------------------- read_portable_bundle
def _manifest(uri: str) -> dict:
    return json.loads((Path(uri) / "manifest.json").read_text())


def _stray_booster(env, tmp: Path) -> str:
    data = (Path(env.xgb_portable_uri) / booster_filename(0)).read_bytes()
    table = {**_manifest(env.lr_portable_uri)["files"], booster_filename(0): file_entry(booster_filename(0), data)}
    return tamper_bundle(env.lr_portable_uri, tmp, set={"portable.booster_files": [booster_filename(0)], "files": table},
                         files={booster_filename(0): data})  # fmt: skip


def _relabelled_split(env, tmp: Path) -> str:
    text = (Path(env.lr_portable_uri) / "split_assignment.csv").read_text()
    return tamper_bundle(env.lr_portable_uri, tmp, files={"split_assignment.csv": text.replace(",test", ",train", 1).encode()})


def _non_canonical(env, tmp: Path) -> str:
    raw = json.dumps(_manifest(env.lr_portable_uri), indent=1).encode()
    return tamper_bundle(env.lr_portable_uri, tmp, manifest_raw=raw, rehash=False)


READ_CASES = [
    ("portable-json-edited", lambda e, t: tamper_bundle(e.lr_portable_uri, t, files={PORTABLE_PARAMS_FILENAME: b"{}\n"}, rehash=False), "does not match its sha256"),
    ("lr-with-stray-booster", _stray_booster, "do not fit model logistic_regression"),
    ("model-name-mismatch", lambda e, t: tamper_bundle(e.lr_portable_uri, t, set={"model_name": "xgboost_calibrated"}), "model_name"),
    ("feature-columns-mismatch", lambda e, t: tamper_bundle(e.lr_portable_uri, t, set={"feature_columns": list(reversed(FEATURE_COLUMNS))}), "feature_columns differ"),
    ("wrong-directory", lambda e, t: tamper_bundle(e.lr_portable_uri, t, dir_name="f" * 64), "directory named after its id"),
    ("non-canonical", _non_canonical, "not canonical"),
    ("schema-1", lambda e, t: e.schema1_uri, "schema_version 1"),
    ("parity-count", lambda e, t: tamper_bundle(e.lr_portable_uri, t, set={"portable.parity.probe_rows": 1}), "parity block"),
    ("split-relabelled", _relabelled_split, "test partition"),
]  # fmt: skip


@pytest.mark.parametrize(("case_id", "build", "message"), READ_CASES, ids=[c[0] for c in READ_CASES])
def test_read_portable_bundle_checks(case_id, build, message, scoring_env, portable_env, tmp_path):
    env = copy.copy(portable_env)
    env.schema1_uri = scoring_env.lr_bundle_uri
    with pytest.raises(ValueError, match=rf"^portable: .*{message}"):
        read_portable_bundle(build(env, tmp_path / "bundles"))


def test_read_portable_bundle_does_not_read_the_pickle(portable_env, tmp_path):
    uri = tamper_bundle(portable_env.xgb_portable_uri, tmp_path / "bundles", files={"model.pkl": b"not a pickle"})
    model, reference = read_portable_bundle(uri)
    assert verify_reference(model, reference) == 0.0


# --------------------------------------------------------------------------------------------- the import rule
def test_predictor_source_has_no_forbidden_imports():
    source = PREDICTOR_SOURCE.read_text()
    forbidden = r"(sklearn|pandas|cloudpickle|pickle|joblib|mlflow|pyspark|google)"
    assert not re.search(rf"^\s*(import|from)\s+{forbidden}\b", source, re.MULTILINE)
    assert "pickle.loads" not in source


def test_predictor_import_does_not_load_mlflow_pyspark_google_or_our_sklearn_modules():
    code = (
        "import sys\n"
        "import readmission_risk.portable.predictor\n"
        "bad = [m for m in ('mlflow', 'pyspark', 'google.cloud', 'readmission_risk.portable.export', "
        "'readmission_risk.scoring', 'readmission_risk.models') if m in sys.modules]\n"
        "assert not bad, bad\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
