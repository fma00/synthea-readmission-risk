"""The bundle manifest: its schema constants, its canonical JSON form and the content hash that names a bundle.
Standard library only. See notes/eg-new-feature/v2-slice2-model-bundle-and-manifest-2026-10-11.md (Interfaces).

bundle_id = sha256 of the canonical JSON of the manifest without its own bundle_id key. The manifest lists every other
file's sha256 and size and holds no timestamp, so one MLflow run always exports to the same id.
"""

from __future__ import annotations

import hashlib
import json
import re

# Re-exported so the exporter and the guard import both fingerprint definitions from here (v2 slice 5). Its home is
# pipeline/gold_metadata.py, which the Spark job writes and which, like this module, is standard library only.
from readmission_risk.pipeline.gold_metadata import (
    FINGERPRINT_DEFINITION_ROWHASH_V1 as FINGERPRINT_DEFINITION_ROWHASH_V1,
)

MANIFEST_FILENAME = "manifest.json"
MODEL_FILENAME = "model.pkl"
SPLIT_FILENAME = "split_assignment.csv"
FILE_ROLES: dict[str, str] = {MODEL_FILENAME: "model_cloudpickle", SPLIT_FILENAME: "split_assignment"}
MANIFEST_SCHEMA_VERSION = 1
FINGERPRINT_DEFINITION_V1 = "pandas-csv-sha256-v1"
TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version", "bundle_id", "model_name", "gold_label_definition", "gold_fingerprint",
        "gold_fingerprint_definition", "params", "library_versions", "python_version", "mlmodel_python_version",
        "feature_columns", "signature", "mlflow", "git", "files", "portable",
    }
)  # fmt: skip
NESTED_KEYS: dict[str, frozenset[str]] = {
    "params": frozenset({"reference_date", "readmission_window_days", "test_start_date"}),
    "library_versions": frozenset({"sklearn_version", "xgboost_version", "mlflow_version", "pandas_version", "numpy_version"}),
    "mlflow": frozenset({"run_id", "run_status", "run_lifecycle_stage", "model_logged_by_run_id", "model_uri"}),
    "git": frozenset({"commit", "dirty"}),
}
HEX64_RE = re.compile(r"[0-9a-f]{64}")  # use with fullmatch


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(obj: dict) -> bytes:
    """Sorted keys, compact separators, ASCII only, no NaN/inf. ValueError for NaN/inf or a value JSON cannot encode."""
    try:
        text = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    except TypeError as exc:
        raise ValueError(f"manifest holds a value JSON cannot encode: {exc}") from exc
    return text.encode("ascii")


def compute_bundle_id(manifest: dict) -> str:
    return sha256_hex(canonical_json({k: v for k, v in manifest.items() if k != "bundle_id"}))


def manifest_bytes(manifest: dict) -> bytes:
    """The exact bytes of manifest.json. ValueError if the manifest's bundle_id is not its content hash."""
    expected = compute_bundle_id(manifest)
    if manifest.get("bundle_id") != expected:
        raise ValueError(f"manifest bundle_id {manifest.get('bundle_id')!r} is not its content hash {expected}")
    return canonical_json(manifest) + b"\n"


def file_entry(name: str, data: bytes) -> dict:
    return {"sha256": sha256_hex(data), "size": len(data), "role": FILE_ROLES[name]}
