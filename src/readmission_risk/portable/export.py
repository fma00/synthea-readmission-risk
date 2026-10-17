"""The one-time portable export of a frozen model bundle (FR0.3, v2 slice 6c): a validated schema-1 bundle in, a new
content-addressed schema-2 bundle out, holding the same model.pkl and split_assignment.csv byte for byte plus the
portable form (portable.json, portable_fold_<k>.ubj) and the parity reference (portable_reference.csv). Nothing is
written unless the portable predictor, loaded from the exact bytes to be written, reproduces the pickle's predict_proba
with a maximum absolute difference of exactly 0 on every test-partition row and every probe row. See
notes/eg-new-feature/v2-slice6c-portable-v1-export-2026-10-17.md (Interfaces > portable/export.py).

Every failure is a ValueError starting "portable export: ", except the gold loader's and validate_bundle's, which keep
their own messages.
"""

from __future__ import annotations

import csv
import io
import json
import platform
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost
from sklearn.calibration import CalibratedClassifierCV
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, OneHotEncoder, StandardScaler

from readmission_risk.bundles.manifest import (
    MODEL_FILENAME,
    PORTABLE_FORMAT_VERSION,
    PORTABLE_MANIFEST_SCHEMA_VERSION,
    PORTABLE_PARAMS_FILENAME,
    PORTABLE_REFERENCE_FILENAME,
    SPLIT_FILENAME,
    booster_filename,
    canonical_json,
    compute_bundle_id,
    file_entry,
    manifest_bytes,
    sha256_hex,
)
from readmission_risk.bundles.store import read_bundle_file, write_bundle
from readmission_risk.models import data as gold_data
from readmission_risk.models.data import (
    CATEGORICAL_FEATURES,
    COUNT_FEATURES,
    FEATURE_COLUMNS,
    NUMERIC_OTHER_FEATURES,
)
from readmission_risk.scoring.bundle_guard import load_bundle_model, validate_bundle
from readmission_risk.scoring.constants import PARTITION_TEST

from . import predictor

PROBE_BASE_ROWS = 5
PROBE_UNSEEN = "__portable_probe_unseen__"
MISSING_CATEGORY = "MISSING"  # what prepare_features fills null categoricals with
DESIGN_NOTE = "notes/eg-new-feature/v2-slice6c-portable-v1-export-2026-10-17.md"


@dataclass(frozen=True, eq=False)
class PortableExportResult:
    bundle_id: str
    bundle_uri: str
    manifest_sha256: str
    created: bool  # local: False when an identical bundle existed; gs://: always True (the writer cannot read)
    source_bundle_id: str
    model_name: str
    parity_rows: int
    probe_rows: int
    max_abs_diff: float  # always 0.0 on success


def _unsupported(what: str) -> ValueError:
    return ValueError(f"portable export: unsupported model shape: {what}")


# --------------------------------------------------------------------------------------------- extract_portable
def _output_config_is_default(obj) -> bool:
    # a private attribute set by set_output; accepted for a one-time export under the pinned scikit-learn (design note)
    return getattr(obj, "_sklearn_output_config", {}).get("transform", "default") == "default"


def _is_01(classes) -> bool:
    return np.array_equal(np.asarray(classes), [0, 1])


def _check_column_transformer(ct, pipeline, names: list[str]) -> list[tuple]:
    """The fitted transformers_ (remainder dropped), after the shared checks; `names` = the expected transformer names."""
    if not isinstance(ct, ColumnTransformer):
        raise _unsupported(f"preprocess is {type(ct).__name__}, not a ColumnTransformer")
    if ct.remainder != "drop" or ct.sparse_output_ is not False:
        raise _unsupported("the ColumnTransformer must drop the remainder and produce dense output")
    if not (_output_config_is_default(ct) and _output_config_is_default(pipeline)):
        raise _unsupported("the transform output is configured (set_output); only the default numpy output is mirrored")
    fitted = list(ct.transformers_)
    if fitted and fitted[-1][0] == "remainder":
        if fitted[-1][1] != "drop":
            raise _unsupported("the remainder is not dropped")
        fitted = fitted[:-1]
    if [entry[0] for entry in fitted] != names:
        raise _unsupported(f"the ColumnTransformer's transformers are {[entry[0] for entry in fitted]}, expected {names}")
    return fitted


