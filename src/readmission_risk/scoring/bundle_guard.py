"""Validating a model bundle and refusing every bundle/gold mismatch BEFORE the model is unpickled (a cloudpickle can
execute arbitrary code on load). Integrity checks I1-I5 (manifest present and parseable, the champion's pin, the content
hash, every file's sha256, the manifest's shape) run first, then guards 1-14 on the manifest's facts, one to one with
the MLflow-run guards they replace. Never imports mlflow. See
notes/eg-new-feature/v2-slice2-model-bundle-and-manifest-2026-10-11.md (Interfaces > scoring/bundle_guard.py and the
guard mapping table); guard 14: notes/eg-new-feature/python-version-guard-2026-10-09.md.

Uniform rule for absent, malformed or unreadable bundle facts: a ValueError whose message carries the check's key
(integrity keys start the message, followed by ": "), never a KeyError / TypeError / OSError / JSON or pandas parse error
(the CLI turns ValueError into "ERROR:" and exit 1).
"""

from __future__ import annotations

import io
import json
import re
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import cloudpickle
import pandas as pd
from sklearn.exceptions import InconsistentVersionWarning

from readmission_risk.bundles.manifest import (
    FILE_ROLES,
    FINGERPRINT_DEFINITION_V1,
    HEX64_RE,
    MANIFEST_FILENAME,
    MANIFEST_SCHEMA_VERSION,
    MODEL_FILENAME,
    NESTED_KEYS,
    SPLIT_FILENAME,
    TOP_LEVEL_KEYS,
    compute_bundle_id,
    manifest_bytes,
    sha256_hex,
)
from readmission_risk.bundles.store import (
    bundle_uri_leaf,
    check_bundle_uri,
    list_bundle_entries,
    read_bundle_file,
)
from readmission_risk.models.constants import MODEL_NAMES
from readmission_risk.models.data import (
    CATEGORICAL_FEATURES,
    COUNT_FEATURES,
    FEATURE_COLUMNS,
    NUMERIC_OTHER_FEATURES,
    gold_fingerprint,
)
from readmission_risk.models.versions import current_python_version
from readmission_risk.pipeline.gold_metadata import GoldMetadata, parse_yyyymmdd

from .constants import PARTITION_TEST, PARTITION_TRAIN

_PYTHON_VERSION_RE = re.compile(r"([0-9]+)\.([0-9]+)(?:\.([0-9]+))?")  # [0-9], not \d: \d also matches non-ASCII digits
_RUN_ID_RE = re.compile(r"[0-9a-f]{32}")
_DISTRIBUTIONS = {
    "sklearn_version": "scikit-learn",
    "xgboost_version": "xgboost",
    "mlflow_version": "mlflow",
    "pandas_version": "pandas",
    "numpy_version": "numpy",
}
_FINGERPRINT_DEFINITIONS: dict[str, Callable[[pd.DataFrame], str]] = {FINGERPRINT_DEFINITION_V1: gold_fingerprint}
_EXPECTED_SIGNATURE_TYPES = {
    **{c: "double" for c in (*COUNT_FEATURES, *NUMERIC_OTHER_FEATURES)},
    **{c: "string" for c in CATEGORICAL_FEATURES},
}


@dataclass(frozen=True, eq=False)  # eq=False: it holds a Series, so the generated __eq__/__hash__ would raise
class ModelBundle:
    bundle_id: str
    bundle_uri: str  # exactly the string validate_bundle was given (the champion's or --bundle's), unresolved
    mlflow_run_id: str  # manifest mlflow.run_id; written to predictions.model_run_id
    model_name: str
    test_start_date: str  # YYYYMMDD (used by guard 12)
    gold_fingerprint: str  # == the gold table's fingerprint once guard 6 has passed
    git_commit: str | None
    git_dirty: str | None
    partition_of: pd.Series  # encounter_id -> "train" | "test" (object dtype, unique index), from split_assignment.csv
    model_bytes: bytes = field(repr=False)  # the sha256-verified model.pkl bytes; load_bundle_model unpickles THESE
    champion_sha256: str | None = None  # the champion pointer's pin (already proven equal by I2); None for --bundle


