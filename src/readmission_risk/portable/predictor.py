"""Loading and running the portable form of the frozen v1 models (FR0.3, v2 slice 6c): parameters as JSON
(portable.json), XGBoost boosters in XGBoost's own UBJ format, and a self-contained parity reference
(portable_reference.csv: the parity inputs and the pickle's outputs on them). Every prediction mirrors scikit-learn
1.9.1's operation order exactly, as measured (max abs diff 0.0 on the v1 test rows); see
notes/eg-new-feature/v2-slice6c-portable-v1-export-2026-10-17.md (Interfaces > portable/predictor.py).

This module's own code uses numpy, scipy.special.expit and xgboost.Booster only: never a scikit-learn object, pandas or
pickle (a static test checks the imports). `import xgboost` itself imports scikit-learn, pandas and cloudpickle as a
soft dependency; nothing here depends on their object layout. It must not import readmission_risk.models or
readmission_risk.scoring (a subprocess test), so the model names are restated below.

Uniform error rule: every defect in the files, the bundle or the input is a ValueError starting "portable: ".
"""

from __future__ import annotations

import csv
import io
import itertools
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
import xgboost
from scipy.special import expit

from readmission_risk.bundles.manifest import (
    HEX64_RE,
    MANIFEST_FILENAME,
    PORTABLE_FORMAT_VERSION,
    PORTABLE_MANIFEST_SCHEMA_VERSION,
    PORTABLE_PARAMS_FILENAME,
    PORTABLE_REFERENCE_FILENAME,
    SPLIT_FILENAME,
    booster_filename,
    check_portable_block,
    check_schema2_files_table,
    compute_bundle_id,
    file_role,
    manifest_bytes,
    sha256_hex,
)
from readmission_risk.bundles.store import (
    bundle_uri_leaf,
    check_bundle_uri,
    list_bundle_entries,
    read_bundle_file,
)

LOGISTIC_REGRESSION = "logistic_regression"  # == models.constants.MODEL_NAMES[0] (a test checks)
XGBOOST_CALIBRATED = "xgboost_calibrated"  # == models.constants.MODEL_NAMES[1]
INFREQUENT_SUFFIX = "infrequent_sklearn"  # scikit-learn's output name for the infrequent column
REFERENCE_KINDS = ("test", "probe")
_SPEC_KEYS = {"column", "categories", "infrequent_categories", "infrequent_column"}


def _fail(message: str) -> ValueError:
    return ValueError(f"portable: {message}")


# --------------------------------------------------------------------------------------------- the model
@dataclass(frozen=True, eq=False)  # eq=False: holds arrays (the generated __eq__/__hash__ would raise)
class OneHotSpec:
    column: str
    categories: np.ndarray  # object array of str: the encoder's frequent categories, in categories_ order
    infrequent_categories: tuple[str, ...]  # recorded for probes and audits; not used to predict
    infrequent_column: bool

    def output_names(self) -> list[str]:
        names = [f"{self.column}_{c}" for c in self.categories]
        return [*names, f"{self.column}_{INFREQUENT_SUFFIX}"] if self.infrequent_column else names

    def transform(self, values: np.ndarray) -> np.ndarray:
        """OneHotEncoder(handle_unknown="infrequent_if_exist", drop=None) semantics: a frequent value -> its column; an
        infrequent or unseen value -> the infrequent column if the encoder has one, else an all-zero block."""
        out = np.zeros((len(values), len(self.categories) + int(self.infrequent_column)))
        for j, category in enumerate(self.categories):
            out[values == category, j] = 1.0
        if self.infrequent_column:
            out[~np.isin(values, self.categories), -1] = 1.0
        return out


