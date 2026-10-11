"""Exports an MLflow run as an immutable model bundle (FR4): the logged model's model.pkl and the run's
split_assignment.csv byte for byte, plus a manifest recording every fact the scorer's 14 guards check. The exporter
records faithfully and judges nothing a guard judges: a fact the run lacks is recorded as null and the scorer refuses it.
It refuses only when a needed byte or fact cannot be read at all -- so a non-FINISHED or deleted run IS exported (its status
is recorded; the scorer's guard 2 refuses it, and the CLI warns). Read-only on the MLflow store. See
notes/eg-new-feature/v2-slice2-model-bundle-and-manifest-2026-10-11.md (Interfaces > models/bundle_export.py).
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import mlflow
import mlflow.artifacts
import mlflow.models
from mlflow.exceptions import MlflowException
from mlflow.tracking import MlflowClient

from readmission_risk.bundles.manifest import (
    FINGERPRINT_DEFINITION_V1,
    MANIFEST_SCHEMA_VERSION,
    MODEL_FILENAME,
    SPLIT_FILENAME,
    compute_bundle_id,
    file_entry,
    manifest_bytes,
    sha256_hex,
)
from readmission_risk.bundles.store import write_bundle

from .tracking import _require_store, mlflow_tracking_uri

_ABSENT = object()  # feature_columns.json without a "features" key
_LIBRARY_TAGS = ("sklearn_version", "xgboost_version", "mlflow_version", "pandas_version", "numpy_version")
_JSON_NATIVE = (str, int, float, bool, list, dict)


@dataclass(frozen=True)
class ExportResult:
    bundle_id: str
    bundle_uri: str
    manifest_sha256: str
    created: bool  # False: an identical bundle already existed
    run_status: str  # the run's status as recorded in the manifest (the scorer refuses anything but FINISHED)
    run_lifecycle_stage: str  # likewise (the scorer refuses anything but active)


def _mlmodel_python_version(info) -> object | None:
    """info.flavors['python_function'].get('python_version'), or None if flavors / the python_function flavor / the key
    is absent, or if either level is not a dict (a hand-edited MLmodel: treated as absent, never an AttributeError).
    Returns the raw value, not necessarily a str."""
    flavors = getattr(info, "flavors", None)
    if not isinstance(flavors, dict):
        return None
    pyfunc = flavors.get("python_function")
    if not isinstance(pyfunc, dict):
        return None
    return pyfunc.get("python_version")


def _json_value(value: object) -> object:
    """A JSON-native value as-is; anything else (a YAML date, say) as str(value)."""
    return value if value is None or isinstance(value, _JSON_NATIVE) else str(value)


def _signature(model_info) -> list[dict] | None:
    signature = getattr(model_info, "signature", None)
    if signature is None or signature.inputs is None:
        return None
    return [
        {"name": spec.get("name"), "type": spec.get("type"), "required": spec.get("required")}
        for spec in signature.inputs.to_dict()
    ]


def build_manifest(run, model_info, features_value, files: Mapping[str, bytes]) -> dict:
    """Pure: the manifest (bundle_id included) from an mlflow Run, a ModelInfo, the feature_columns.json "features" value
    (_ABSENT when the key is absent) and the bundle's file bytes."""
    tags, params = run.data.tags, run.data.params
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "model_name": tags.get("model_name"),
        "gold_label_definition": tags.get("gold_label_definition"),
        "gold_fingerprint": tags.get("gold_fingerprint"),
        "gold_fingerprint_definition": FINGERPRINT_DEFINITION_V1,  # the only recipe today (models/data.py)
        "params": {k: params.get(k) for k in ("reference_date", "readmission_window_days", "test_start_date")},
        "library_versions": {k: tags.get(k) for k in _LIBRARY_TAGS},
        "python_version": tags.get("python_version"),
        "mlmodel_python_version": _json_value(_mlmodel_python_version(model_info)),
        "feature_columns": None if features_value is _ABSENT else features_value,
        "signature": _signature(model_info),
        "mlflow": {
            "run_id": run.info.run_id,
            "run_status": run.info.status,
            "run_lifecycle_stage": run.info.lifecycle_stage,
            "model_logged_by_run_id": getattr(model_info, "run_id", None),
            "model_uri": tags.get("model_uri"),
        },
        "git": {"commit": tags.get("mlflow.source.git.commit"), "dirty": tags.get("git_dirty")},
        "files": {name: file_entry(name, data) for name, data in files.items()},
        "portable": None,  # FR0.3 fills this in v2 slice 6 (manifest schema 2)
    }
    return {**manifest, "bundle_id": compute_bundle_id(manifest)}