def installed_versions() -> dict[str, str]:
    """The five library versions guard 8 checks, read from installed distribution metadata (never imports mlflow)."""
    result = {}
    for key, distribution in _DISTRIBUTIONS.items():
        try:
            result[key] = version(distribution)
        except PackageNotFoundError:
            result[key] = "not installed"
    return result


def parse_python_major_minor(value: object) -> tuple[int, int] | None:
    """(major, minor) of an 'X.Y' or 'X.Y.Z' string (re.fullmatch, ASCII digits 0-9 only); None for anything else,
    including non-strings (a hand-edited MLmodel can hold a YAML float such as 3.12)."""
    if not isinstance(value, str):
        return None
    match = _PYTHON_VERSION_RE.fullmatch(value)
    return (int(match.group(1)), int(match.group(2))) if match else None


# --------------------------------------------------------------------------------------------- integrity (I1-I5)
def _store_call(key: str, uri: str, call: Callable):
    """Runs a bundles.store call, re-raising its ValueError / OSError as a ValueError carrying `key`."""
    try:
        return call()
    except FileNotFoundError as exc:
        raise ValueError(_keyed(key, str(exc))) from exc
    except ValueError as exc:
        raise ValueError(_keyed(key, str(exc))) from exc
    except OSError as exc:
        raise ValueError(f"{key}: cannot read bundle at {uri}: {exc}") from exc


def _keyed(key: str, message: str) -> str:
    return message if message.startswith(f"{key}:") else f"{key}: {message}"


def _read_manifest(uri: str, champion_sha: str | None, champion_path: Path | None) -> tuple[dict, list[str]]:
    _store_call("bundle not found", uri, lambda: check_bundle_uri(uri))
    entries = _store_call("bundle not found", uri, lambda: list_bundle_entries(uri))
    # I1 (read)
    if MANIFEST_FILENAME not in entries:
        raise ValueError(f"manifest: bundle at {uri} has no {MANIFEST_FILENAME}")
    raw = _store_call("manifest", uri, lambda: read_bundle_file(uri, MANIFEST_FILENAME))
    # I2, on the raw bytes before any parsing
    if champion_sha is not None:
        actual = sha256_hex(raw)
        if actual != champion_sha:
            raise ValueError(
                f"champion sha256: {champion_path} pins manifest {champion_sha} but the {MANIFEST_FILENAME} of bundle at "
                f"{uri} has sha256 {actual}"
            )
    # I1 (parse)
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"manifest: {MANIFEST_FILENAME} of bundle at {uri} is not UTF-8 JSON: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ValueError(f"manifest: {MANIFEST_FILENAME} of bundle at {uri} is not a JSON object")  # noqa: TRY004 -- malformed input is a ValueError by design (the CLI's user-error type)
    schema = manifest.get("schema_version")
    if type(schema) is not int or schema != MANIFEST_SCHEMA_VERSION:
        raise ValueError(f"manifest: bundle at {uri} has unsupported schema_version {schema!r} (this scorer reads {MANIFEST_SCHEMA_VERSION})")
    # I3
    bundle_id = manifest.get("bundle_id")
    if bundle_id is None:
        raise ValueError(f"bundle_id: manifest of bundle at {uri} has no bundle_id")
    if type(bundle_id) is not str or not HEX64_RE.fullmatch(bundle_id):
        raise ValueError(f"bundle_id: manifest of bundle at {uri} has bundle_id {bundle_id!r}, not 64 lowercase hex characters")
    try:
        expected_id = compute_bundle_id(manifest)
    except ValueError as exc:
        raise ValueError(f"bundle_id: manifest of bundle at {uri} cannot be hashed: {exc}") from exc
    if bundle_id != expected_id:
        raise ValueError(f"bundle_id: manifest of bundle at {uri} says {bundle_id} but its content hash is {expected_id} (edited?)")
    directory = bundle_uri_leaf(uri)
    if directory != bundle_id:
        raise ValueError(f"bundle_id: bundle at {uri} lives in directory {directory!r}, not in its id {bundle_id}")
    if raw != manifest_bytes(manifest):
        raise ValueError(f"bundle_id: {MANIFEST_FILENAME} of bundle at {uri} is not in canonical form")
    return manifest, entries