@dataclass(frozen=True, eq=False)
class LinearPreprocess:
    log1p_columns: tuple[str, ...]
    log1p_mean: np.ndarray
    log1p_scale: np.ndarray
    scaled_columns: tuple[str, ...]
    scaled_mean: np.ndarray
    scaled_scale: np.ndarray
    one_hot: tuple[OneHotSpec, ...]

    @property
    def numeric_columns(self) -> tuple[str, ...]:
        return (*self.log1p_columns, *self.scaled_columns)

    def output_names(self) -> list[str]:
        return [*self.numeric_columns, *(name for spec in self.one_hot for name in spec.output_names())]

    def transform(self, columns: Mapping[str, np.ndarray]) -> np.ndarray:
        """`columns`: validated column arrays (see _extract). log1p then standardize the counts, standardize age, one-hot
        the categoricals, hstack in that order (the ColumnTransformer's order)."""
        with np.errstate(divide="ignore", invalid="ignore"):  # a count <= -1 is refused right after, not warned about
            a = np.log1p(np.column_stack([columns[c] for c in self.log1p_columns]))
        a = (a - self.log1p_mean) / self.log1p_scale
        b = (np.column_stack([columns[c] for c in self.scaled_columns]) - self.scaled_mean) / self.scaled_scale
        return np.hstack([a, b, *(spec.transform(columns[spec.column]) for spec in self.one_hot)])


@dataclass(frozen=True, eq=False)
class TreePreprocess:
    passthrough_columns: tuple[str, ...]
    one_hot: tuple[OneHotSpec, ...]

    @property
    def numeric_columns(self) -> tuple[str, ...]:
        return self.passthrough_columns

    def output_names(self) -> list[str]:
        return [*self.passthrough_columns, *(name for spec in self.one_hot for name in spec.output_names())]

    def transform(self, columns: Mapping[str, np.ndarray]) -> np.ndarray:
        numeric = np.column_stack([columns[c] for c in self.passthrough_columns])
        return np.hstack([numeric, *(spec.transform(columns[spec.column]) for spec in self.one_hot)])


@dataclass(frozen=True, eq=False)
class PortableFold:
    preprocess: TreePreprocess
    booster: xgboost.Booster
    iteration_range: tuple[int, int]  # always (0, 0): all trees
    a: np.float64  # MUST be np.float64: a Python float times a float32 array stays float32 under NumPy 2 (parity breaks)
    b: np.float64


def _extract(X, feature_columns: tuple[str, ...], categorical: frozenset[str], *, allow_nan: bool) -> dict[str, np.ndarray]:
    """The input contract of predict_proba: validated column arrays (numeric float64, categorical object arrays of str)."""
    columns = getattr(X, "columns", None)
    if columns is None:
        raise _fail("input has no columns attribute")
    if len(X) == 0:
        raise _fail("input has no rows")
    names = list(columns)
    duplicates = sorted({c for c in names if names.count(c) > 1})
    if duplicates:
        raise _fail(f"input has duplicate column(s) {duplicates}")
    missing = [c for c in feature_columns if c not in names]
    if missing:
        raise _fail(f"input is missing column(s) {missing}")
    out = {}
    for col in feature_columns:
        dtype = object if col in categorical else np.float64
        try:
            values = X[col].to_numpy(dtype=dtype)
        except (TypeError, ValueError) as exc:
            raise _fail(f"cannot convert column {col}: {exc}") from exc
        if col in categorical:
            if not all(type(v) is str for v in values):
                raise _fail(f"non-string value in categorical column {col}")
        elif np.isinf(values).any() or (not allow_nan and np.isnan(values).any()):
            raise _fail(f"non-finite numeric input in {col}")
        out[col] = values
    return out


def _categorical(one_hot: tuple[OneHotSpec, ...]) -> frozenset[str]:
    return frozenset(spec.column for spec in one_hot)


def _check_shape(proba: np.ndarray, n: int) -> np.ndarray:
    if proba.shape != (n, 2):
        raise _fail(f"prediction has shape {proba.shape}; expected {(n, 2)}")
    return proba


@dataclass(frozen=True, eq=False)
class PortableLogisticRegression:
    model_name: str
    feature_columns: tuple[str, ...]
    preprocess: LinearPreprocess
    coef: np.ndarray  # float64, C-contiguous, shape (1, n_out)
    intercept: np.ndarray  # float64, shape (1,)

    def predict_proba(self, X) -> np.ndarray:
        """scikit-learn's decision_function + _predict_proba_lr, op for op."""
        columns = _extract(X, self.feature_columns, _categorical(self.preprocess.one_hot), allow_nan=False)
        Z = self.preprocess.transform(columns)
        if not np.isfinite(Z).all():
            raise _fail("non-finite value after the linear transform (a count <= -1?)")
        p = expit((Z @ self.coef.T + self.intercept).reshape(-1))
        return _check_shape(np.stack([1 - p, p], axis=1), len(Z))