def _check_columns(cols, expected, what: str) -> list[str]:
    cols = list(cols)
    if cols != list(expected):
        raise _unsupported(f"{what} columns are {cols}, expected {list(expected)}")
    return cols


def _check_scaler(scaler, what: str) -> None:
    if not isinstance(scaler, StandardScaler) or not (scaler.with_mean and scaler.with_std):
        raise _unsupported(f"{what} is not a StandardScaler with with_mean and with_std")
    if getattr(scaler, "mean_", None) is None or getattr(scaler, "scale_", None) is None:
        raise _unsupported(f"{what} has no fitted mean_/scale_")


def _one_hot_specs(encoder, cols: list[str]) -> list[dict]:
    if not isinstance(encoder, OneHotEncoder):
        raise _unsupported(f"the categorical transformer is {type(encoder).__name__}, not a OneHotEncoder")
    if (
        encoder.handle_unknown != "infrequent_if_exist"
        or encoder.drop is not None
        or encoder.sparse_output is not False
        or encoder.dtype is not np.float64
    ):
        raise _unsupported("the OneHotEncoder must have handle_unknown='infrequent_if_exist', drop=None, dense float64 output")
    specs = []
    for i, column in enumerate(cols):
        categories = encoder.categories_[i]
        if categories.dtype != object or not all(type(c) is str for c in categories):
            raise _unsupported(f"the categories of {column} are not all strings")
        infrequent = encoder.infrequent_categories_[i]
        infrequent_list = [] if infrequent is None else [str(c) for c in infrequent]
        specs.append(
            {
                "column": column,
                "categories": [c for c in categories if c not in set(infrequent_list)],
                "infrequent_categories": infrequent_list,
                "infrequent_column": infrequent is not None,
            }
        )
    return specs


def _derived_names(numeric: list[str], specs: list[dict]) -> list[str]:
    names = list(numeric)
    for spec in specs:
        names += [f"{spec['column']}_{c}" for c in spec["categories"]]
        if spec["infrequent_column"]:
            names.append(f"{spec['column']}_{predictor.INFREQUENT_SUFFIX}")
    return names


def _check_names(ct, derived: list[str]) -> list[str]:
    if [str(n) for n in ct.get_feature_names_out()] != derived:
        raise _unsupported("output feature names differ")
    return derived


def _floats(values) -> list[float]:
    return [float(v) for v in np.asarray(values, dtype=np.float64).ravel()]