def _read_files(uri: str, manifest: dict, entries: list[str]) -> dict[str, bytes]:
    """I4: the files table, the directory listing, and each file's size and sha256."""
    table = manifest.get("files")
    if table is None:
        raise ValueError(f"files: manifest of bundle at {uri} has no files table")
    if not isinstance(table, dict) or set(table) != set(FILE_ROLES):
        names = sorted(table) if isinstance(table, dict) else type(table).__name__
        raise ValueError(f"files: the files table of bundle at {uri} must list exactly {sorted(FILE_ROLES)}; got {names}")
    for name, entry in table.items():
        if (
            not isinstance(entry, dict)
            or set(entry) != {"sha256", "size", "role"}
            or type(entry["sha256"]) is not str
            or not HEX64_RE.fullmatch(entry["sha256"])
            or type(entry["size"]) is not int
            or entry["role"] != FILE_ROLES[name]
        ):
            raise ValueError(f"files: entry {name!r} of bundle at {uri} is malformed: {entry!r}")
    expected = sorted({MANIFEST_FILENAME, *FILE_ROLES})
    if entries != expected:
        missing, unlisted = sorted(set(expected) - set(entries)), sorted(set(entries) - set(expected))
        raise ValueError(f"files: bundle at {uri} is missing {missing} and holds unlisted entries {unlisted}")
    contents = {}
    for name, entry in table.items():
        data = _store_call("files", uri, lambda name=name: read_bundle_file(uri, name))
        if len(data) != entry["size"] or sha256_hex(data) != entry["sha256"]:
            raise ValueError(
                f"files: {name} of bundle at {uri} has size {len(data)} and sha256 {sha256_hex(data)}; the manifest records "
                f"size {entry['size']} and sha256 {entry['sha256']}"
            )
        contents[name] = data
    return contents


def _check_shape(uri: str, manifest: dict) -> None:
    """I5: the key sets, and the types of the facts no guard judges but the output uses."""
    key = "manifest shape"
    keys = set(manifest)
    if keys != TOP_LEVEL_KEYS:
        raise ValueError(
            f"{key}: manifest of bundle at {uri} is missing {sorted(TOP_LEVEL_KEYS - keys)} and has unknown keys "
            f"{sorted(keys - TOP_LEVEL_KEYS)}"
        )
    for name, nested in NESTED_KEYS.items():
        value = manifest[name]
        if not isinstance(value, dict) or set(value) != nested:
            raise ValueError(f"{key}: {name!r} of bundle at {uri} must be an object with exactly the keys {sorted(nested)}; got {value!r}")
    mlflow_block, git = manifest["mlflow"], manifest["git"]
    if type(mlflow_block["run_id"]) is not str or not _RUN_ID_RE.fullmatch(mlflow_block["run_id"]):
        raise ValueError(f"{key}: mlflow.run_id of bundle at {uri} must be 32 lowercase hex characters; got {mlflow_block['run_id']!r}")
    if type(mlflow_block["model_uri"]) is not str:
        raise ValueError(f"{key}: mlflow.model_uri of bundle at {uri} must be a string; got {mlflow_block['model_uri']!r}")
    for name in ("commit", "dirty"):
        if git[name] is not None and type(git[name]) is not str:
            raise ValueError(f"{key}: git.{name} of bundle at {uri} must be a string or null; got {git[name]!r}")
    if manifest["portable"] is not None:
        raise ValueError(f"{key}: portable of bundle at {uri} must be null in schema_version 1; got {manifest['portable']!r}")