@dataclass(frozen=True, eq=False)
class PortableCalibratedXGBoost:
    model_name: str
    feature_columns: tuple[str, ...]
    folds: tuple[PortableFold, ...]

    def predict_proba(self, X) -> np.ndarray:
        """CalibratedClassifierCV(method="sigmoid", ensemble=True).predict_proba, op for op: per fold the booster's
        probability, the sigmoid calibrator, 1 - p, the clip just above 1.0; the mean over folds."""
        categorical = frozenset().union(*(_categorical(f.preprocess.one_hot) for f in self.folds))
        columns = _extract(X, self.feature_columns, categorical, allow_nan=True)
        n = len(columns[self.feature_columns[0]])
        mean = np.zeros((n, 2))
        for fold in self.folds:
            p = fold.booster.inplace_predict(fold.preprocess.transform(columns), iteration_range=fold.iteration_range, missing=np.nan)
            proba = np.zeros((n, 2))
            proba[:, 1] = expit(-(fold.a * p + fold.b))
            proba[:, 0] = 1.0 - proba[:, 1]
            proba[(proba > 1.0) & (proba <= 1.0 + 1e-5)] = 1.0
            mean += proba
        mean /= len(self.folds)
        return _check_shape(mean, n)


PortableModel = PortableLogisticRegression | PortableCalibratedXGBoost


# --------------------------------------------------------------------------------------------- loading portable.json
def _keys(obj: object, keys: set[str], where: str) -> dict:
    if not isinstance(obj, dict) or set(obj) != keys:
        got = sorted(obj) if isinstance(obj, dict) else type(obj).__name__
        raise _fail(f"{where} must be an object with exactly the keys {sorted(keys)}; got {got}")
    return obj


def _str_list(obj: object, where: str, *, nonempty: bool = False) -> list[str]:
    if not isinstance(obj, list) or not all(type(v) is str for v in obj):
        raise _fail(f"{where} must be a list of strings")
    if len(set(obj)) != len(obj):
        raise _fail(f"{where} has duplicates")
    if nonempty and not obj:
        raise _fail(f"{where} must not be empty")
    return obj


def _float(obj: object, where: str) -> float:
    if type(obj) not in (int, float) or not math.isfinite(obj):
        raise _fail(f"{where} must be a finite number; got {obj!r}")
    return float(obj)


def _floats(obj: object, n: int, where: str) -> np.ndarray:
    if not isinstance(obj, list) or len(obj) != n:
        raise _fail(f"{where} must be a list of {n} numbers")
    return np.asarray([_float(v, where) for v in obj], dtype=np.float64)


def _spec(obj: object, where: str) -> OneHotSpec:
    obj = _keys(obj, _SPEC_KEYS, where)
    if type(obj["column"]) is not str:
        raise _fail(f"{where}.column must be a string")
    categories = _str_list(obj["categories"], f"{where}.categories")
    infrequent = _str_list(obj["infrequent_categories"], f"{where}.infrequent_categories")
    if type(obj["infrequent_column"]) is not bool or obj["infrequent_column"] != bool(infrequent):
        raise _fail(f"{where}.infrequent_column must be true exactly when infrequent_categories is non-empty")
    if set(categories) & set(infrequent):
        raise _fail(f"{where}: categories and infrequent_categories overlap")
    return OneHotSpec(
        column=obj["column"],
        categories=np.asarray(categories, dtype=object),
        infrequent_categories=tuple(infrequent),
        infrequent_column=obj["infrequent_column"],
    )


def _specs(obj: object, where: str) -> tuple[OneHotSpec, ...]:
    if not isinstance(obj, list):
        raise _fail(f"{where} must be a list")
    return tuple(_spec(s, f"{where}[{i}]") for i, s in enumerate(obj))


def _check_columns(preprocess, feature_columns: tuple[str, ...], output_names: object, where: str) -> None:
    used = [*preprocess.numeric_columns, *(spec.column for spec in preprocess.one_hot)]
    if sorted(used) != sorted(feature_columns):
        raise _fail(f"{where}: the preprocess must use every feature column exactly once; uses {used}")
    names = _str_list(output_names, f"{where}.output_feature_names")
    if names != preprocess.output_names():
        raise _fail(f"{where}.output_feature_names differ from the names the preprocess implies")