def _extract_linear(model: Pipeline) -> dict:
    if [name for name, _ in model.steps] != ["preprocess", "model"]:
        raise _unsupported(f"the pipeline steps are {[name for name, _ in model.steps]}, expected ['preprocess', 'model']")
    ct, lr = model.named_steps["preprocess"], model.named_steps["model"]
    if not isinstance(lr, LogisticRegression) or not _is_01(lr.classes_):
        raise _unsupported("the model step is not a binary LogisticRegression with classes [0, 1]")
    if lr.coef_.ndim != 2 or lr.coef_.shape[0] != 1 or lr.intercept_.shape != (1,):
        raise _unsupported(f"LogisticRegression coef_/intercept_ have shapes {lr.coef_.shape}/{lr.intercept_.shape}")
    (_, counts, count_cols), (_, age, age_cols), (_, encoder, cat_cols) = _check_column_transformer(
        ct, model, ["counts", "age", "cats"]
    )
    count_cols = _check_columns(count_cols, COUNT_FEATURES, "counts")
    age_cols = _check_columns(age_cols, NUMERIC_OTHER_FEATURES, "age")
    cat_cols = _check_columns(cat_cols, CATEGORICAL_FEATURES, "cats")
    if not isinstance(counts, Pipeline) or [name for name, _ in counts.steps] != ["log1p", "scale"]:
        raise _unsupported("counts is not Pipeline[log1p, scale]")
    log1p, scale = counts.named_steps["log1p"], counts.named_steps["scale"]
    if not isinstance(log1p, FunctionTransformer) or log1p.func is not np.log1p or log1p.kw_args is not None or log1p.validate:
        raise _unsupported("counts' log1p step is not FunctionTransformer(np.log1p) without kw_args or validation")
    _check_scaler(scale, "counts' scale step")
    _check_scaler(age, "age")
    specs = _one_hot_specs(encoder, cat_cols)
    names = _check_names(ct, _derived_names([*count_cols, *age_cols], specs))
    if lr.coef_.shape[1] != len(names):
        raise _unsupported(f"coef_ has {lr.coef_.shape[1]} columns for {len(names)} output features")
    return {
        "format_version": PORTABLE_FORMAT_VERSION,
        "model_name": predictor.LOGISTIC_REGRESSION,
        "feature_columns": list(FEATURE_COLUMNS),
        "preprocess": {
            "kind": "linear",
            "log1p_standardized": {"columns": count_cols, "mean": _floats(scale.mean_), "scale": _floats(scale.scale_)},
            "standardized": {"columns": age_cols, "mean": _floats(age.mean_), "scale": _floats(age.scale_)},
            "one_hot": specs,
        },
        "output_feature_names": names,
        "linear": {"coef": _floats(lr.coef_), "intercept": float(lr.intercept_[0])},
    }


def _check_passthrough(entry, ct) -> None:
    unfitted = [(name, t) for name, t, _ in ct.transformers]
    if [name for name, _ in unfitted] != ["num", "cats"] or unfitted[0][1] != "passthrough":
        raise _unsupported(f"the unfitted transformers are {unfitted}, expected [('num', 'passthrough'), ('cats', <encoder>)]")
    # scikit-learn 1.9.1 substitutes this FunctionTransformer for "passthrough" when fitting (measured on the v1 model)
    if not (
        isinstance(entry, FunctionTransformer)
        and entry.func is None
        and entry.inverse_func is None
        and entry.kw_args is None
        and not entry.validate
        and entry.feature_names_out == "one-to-one"
    ):
        raise _unsupported("the fitted 'num' transformer is not scikit-learn's identity passthrough")