# --------------------------------------------------------------------------------------------- guards 11, 12, 14
def _read_assignment(data: bytes, gold: pd.DataFrame, who: str) -> pd.Series:
    key = "split_assignment.csv"
    try:
        frame = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False)  # keep_default_na: an id "NA" stays a string
    except (pd.errors.EmptyDataError, pd.errors.ParserError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"{key}: file of {who} cannot be parsed: {exc}") from exc
    if list(frame.columns) != ["encounter_id", "partition"]:
        raise ValueError(f"{key}: columns must be exactly ['encounter_id', 'partition']; got {list(frame.columns)}")
    if frame["encounter_id"].duplicated().any():
        raise ValueError(f"{key}: encounter_id values are not unique")
    unknown = sorted(set(frame["partition"]) - {PARTITION_TRAIN, PARTITION_TEST})
    if unknown:
        raise ValueError(f"{key}: partition values must be 'train' or 'test'; found {unknown}")
    if not (frame["partition"] == PARTITION_TEST).any():
        raise ValueError(f"{key}: no 'test' row")
    absent = frame.loc[~frame["encounter_id"].isin(gold["encounter_id"]), "encounter_id"]
    if len(absent):
        raise ValueError(f"{key}: {len(absent)} encounter_id(s) are not in the gold table, e.g. {absent.iloc[0]!r}")
    return pd.Series(frame["partition"].to_numpy(dtype=object), index=pd.Index(frame["encounter_id"], dtype=object))


def _check_split_consistency(partition_of: pd.Series, test_start_date: object, gold: pd.DataFrame) -> None:
    key = "split consistency"
    try:
        t_date = parse_yyyymmdd(test_start_date, "test_start_date")
    except ValueError as exc:
        raise ValueError(f"{key}: the bundle's test_start_date param is absent or invalid: {exc}") from exc
    t = pd.Timestamp(t_date.isoformat(), tz="UTC")
    by_id = gold.set_index("encounter_id")
    starts = by_id["index_start"].reindex(partition_of.index)
    patients = by_id["patient_id"].reindex(partition_of.index)
    is_test = (partition_of == PARTITION_TEST).to_numpy()
    n_test_before = int(((starts < t).to_numpy() & is_test).sum())
    n_train_after = int(((starts >= t).to_numpy() & ~is_test).sum())
    n_patients_both = len(set(patients[is_test]) & set(patients[~is_test]))
    if n_test_before or n_train_after or n_patients_both:
        raise ValueError(
            f"{key}: split_assignment.csv disagrees with the bundle's test_start_date {test_start_date} and the gold table: "
            f"{n_test_before} test row(s) start before test_start_date, {n_train_after} train row(s) start on/after it, "
            f"{n_patients_both} patient(s) appear in both partitions"
        )


def _check_python_version(tag: object, mlmodel: object, who: str) -> None:
    """Guard 14: the Python major.minor the model was cloudpickled under (the run tag, or for runs trained before the tag
    existed the version MLflow wrote into the logged model's MLmodel; both copied into the manifest at export) must equal
    the running interpreter's. Same steps, order and messages as the MLflow-run guard it replaces; (vi) relies on
    (iii)/(iv) having rejected anything unparseable."""
    # (i)
    running = current_python_version()
    running_mm_t = parse_python_major_minor(running)
    tag_mm = parse_python_major_minor(tag) if tag is not None else None
    mlmodel_mm = parse_python_major_minor(mlmodel) if mlmodel is not None else None
    # (ii)
    if tag is None and mlmodel is None:
        raise ValueError(
            f"python version: {who} has no 'python_version' tag and its logged model's MLmodel records no "
            "python_version, so the interpreter its cloudpickle was written by is unknown"
        )
    # (iii)
    if tag is not None and tag_mm is None:
        raise ValueError(f"python version: {who} tag 'python_version' is {tag!r}, not a version of the form X.Y or X.Y.Z")
    # (iv)
    if mlmodel is not None and mlmodel_mm is None:
        raise ValueError(
            f"python version: the logged model of {who} records MLmodel python_version {mlmodel!r}, not a version of the "
            "form X.Y or X.Y.Z"
        )
    # (v)
    if tag is not None and mlmodel is not None and tag_mm != mlmodel_mm:
        raise ValueError(
            f"python version: {who} has tag python_version {tag!r} but its logged model's MLmodel records {mlmodel!r} "
            "(inconsistent provenance)"
        )
    # (vi)
    if tag is not None:
        recorded, recorded_mm_t, source = tag, tag_mm, "run tag"
    else:
        recorded, recorded_mm_t, source = mlmodel, mlmodel_mm, "logged model's MLmodel"
    if recorded_mm_t != running_mm_t:
        recorded_mm, running_mm = "{}.{}".format(*recorded_mm_t), "{}.{}".format(*running_mm_t)
        raise ValueError(
            f"python version mismatch for {who}: trained under Python {recorded} (from the {source}), running Python "
            f"{running}; a cloudpickled model is only loaded under the Python major.minor it was written by. Retrain under "
            f"Python {running_mm} or score with Python {recorded_mm}"
        )