def _linear(doc: dict, feature_columns: tuple[str, ...]) -> PortableLogisticRegression:
    pre = _keys(doc["preprocess"], {"kind", "log1p_standardized", "standardized", "one_hot"}, "preprocess")
    if pre["kind"] != "linear":
        raise _fail(f"preprocess.kind must be 'linear'; got {pre['kind']!r}")
    blocks = {}
    for name in ("log1p_standardized", "standardized"):
        block = _keys(pre[name], {"columns", "mean", "scale"}, f"preprocess.{name}")
        cols = _str_list(block["columns"], f"preprocess.{name}.columns", nonempty=True)
        blocks[name] = (tuple(cols), _floats(block["mean"], len(cols), f"preprocess.{name}.mean"),
                        _floats(block["scale"], len(cols), f"preprocess.{name}.scale"))  # fmt: skip
    preprocess = LinearPreprocess(*blocks["log1p_standardized"], *blocks["standardized"], one_hot=_specs(pre["one_hot"], "preprocess.one_hot"))
    _check_columns(preprocess, feature_columns, doc["output_feature_names"], "model")
    linear = _keys(doc["linear"], {"coef", "intercept"}, "linear")
    n_out = len(doc["output_feature_names"])
    return PortableLogisticRegression(
        model_name=LOGISTIC_REGRESSION,
        feature_columns=feature_columns,
        preprocess=preprocess,
        coef=_floats(linear["coef"], n_out, "linear.coef").reshape(1, -1),
        intercept=np.asarray([_float(linear["intercept"], "linear.intercept")], dtype=np.float64),
    )


def _load_booster(data: bytes, name: str) -> xgboost.Booster:
    booster = xgboost.Booster()
    try:
        booster.load_model(bytearray(data))
    except xgboost.core.XGBoostError as exc:
        raise _fail(f"{name} is not a loadable XGBoost model: {exc}") from exc
    return booster


def _calibrated(doc: dict, feature_columns: tuple[str, ...], files: Mapping[str, bytes]) -> PortableCalibratedXGBoost:
    if not isinstance(doc["folds"], list) or not doc["folds"]:
        raise _fail("folds must be a non-empty list")
    folds = []
    for k, raw in enumerate(doc["folds"]):
        where = f"folds[{k}]"
        fold = _keys(raw, {"preprocess", "output_feature_names", "booster_file", "iteration_range", "calibrator"}, where)
        pre = _keys(fold["preprocess"], {"kind", "passthrough", "one_hot"}, f"{where}.preprocess")
        if pre["kind"] != "tree":
            raise _fail(f"{where}.preprocess.kind must be 'tree'; got {pre['kind']!r}")
        passthrough = _keys(pre["passthrough"], {"columns"}, f"{where}.preprocess.passthrough")
        preprocess = TreePreprocess(
            passthrough_columns=tuple(_str_list(passthrough["columns"], f"{where}.preprocess.passthrough.columns", nonempty=True)),
            one_hot=_specs(pre["one_hot"], f"{where}.preprocess.one_hot"),
        )
        _check_columns(preprocess, feature_columns, fold["output_feature_names"], where)
        if fold["booster_file"] != booster_filename(k):
            raise _fail(f"{where}.booster_file must be {booster_filename(k)!r}; got {fold['booster_file']!r}")
        if fold["iteration_range"] != [0, 0] or not all(type(v) is int for v in fold["iteration_range"]):
            raise _fail(f"{where}.iteration_range must be [0, 0]; got {fold['iteration_range']!r}")
        calibrator = _keys(fold["calibrator"], {"a", "b"}, f"{where}.calibrator")
        if fold["booster_file"] not in files:
            raise _fail(f"{where}: booster file {fold['booster_file']} is missing")
        booster = _load_booster(files[fold["booster_file"]], fold["booster_file"])
        if booster.num_features() != len(fold["output_feature_names"]) or booster.feature_names is not None:
            raise _fail(
                f"{fold['booster_file']} has {booster.num_features()} features (names {booster.feature_names!r}); "
                f"expected {len(fold['output_feature_names'])} unnamed features"
            )
        folds.append(
            PortableFold(
                preprocess=preprocess,
                booster=booster,
                iteration_range=(0, 0),
                a=np.float64(_float(calibrator["a"], f"{where}.calibrator.a")),
                b=np.float64(_float(calibrator["b"], f"{where}.calibrator.b")),
            )
        )
    return PortableCalibratedXGBoost(model_name=XGBOOST_CALIBRATED, feature_columns=feature_columns, folds=tuple(folds))