def _extract_calibrated(model: CalibratedClassifierCV) -> tuple[dict, dict[str, bytes]]:
    if model.method != "sigmoid" or model.ensemble is not True or not _is_01(model.classes_):
        raise _unsupported("not a CalibratedClassifierCV(method='sigmoid', ensemble=True) with classes [0, 1]")
    folds, boosters = [], {}
    for k, calibrated in enumerate(model.calibrated_classifiers_):
        if calibrated.method != "sigmoid" or not _is_01(calibrated.classes) or len(calibrated.calibrators) != 1:
            raise _unsupported(f"fold {k} is not a binary sigmoid-calibrated classifier with one calibrator")
        calibrator = calibrated.calibrators[0]
        if type(calibrator).__name__ != "_SigmoidCalibration":
            raise _unsupported(f"fold {k}'s calibrator is {type(calibrator).__name__}")
        a, b = float(calibrator.a_), float(calibrator.b_)
        if not (np.isfinite(a) and np.isfinite(b)):
            raise _unsupported(f"fold {k}'s calibrator a_/b_ are not finite")
        pipe = calibrated.estimator
        if not isinstance(pipe, Pipeline) or [name for name, _ in pipe.steps] != ["preprocess", "model"]:
            raise _unsupported(f"fold {k}'s estimator is not Pipeline[preprocess, model]")
        if hasattr(pipe, "decision_function"):
            raise _unsupported(f"fold {k}'s estimator has decision_function (the mirror assumes predict_proba is calibrated)")
        ct, clf = pipe.named_steps["preprocess"], pipe.named_steps["model"]
        (_, num, num_cols), (_, encoder, cat_cols) = _check_column_transformer(ct, pipe, ["num", "cats"])
        _check_passthrough(num, ct)
        num_cols = _check_columns(num_cols, [*COUNT_FEATURES, *NUMERIC_OTHER_FEATURES], "num")
        cat_cols = _check_columns(cat_cols, CATEGORICAL_FEATURES, "cats")
        if not isinstance(clf, xgboost.XGBClassifier):
            raise _unsupported(f"fold {k}'s model is {type(clf).__name__}, not an XGBClassifier")
        if clf.objective != "binary:logistic" or clf.booster not in (None, "gbtree") or not _is_01(clf.classes_):
            raise _unsupported(f"fold {k}'s XGBClassifier is not a binary:logistic gbtree with classes [0, 1]")
        if not (isinstance(clf.missing, float) and np.isnan(clf.missing)):
            raise _unsupported(f"fold {k}'s XGBClassifier has missing={clf.missing!r}, not NaN")
        # private, but it is what XGBClassifier.predict_proba itself uses; (0, best_iteration + 1) for an early-stopped model
        iteration_range = clf._get_iteration_range(None)
        if tuple(iteration_range) != (0, 0):
            raise _unsupported(f"fold {k}'s booster predicts with iteration_range {iteration_range} (early stopping?)")
        booster = clf.get_booster()
        if booster.feature_names is not None:
            raise _unsupported(f"fold {k}'s booster carries feature names")
        specs = _one_hot_specs(encoder, cat_cols)
        names = _check_names(ct, _derived_names(num_cols, specs))
        boosters[booster_filename(k)] = bytes(booster.save_raw("ubj"))
        folds.append(
            {
                "preprocess": {"kind": "tree", "passthrough": {"columns": num_cols}, "one_hot": specs},
                "output_feature_names": names,
                "booster_file": booster_filename(k),
                "iteration_range": [0, 0],
                "calibrator": {"a": a, "b": b},
            }
        )
    if not folds:
        raise _unsupported("no calibrated classifiers")
    document = {
        "format_version": PORTABLE_FORMAT_VERSION,
        "model_name": predictor.XGBOOST_CALIBRATED,
        "feature_columns": list(FEATURE_COLUMNS),
        "folds": folds,
    }
    return document, boosters


def extract_portable(model) -> tuple[dict, dict[str, bytes]]:
    """(portable.json document, {booster file name: UBJ bytes}) of an unpickled v1 model; refuses every shape it does not
    mirror exactly."""
    if isinstance(model, CalibratedClassifierCV):
        return _extract_calibrated(model)
    if isinstance(model, Pipeline):
        return _extract_linear(model), {}
    raise _unsupported(f"{type(model).__name__} is neither a Pipeline nor a CalibratedClassifierCV")


# --------------------------------------------------------------------------------------------- probes and the reference
def _document_specs(document: dict) -> list[dict]:
    if document["model_name"] == predictor.LOGISTIC_REGRESSION:
        return list(document["preprocess"]["one_hot"])
    return [spec for fold in document["folds"] for spec in fold["preprocess"]["one_hot"]]


def build_probes(X_test: pd.DataFrame, document: dict) -> pd.DataFrame:
    """Deterministic probe rows from the document being exported: for each categorical column, PROBE_UNSEEN and every
    fitted category of that column (any spec, any fold) plus "MISSING", each written into copies of the first
    PROBE_BASE_ROWS test rows."""
    base = X_test.iloc[:PROBE_BASE_ROWS]
    specs = _document_specs(document)
    pieces = []
    for column in CATEGORICAL_FEATURES:
        fitted = set()
        for spec in specs:
            if spec["column"] == column:
                fitted |= {*spec["categories"], *spec["infrequent_categories"]}
        if PROBE_UNSEEN in fitted:
            raise ValueError(f"portable export: probe value {PROBE_UNSEEN!r} is a fitted category of {column}")
        for value in [PROBE_UNSEEN, *sorted(fitted | {MISSING_CATEGORY})]:
            piece = base.copy()
            piece[column] = pd.Series([value] * len(piece), index=piece.index, dtype=object)
            pieces.append(piece)
    return pd.concat(pieces, ignore_index=True)[list(FEATURE_COLUMNS)]