def _check_sklearn_flavor(model_info, model_uri: str) -> None:
    flavors = getattr(model_info, "flavors", None)
    sklearn_flavor = flavors.get("sklearn") if isinstance(flavors, dict) else None
    if not isinstance(sklearn_flavor, dict) or sklearn_flavor.get("serialization_format") != "cloudpickle":
        raise ValueError(f"export: the logged model {model_uri} is not a cloudpickle sklearn model (the scorer loads cloudpickle models only)")
    if sklearn_flavor.get("pickled_model") != MODEL_FILENAME:
        raise ValueError(f"export: the logged model {model_uri} stores its pickle as {sklearn_flavor.get('pickled_model')!r}, not {MODEL_FILENAME!r}")
    if sklearn_flavor.get("code") is not None:
        raise ValueError(f"export: the logged model {model_uri} was logged with code paths; a bundle carries {MODEL_FILENAME} only")


def _download_run_artifact(client: MlflowClient, run_id: str, path: str, tmp: str) -> bytes:
    try:
        return Path(client.download_artifacts(run_id, path, dst_path=tmp)).read_bytes()
    except (MlflowException, OSError) as exc:
        raise ValueError(f"export: cannot read artifact {path!r} of MLflow run {run_id}: {exc}") from exc


def export_bundle(tracking_dir: Path, run_id: str, bundles_root: Path) -> ExportResult:
    _require_store(tracking_dir)
    original_uri = mlflow.get_tracking_uri()
    mlflow.set_tracking_uri(mlflow_tracking_uri(tracking_dir))
    try:
        client = MlflowClient()
        try:
            run = client.get_run(run_id)
        except MlflowException as exc:
            raise ValueError(f"export: run not found: no MLflow run {run_id!r} in {Path(tracking_dir) / 'mlflow.db'} ({exc})") from exc
        model_uri = run.data.tags.get("model_uri")
        if model_uri is None:
            raise ValueError(f"export: MLflow run {run_id} has no model_uri tag (was it produced by train_and_evaluate?)")
        try:
            model_info = mlflow.models.get_model_info(model_uri)
        except MlflowException as exc:
            raise ValueError(f"export: cannot read the logged model {model_uri!r} of MLflow run {run_id}: {exc}") from exc
        _check_sklearn_flavor(model_info, model_uri)

        with tempfile.TemporaryDirectory() as tmp:
            try:
                model_dir = mlflow.artifacts.download_artifacts(artifact_uri=model_uri, dst_path=str(Path(tmp) / "model"))
            except (MlflowException, OSError) as exc:
                raise ValueError(f"export: cannot download the logged model {model_uri!r}: {exc}") from exc
            try:
                model_bytes = (Path(model_dir) / MODEL_FILENAME).read_bytes()
            except OSError as exc:
                raise ValueError(f"export: cannot read {MODEL_FILENAME} of the logged model {model_uri!r}: {exc}") from exc
            features_raw = _download_run_artifact(client, run_id, "feature_columns.json", tmp)
            split_bytes = _download_run_artifact(client, run_id, SPLIT_FILENAME, tmp)
    finally:
        mlflow.set_tracking_uri(original_uri)

    try:
        features_document = json.loads(features_raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"export: feature_columns.json of MLflow run {run_id} is not JSON: {exc}") from exc
    if not isinstance(features_document, dict):
        raise ValueError(f"export: feature_columns.json of MLflow run {run_id} is not a JSON object")  # noqa: TRY004 -- malformed input is a ValueError by design (the CLI's user-error type)

    files = {MODEL_FILENAME: model_bytes, SPLIT_FILENAME: split_bytes}
    manifest = build_manifest(run, model_info, features_document.get("features", _ABSENT), files)
    uri, created = write_bundle(files, manifest, bundles_root)
    return ExportResult(
        bundle_id=manifest["bundle_id"],
        bundle_uri=uri,
        manifest_sha256=sha256_hex(manifest_bytes(manifest)),
        created=created,
        run_status=run.info.status,
        run_lifecycle_stage=run.info.lifecycle_stage,
    )