def load_portable(files: Mapping[str, bytes]) -> PortableModel:
    """The model from portable.json and the booster files it names (other entries are ignored here; read_portable_bundle
    enforces the exact file set). Strict validation; ValueError("portable: ...") on any defect."""
    if PORTABLE_PARAMS_FILENAME not in files:
        raise _fail(f"{PORTABLE_PARAMS_FILENAME} is missing")
    try:
        doc = json.loads(files[PORTABLE_PARAMS_FILENAME].decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise _fail(f"{PORTABLE_PARAMS_FILENAME} is not UTF-8 JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise _fail(f"{PORTABLE_PARAMS_FILENAME} must hold a JSON object")
    model_name = doc.get("model_name")
    model_keys = {
        LOGISTIC_REGRESSION: {"preprocess", "output_feature_names", "linear"},
        XGBOOST_CALIBRATED: {"folds"},
    }
    if model_name not in model_keys:
        raise _fail(f"model_name must be one of {sorted(model_keys)}; got {model_name!r}")
    _keys(doc, {"format_version", "model_name", "feature_columns", *model_keys[model_name]}, PORTABLE_PARAMS_FILENAME)
    if type(doc["format_version"]) is not int or doc["format_version"] != PORTABLE_FORMAT_VERSION:
        raise _fail(f"format_version must be {PORTABLE_FORMAT_VERSION}; got {doc['format_version']!r}")
    feature_columns = tuple(_str_list(doc["feature_columns"], "feature_columns", nonempty=True))
    if model_name == LOGISTIC_REGRESSION:
        return _linear(doc, feature_columns)
    return _calibrated(doc, feature_columns, files)


# --------------------------------------------------------------------------------------------- the reference
@dataclass(frozen=True, eq=False)
class _Column:
    values: np.ndarray

    def to_numpy(self, dtype=None) -> np.ndarray:
        return np.asarray(self.values, dtype=dtype)


@dataclass(frozen=True, eq=False)
class Reference:
    kinds: np.ndarray  # object array, "test" | "probe"
    encounter_ids: np.ndarray  # object array
    features: dict[str, np.ndarray]  # numeric columns float64, categorical columns object arrays of str
    proba: np.ndarray  # (n, 2) float64: the pickle's outputs

    def segment(self, kind: str) -> Reference:
        mask = self.kinds == kind
        return Reference(
            kinds=self.kinds[mask],
            encounter_ids=self.encounter_ids[mask],
            features={c: v[mask] for c, v in self.features.items()},
            proba=self.proba[mask],
        )

    def __len__(self) -> int:
        return len(self.kinds)

    def __getitem__(self, column: str) -> _Column:
        return _Column(self.features[column])

    @property
    def columns(self) -> tuple[str, ...]:
        return tuple(self.features)


def reference_header(feature_columns) -> list[str]:
    return ["kind", "encounter_id", *feature_columns, "p0", "p1"]


def parse_reference(data: bytes, feature_columns, categorical_columns) -> Reference:
    """Strict parse of portable_reference.csv; ValueError("portable: reference ...") on any defect."""
    feature_columns, categorical = tuple(feature_columns), frozenset(categorical_columns)
    try:
        rows = list(csv.reader(io.StringIO(data.decode("utf-8"), newline="")))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise _fail(f"reference is not a UTF-8 CSV: {exc}") from exc
    header = reference_header(feature_columns)
    if not rows or rows[0] != header:
        raise _fail(f"reference header must be {','.join(header)}")
    body = rows[1:]
    if any(len(r) != len(header) for r in body):
        raise _fail(f"reference rows must have {len(header)} fields")
    kinds = [r[0] for r in body]
    n_test = sum(k == "test" for k in kinds)
    if n_test == 0 or kinds != ["test"] * n_test + ["probe"] * (len(kinds) - n_test):
        raise _fail("reference must hold >= 1 test row, then only probe rows")
    ids = [r[1] for r in body]
    test_ids = ids[:n_test]
    if any(a >= b for a, b in itertools.pairwise(test_ids)):
        raise _fail("reference test encounter_ids must be unique and strictly ascending")
    if ids[n_test:] != [f"probe-{i}" for i in range(len(kinds) - n_test)]:
        raise _fail("reference probe ids must be probe-0, probe-1, ... in order")

    def numbers(index: int, name: str) -> np.ndarray:
        try:
            values = np.asarray([float(r[index]) for r in body], dtype=np.float64)
        except ValueError as exc:
            raise _fail(f"reference column {name} holds a non-number: {exc}") from exc
        if not np.isfinite(values).all():
            raise _fail(f"reference column {name} holds a non-finite number")
        return values

    features = {}
    for i, col in enumerate(feature_columns, start=2):
        features[col] = np.asarray([r[i] for r in body], dtype=object) if col in categorical else numbers(i, col)
    proba = np.column_stack([numbers(len(header) - 2, "p0"), numbers(len(header) - 1, "p1")])
    if ((proba < 0) | (proba > 1)).any():
        raise _fail("reference probabilities must lie in [0, 1]")
    return Reference(kinds=np.asarray(kinds, dtype=object), encounter_ids=np.asarray(ids, dtype=object), features=features, proba=proba)


def model_categorical_columns(model: PortableModel) -> frozenset[str]:
    if isinstance(model, PortableLogisticRegression):
        return _categorical(model.preprocess.one_hot)
    return frozenset().union(*(_categorical(f.preprocess.one_hot) for f in model.folds))


def verify_reference(model: PortableModel, reference: Reference) -> float:
    """Re-scores each reference segment (test, probe) in ONE predict_proba call, in file order (the batch layout is part of
    the contract), and requires exact equality with the pickle's stored outputs. Returns 0.0."""
    worst, ok = 0.0, True
    for kind in REFERENCE_KINDS:
        segment = reference.segment(kind)
        if len(segment) == 0:
            continue
        got = model.predict_proba(segment)
        if got.shape != segment.proba.shape:
            ok, worst = False, math.inf
            continue
        if not np.array_equal(got, segment.proba):
            ok, worst = False, max(worst, float(np.max(np.abs(got - segment.proba))))
    if not ok:
        n_test = int((reference.kinds == "test").sum())
        raise _fail(
            f"reference mismatch: max abs diff {worst!r} over {len(reference)} rows ({n_test} test, "
            f"{len(reference) - n_test} probe); this environment does not reproduce the v1 model"
        )
    return 0.0


# --------------------------------------------------------------------------------------------- reading a bundle
def _store(call):
    try:
        return call()
    except (FileNotFoundError, ValueError, OSError) as exc:
        raise _fail(str(exc)) from exc


def _parse_split_test_ids(data: bytes) -> list[str]:
    try:
        rows = list(csv.reader(io.StringIO(data.decode("utf-8"), newline="")))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise _fail(f"{SPLIT_FILENAME} is not a UTF-8 CSV: {exc}") from exc
    if not rows or rows[0] != ["encounter_id", "partition"] or any(len(r) != 2 for r in rows[1:]):
        raise _fail(f"{SPLIT_FILENAME} must have exactly the columns encounter_id,partition")
    ids = [r[0] for r in rows[1:]]
    if len(set(ids)) != len(ids):
        raise _fail(f"{SPLIT_FILENAME} encounter_id values are not unique")
    if {r[1] for r in rows[1:]} - {"train", "test"}:
        raise _fail(f"{SPLIT_FILENAME} partition values must be 'train' or 'test'")
    return sorted(r[0] for r in rows[1:] if r[1] == "test")


def _check_files_table(manifest: dict) -> None:
    """The scorer's I4 entry checks and I5 portable-block check, in the order that keeps every helper's input valid."""
    table = manifest.get("files")
    check_schema2_files_table(table)  # first: every name is known before file_role is asked
    for name, entry in table.items():
        if (
            not isinstance(entry, dict)
            or set(entry) != {"sha256", "size", "role"}
            or type(entry["sha256"]) is not str
            or not HEX64_RE.fullmatch(entry["sha256"])
            or type(entry["size"]) is not int
            or entry["role"] != file_role(name)
        ):
            raise ValueError(f"files entry {name!r} is malformed: {entry!r}")
    check_portable_block(manifest)


def read_portable_bundle(bundle_uri: str) -> tuple[PortableModel, Reference]:
    """The portable model and reference of a schema-2 bundle (local path or gs://), with every integrity check that does
    not need the pickle, the gold table or the training environment. Never reads model.pkl. ValueError("portable: ...")."""
    # 1
    _store(lambda: check_bundle_uri(bundle_uri))
    entries = _store(lambda: list_bundle_entries(bundle_uri))
    if MANIFEST_FILENAME not in entries:
        raise _fail(f"bundle at {bundle_uri} has no {MANIFEST_FILENAME}")
    raw = _store(lambda: read_bundle_file(bundle_uri, MANIFEST_FILENAME))
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise _fail(f"{MANIFEST_FILENAME} of bundle at {bundle_uri} is not UTF-8 JSON: {exc}") from exc
    if not isinstance(manifest, dict):
        raise _fail(f"{MANIFEST_FILENAME} of bundle at {bundle_uri} is not a JSON object")
    schema = manifest.get("schema_version")
    if type(schema) is not int or schema != PORTABLE_MANIFEST_SCHEMA_VERSION:
        raise _fail(f"bundle at {bundle_uri} has schema_version {schema!r}; a portable bundle is schema 2")
    # 2
    bundle_id = manifest.get("bundle_id")
    if type(bundle_id) is not str or not HEX64_RE.fullmatch(bundle_id):
        raise _fail(f"bundle at {bundle_uri} has bundle_id {bundle_id!r}, not 64 lowercase hex characters")
    try:
        expected_id = compute_bundle_id(manifest)
    except ValueError as exc:
        raise _fail(f"{MANIFEST_FILENAME} of bundle at {bundle_uri} cannot be hashed: {exc}") from exc
    # manifest_bytes is evaluated only once the ids agree, so it cannot raise here
    if expected_id != bundle_id or raw != manifest_bytes(manifest):
        raise _fail(f"{MANIFEST_FILENAME} of bundle at {bundle_uri} is not its content hash or not canonical (edited?)")
    if _store(lambda: bundle_uri_leaf(bundle_uri)) != bundle_id:
        raise _fail(f"bundle at {bundle_uri} does not live in a directory named after its id {bundle_id}")
    # 3
    table = manifest.get("files")
    try:
        _check_files_table(manifest)
    except ValueError as exc:
        raise _fail(f"bundle at {bundle_uri}: {exc}") from exc
    if entries != sorted({MANIFEST_FILENAME, *table}):
        raise _fail(f"bundle at {bundle_uri} holds {entries}; its manifest lists {sorted({MANIFEST_FILENAME, *table})}")
    # 4
    block = manifest["portable"]
    contents = {}
    for name in (PORTABLE_PARAMS_FILENAME, PORTABLE_REFERENCE_FILENAME, *block["booster_files"], SPLIT_FILENAME):
        data = _store(lambda name=name: read_bundle_file(bundle_uri, name))
        if len(data) != table[name]["size"] or sha256_hex(data) != table[name]["sha256"]:
            raise _fail(f"{name} of bundle at {bundle_uri} does not match its sha256/size in the manifest")
        contents[name] = data
    # 6 (load first; 5 needs the parsed document)
    model = load_portable(contents)
    # 5
    if manifest.get("model_name") != model.model_name:
        raise _fail(f"the manifest's model_name {manifest.get('model_name')!r} differs from portable.json's {model.model_name!r}")
    expected_boosters = [] if isinstance(model, PortableLogisticRegression) else [booster_filename(k) for k in range(len(model.folds))]
    if block["booster_files"] != expected_boosters:
        raise _fail(f"booster_files {block['booster_files']!r} do not fit model {model.model_name} (expected {expected_boosters!r})")
    if manifest.get("feature_columns") != list(model.feature_columns):
        raise _fail("the manifest's feature_columns differ from portable.json's")
    # 6
    reference = parse_reference(contents[PORTABLE_REFERENCE_FILENAME], model.feature_columns, model_categorical_columns(model))
    n_test = int((reference.kinds == "test").sum())
    if (n_test, len(reference) - n_test) != (block["parity"]["rows"], block["parity"]["probe_rows"]):
        raise _fail(f"the reference holds {n_test} test and {len(reference) - n_test} probe rows; the manifest's parity block says otherwise")
    if list(reference.encounter_ids[:n_test]) != _parse_split_test_ids(contents[SPLIT_FILENAME]):
        raise _fail(f"the reference's test rows are not exactly the test partition of {SPLIT_FILENAME}")
    return model, reference