def _cell(value, categorical: bool) -> str:
    if categorical:
        if "\r" in value or "\n" in value:
            raise ValueError(f"portable export: categorical value {value!r} contains a line break")
        return value
    return repr(float(value))


def reference_bytes(X_test: pd.DataFrame, probes: pd.DataFrame, ref_test: np.ndarray, ref_probe: np.ndarray) -> bytes:
    """portable_reference.csv: the test segment (index = encounter_id, ascending), then the probe segment."""
    categorical = set(CATEGORICAL_FEATURES)
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(predictor.reference_header(FEATURE_COLUMNS))
    segments = (("test", X_test, [str(i) for i in X_test.index], ref_test),
                ("probe", probes, [f"probe-{i}" for i in range(len(probes))], ref_probe))  # fmt: skip
    for kind, frame, ids, proba in segments:
        columns = {c: frame[c].to_numpy() for c in FEATURE_COLUMNS}
        for i, row_id in enumerate(ids):
            if "\r" in row_id or "\n" in row_id:
                raise ValueError(f"portable export: encounter_id {row_id!r} contains a line break")
            cells = [_cell(columns[c][i], c in categorical) for c in FEATURE_COLUMNS]
            writer.writerow([kind, row_id, *cells, repr(float(proba[i, 0])), repr(float(proba[i, 1]))])
    return buffer.getvalue().encode("utf-8")


def _pickle_proba(model, frame: pd.DataFrame, what: str) -> np.ndarray:
    proba = np.asarray(model.predict_proba(frame), dtype=np.float64)
    if proba.shape != (len(frame), 2) or not np.isfinite(proba).all():
        raise ValueError(f"portable export: the pickle's predict_proba on the {what} has shape {proba.shape} or non-finite values")
    return proba


def _check_lossless(reference: predictor.Reference, frames: list[pd.DataFrame], proba: np.ndarray) -> None:
    if not np.array_equal(reference.proba, proba):
        raise ValueError("portable export: the reference CSV does not round-trip the pickle's outputs exactly")
    for column in FEATURE_COLUMNS:
        expected = np.concatenate([f[column].to_numpy() for f in frames])
        got = reference.features[column]
        same = list(got) == list(expected) if column in CATEGORICAL_FEATURES else np.array_equal(got, expected.astype(np.float64))
        if not same:
            raise ValueError(f"portable export: the reference CSV does not round-trip column {column} exactly")


def writer_versions() -> dict[str, str]:
    try:
        blas = str(np.show_config(mode="dicts")["Build Dependencies"]["blas"]["name"])
    except (KeyError, TypeError):
        blas = "unknown"
    return {
        "numpy": version("numpy"),
        "scipy": version("scipy"),
        "xgboost": version("xgboost"),
        "python": platform.python_version(),
        "platform": f"{platform.system()}-{platform.machine()}",
        "blas": blas,
    }