def _check_signature(signature: object, who: str) -> None:
    key = "signature"
    expected = list(FEATURE_COLUMNS)
    if not isinstance(signature, list) or not all(
        isinstance(s, dict) and set(s) == {"name", "type", "required"} and type(s["name"]) is str and type(s["type"]) is str
        for s in signature
    ):
        raise ValueError(f"{key} of the logged model of {who} is absent or malformed: {signature!r}")
    names = [s["name"] for s in signature]
    if names != expected:
        raise ValueError(f"{key} of the logged model of {who} has input names {names!r}, expected {expected}")
    wrong = [f"{s['name']}: {s['type']}" for s in signature if s["type"] != _EXPECTED_SIGNATURE_TYPES[s["name"]]]
    if wrong:
        raise ValueError(f"{key} of the logged model of {who} has input types that differ from the feature contract: {wrong}")
    optional = [s["name"] for s in signature if s["required"] is not True]
    if optional:
        raise ValueError(f"{key} of the logged model of {who} marks input(s) as not required: {optional}")


# --------------------------------------------------------------------------------------------- the guard sequence
def validate_bundle(
    bundle_uri: str,
    *,
    gold: pd.DataFrame,
    metadata: GoldMetadata,
    expected_manifest_sha256: str | None = None,
    champion_path: Path | None = None,
) -> ModelBundle:
    """Integrity checks I1-I5, then guards 1-14, raising ValueError (message carries the check's key) on the first failure.
    champion_path is used only in the I2 message. Reads files through bundles.store only and never unpickles."""
    manifest, entries = _read_manifest(bundle_uri, expected_manifest_sha256, champion_path)
    contents = _read_files(bundle_uri, manifest, entries)
    _check_shape(bundle_uri, manifest)

    bundle_id, block, params = manifest["bundle_id"], manifest["mlflow"], manifest["params"]
    run_id = block["run_id"]
    who = f"bundle {bundle_id[:12]} (MLflow run {run_id})"

    # 2
    if block["run_status"] != "FINISHED":
        raise ValueError(f"{who} is not FINISHED (status {block['run_status']!r} at export)")
    if block["run_lifecycle_stage"] != "active":
        raise ValueError(f"{who} was deleted or not active at export (lifecycle stage {block['run_lifecycle_stage']!r})")

    # 3 is I4 (the model file is present and verified); 4
    model_name = manifest["model_name"]
    if model_name is None:
        raise ValueError(f"{who} has no 'model_name'")
    if type(model_name) is not str or model_name not in MODEL_NAMES:
        raise ValueError(f"{who} has model_name {model_name!r}; expected one of {MODEL_NAMES}")

    # 5
    label = manifest["gold_label_definition"]
    if label is None:
        raise ValueError(
            f"{who} has no 'gold_label_definition': it predates slice 2b (all-cause label) and is not comparable"
        )
    if label != metadata.label_definition:
        raise ValueError(
            f"{who} was trained on gold_label_definition {label!r}, but the gold table's label definition is "
            f"{metadata.label_definition!r}"
        )

    # 6
    definition = manifest["gold_fingerprint_definition"]
    if type(definition) is not str:
        raise ValueError(f"{who} has gold_fingerprint definition {definition!r}, not a string")
    if definition not in _FINGERPRINT_DEFINITIONS:
        raise ValueError(
            f"{who} has gold_fingerprint definition {definition!r}, unknown to this scorer (known: {sorted(_FINGERPRINT_DEFINITIONS)})"
        )
    fingerprint = _FINGERPRINT_DEFINITIONS[definition](gold)
    if manifest["gold_fingerprint"] != fingerprint:
        raise ValueError(
            f"{who} has gold_fingerprint {manifest['gold_fingerprint']!r}, but this gold table's is {fingerprint!r}: the "
            "model was not trained on this table (or the table was edited)"
        )

    # 7 (each defect names only the offending param)
    if params["reference_date"] != metadata.reference_date:
        raise ValueError(
            f"{who} param 'reference_date' is {params['reference_date']!r}, but the gold table's is {metadata.reference_date!r}"
        )
    if params["readmission_window_days"] != str(metadata.readmission_window_days):
        raise ValueError(
            f"{who} param 'readmission_window_days' is {params['readmission_window_days']!r}, but the gold table's is "
            f"{metadata.readmission_window_days!r}"
        )

    # 8
    recorded = manifest["library_versions"]
    mismatches = [
        f"{name}: bundle has {recorded[name]!r}, installed {installed!r}"
        for name, installed in installed_versions().items()
        if recorded[name] != installed
    ]
    if mismatches:
        raise ValueError(
            f"library version mismatch for {who} (the model is a cloudpickle, valid only under the lockfile it was trained "
            f"with): {'; '.join(mismatches)}"
        )

    # 9
    features = manifest["feature_columns"]
    if type(features) is not list or features != list(FEATURE_COLUMNS):
        raise ValueError(
            f"feature_columns.json of {who} lists features {features!r}, expected {list(FEATURE_COLUMNS)} (the run was "
            "trained with a different feature contract)"
        )

    # 10
    _check_signature(manifest["signature"], who)

    # 11
    partition_of = _read_assignment(contents[SPLIT_FILENAME], gold, who)

    # 12
    _check_split_consistency(partition_of, params["test_start_date"], gold)

    # 13
    logged_by = block["model_logged_by_run_id"]
    if type(logged_by) is not str:
        raise ValueError(f"model provenance: {who} records no model run ({logged_by!r})")
    if logged_by != run_id:
        raise ValueError(f"model provenance: the model of {who} was logged by run {logged_by!r}")

    # 14
    _check_python_version(manifest["python_version"], manifest["mlmodel_python_version"], who)

    return ModelBundle(
        bundle_id=bundle_id,
        bundle_uri=bundle_uri,
        mlflow_run_id=run_id,
        model_name=model_name,
        test_start_date=params["test_start_date"],
        gold_fingerprint=fingerprint,
        git_commit=manifest["git"]["commit"],
        git_dirty=manifest["git"]["dirty"],
        partition_of=partition_of,
        model_bytes=contents[MODEL_FILENAME],
        champion_sha256=expected_manifest_sha256,
    )


def load_bundle_model(bundle: ModelBundle):
    """Unpickles the verified model bytes. Call ONLY after validate_bundle has passed. scikit-learn's
    InconsistentVersionWarning is turned into an error: guard 8 should make it impossible, this makes a drift loud."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", InconsistentVersionWarning)
        model = cloudpickle.loads(bundle.model_bytes)  # trusted input: sha256-verified against the manifest
    if not callable(getattr(model, "predict_proba", None)):
        raise RuntimeError(f"the model of bundle {bundle.bundle_id[:12]} has no predict_proba")  # noqa: TRY004 -- a broken model is a RuntimeError by design (a traceback, not a user error)
    return model