# --------------------------------------------------------------------------------------------- the export
def export_portable_bundle(source_bundle_uri: str, gold_dir: Path, bundles_root: Path | str) -> PortableExportResult:
    # 1
    if source_bundle_uri.startswith("gs://"):
        raise ValueError(
            "portable export: the source bundle must be a local path (download it first: docs/scoring.md > Model bundles)"
        )
    # 2, 3
    gold, metadata = gold_data.load_gold_table_with_metadata(gold_dir)
    bundle = validate_bundle(source_bundle_uri, gold=gold, metadata=metadata)
    who = f"bundle {bundle.bundle_id[:12]}"
    # 4
    raw = read_bundle_file(source_bundle_uri, "manifest.json")
    source_manifest = json.loads(raw.decode("utf-8"))
    if source_manifest.get("schema_version") != 1:
        raise ValueError(f"portable export: {who} is already schema {source_manifest.get('schema_version')!r}")
    if compute_bundle_id(source_manifest) != bundle.bundle_id or raw != manifest_bytes(source_manifest):
        raise ValueError(f"portable export: the manifest of {who} changed after validation")
    split_bytes = read_bundle_file(source_bundle_uri, SPLIT_FILENAME)
    files_table = source_manifest["files"]
    if file_entry(SPLIT_FILENAME, split_bytes) != files_table[SPLIT_FILENAME] or sha256_hex(bundle.model_bytes) != files_table[MODEL_FILENAME]["sha256"]:
        raise ValueError(f"portable export: the files of {who} changed after validation")
    # 5
    model = load_bundle_model(bundle)
    # 6
    test_ids = sorted(bundle.partition_of.index[bundle.partition_of == PARTITION_TEST])
    X_test = gold_data.prepare_features(gold.set_index("encounter_id", drop=False).loc[test_ids])
    # 7: the two pickle calls are exactly the two reference segments (batch-layout contract)
    document, boosters = extract_portable(model)
    probes = build_probes(X_test, document)
    ref_test = _pickle_proba(model, X_test, "test rows")
    ref_probe = _pickle_proba(model, probes, "probe rows")
    reference_data = reference_bytes(X_test, probes, ref_test, ref_probe)
    try:
        portable_bytes = canonical_json(document) + b"\n"
    except ValueError as exc:
        raise ValueError(f"portable export: {exc}") from exc
    # 8: parity on the bytes that will be written
    try:
        portable_model = predictor.load_portable({PORTABLE_PARAMS_FILENAME: portable_bytes, **boosters})
        reference = predictor.parse_reference(reference_data, FEATURE_COLUMNS, CATEGORICAL_FEATURES)
        _check_lossless(reference, [X_test, probes], np.vstack([ref_test, ref_probe]))
        predictor.verify_reference(portable_model, reference)
    except ValueError as exc:
        raise ValueError(f"portable export: parity failed for {who}: {exc}; a requirement change, see {DESIGN_NOTE}") from exc
    # 9
    files = {
        MODEL_FILENAME: bundle.model_bytes,
        SPLIT_FILENAME: split_bytes,
        PORTABLE_PARAMS_FILENAME: portable_bytes,
        PORTABLE_REFERENCE_FILENAME: reference_data,
        **boosters,
    }
    manifest = {k: v for k, v in source_manifest.items() if k != "bundle_id"}
    manifest["schema_version"] = PORTABLE_MANIFEST_SCHEMA_VERSION
    manifest["files"] = {name: file_entry(name, data) for name, data in files.items()}
    manifest["portable"] = {
        "format_version": PORTABLE_FORMAT_VERSION,
        "params_file": PORTABLE_PARAMS_FILENAME,
        "reference_file": PORTABLE_REFERENCE_FILENAME,
        "booster_files": [booster_filename(k) for k in range(len(boosters))],
        "parity": {"rows": len(X_test), "probe_rows": len(probes), "max_abs_diff": 0.0},
        "writer_versions": writer_versions(),
    }
    manifest["bundle_id"] = compute_bundle_id(manifest)
    uri, created = write_bundle(files, manifest, bundles_root)
    # 10
    return PortableExportResult(
        bundle_id=manifest["bundle_id"],
        bundle_uri=uri,
        manifest_sha256=sha256_hex(manifest_bytes(manifest)),
        created=created,
        source_bundle_id=bundle.bundle_id,
        model_name=portable_model.model_name,
        parity_rows=len(X_test),
        probe_rows=len(probes),
        max_abs_diff=0.0,
    )
