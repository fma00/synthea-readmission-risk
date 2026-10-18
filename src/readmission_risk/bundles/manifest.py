"""The bundle manifest: its schema constants, its canonical JSON form and the content hash that names a bundle.
Standard library only. See notes/eg-new-feature/v2-slice2-model-bundle-and-manifest-2026-10-11.md (Interfaces); schema 2
(the portable form of a model, FR0.3): notes/eg-new-feature/v2-slice6c-portable-v1-export-2026-10-17.md.

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
MANIFEST_SCHEMA_VERSION = 1  # what build_manifest (the MLflow-run export) writes
PORTABLE_MANIFEST_SCHEMA_VERSION = 2  # a schema-1 bundle plus the portable form (v2 slice 6c)
SUPPORTED_SCHEMA_VERSIONS = (MANIFEST_SCHEMA_VERSION, PORTABLE_MANIFEST_SCHEMA_VERSION)
PORTABLE_PARAMS_FILENAME = "portable.json"
PORTABLE_REFERENCE_FILENAME = "portable_reference.csv"
PORTABLE_BOOSTER_RE = re.compile(r"portable_fold_(0|[1-9][0-9]*)\.ubj")  # use with fullmatch
PORTABLE_ROLE_PARAMS, PORTABLE_ROLE_REFERENCE, PORTABLE_ROLE_BOOSTER = "portable_params", "portable_reference", "portable_booster"
PORTABLE_FORMAT_VERSION = 1
PORTABLE_KEYS = frozenset({"format_version", "params_file", "reference_file", "booster_files", "parity", "writer_versions"})
PORTABLE_PARITY_KEYS = frozenset({"rows", "probe_rows", "max_abs_diff"})
PORTABLE_WRITER_VERSION_KEYS = frozenset({"numpy", "scipy", "xgboost", "python", "platform", "blas"})
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


def booster_filename(fold: int) -> str:
    return f"portable_fold_{fold}.ubj"


def file_role(name: str) -> str:
    """The role of a bundle file name in either schema; ValueError for a name no schema knows."""
    if name in FILE_ROLES:
        return FILE_ROLES[name]
    if name == PORTABLE_PARAMS_FILENAME:
        return PORTABLE_ROLE_PARAMS
    if name == PORTABLE_REFERENCE_FILENAME:
        return PORTABLE_ROLE_REFERENCE
    if PORTABLE_BOOSTER_RE.fullmatch(name):
        return PORTABLE_ROLE_BOOSTER
    raise ValueError(f"unknown bundle file name {name!r}")


def file_entry(name: str, data: bytes) -> dict:
    return {"sha256": sha256_hex(data), "size": len(data), "role": file_role(name)}


def _is_int(value: object) -> bool:
    return type(value) is int


def _is_number(value: object) -> bool:
    return type(value) in (int, float)


def check_schema2_files_table(table: object) -> None:
    """The file names a schema-2 files table may hold: the schema-1 files, portable.json, portable_reference.csv and
    portable_fold_<k>.ubj files. Entry shapes and roles are the caller's check. ValueError (no key prefix) otherwise."""
    if not isinstance(table, dict):
        raise ValueError(f"must be an object; got {type(table).__name__}")  # noqa: TRY004 -- malformed input is a ValueError by design
    required = {*FILE_ROLES, PORTABLE_PARAMS_FILENAME, PORTABLE_REFERENCE_FILENAME}
    missing = sorted(required - set(table))
    unknown = sorted(name for name in set(table) - required if not (type(name) is str and PORTABLE_BOOSTER_RE.fullmatch(name)))
    if missing or unknown:
        raise ValueError(
            f"must list {sorted(required)} plus portable_fold_<k>.ubj files only; missing {missing}, unknown {unknown}"
        )


def check_portable_block(manifest: dict) -> None:
    """The schema-2 `portable` block and its agreement with the files table's booster entries. Structural only (it does
    not tie booster_files to model_name). ValueError (no key prefix), never KeyError/TypeError."""
    block = manifest.get("portable")
    if not isinstance(block, dict) or set(block) != PORTABLE_KEYS:
        keys = sorted(block) if isinstance(block, dict) else block
        raise ValueError(f"must be an object with exactly the keys {sorted(PORTABLE_KEYS)}; got {keys!r}")
    if not _is_int(block["format_version"]) or block["format_version"] != PORTABLE_FORMAT_VERSION:
        raise ValueError(f"format_version must be {PORTABLE_FORMAT_VERSION}; got {block['format_version']!r}")
    if block["params_file"] != PORTABLE_PARAMS_FILENAME:
        raise ValueError(f"params_file must be {PORTABLE_PARAMS_FILENAME!r}; got {block['params_file']!r}")
    if block["reference_file"] != PORTABLE_REFERENCE_FILENAME:
        raise ValueError(f"reference_file must be {PORTABLE_REFERENCE_FILENAME!r}; got {block['reference_file']!r}")
    boosters = block["booster_files"]
    if not isinstance(boosters, list) or not all(type(b) is str for b in boosters):
        raise ValueError(f"booster_files must be a list of strings; got {boosters!r}")
    if boosters != [booster_filename(k) for k in range(len(boosters))]:
        raise ValueError(f"booster_files must be portable_fold_0.ubj .. portable_fold_<k-1>.ubj in order; got {boosters!r}")
    table = manifest.get("files")
    if not isinstance(table, dict) or not all(isinstance(e, dict) and type(e.get("role")) is str for e in table.values()):
        raise ValueError("the files table must be an object of objects that each carry a string role")
    in_table = {name for name, entry in table.items() if entry["role"] == PORTABLE_ROLE_BOOSTER}
    if set(boosters) != in_table:
        raise ValueError(f"booster_files {boosters!r} disagree with the files table's boosters {sorted(in_table)}")
    parity = block["parity"]
    if not isinstance(parity, dict) or set(parity) != PORTABLE_PARITY_KEYS:
        raise ValueError(f"parity must be an object with exactly the keys {sorted(PORTABLE_PARITY_KEYS)}; got {parity!r}")
    if not _is_int(parity["rows"]) or parity["rows"] <= 0:
        raise ValueError(f"parity.rows must be a positive integer; got {parity['rows']!r}")
    if not _is_int(parity["probe_rows"]) or parity["probe_rows"] < 0:
        raise ValueError(f"parity.probe_rows must be a non-negative integer; got {parity['probe_rows']!r}")
    if not _is_number(parity["max_abs_diff"]) or parity["max_abs_diff"] != 0:
        raise ValueError(f"parity.max_abs_diff must be 0; got {parity['max_abs_diff']!r}")
    versions = block["writer_versions"]
    if (
        not isinstance(versions, dict)
        or set(versions) != PORTABLE_WRITER_VERSION_KEYS
        or not all(type(v) is str for v in versions.values())
    ):
        raise ValueError(
            f"writer_versions must map exactly {sorted(PORTABLE_WRITER_VERSION_KEYS)} to strings; got {versions!r}"
        )


def expected_file_names(manifest: dict) -> set[str]:
    """The writer's view: the exact file set (manifest.json excluded) a bundle with this manifest holds. ValueError for an
    unsupported schema, a schema-1 manifest with a non-null portable, or a schema-2 manifest the scorer would refuse at
    I5 (wrong top-level keys, malformed portable block)."""
    schema = manifest.get("schema_version")
    if type(schema) is not int or schema not in SUPPORTED_SCHEMA_VERSIONS:
        raise ValueError(f"unsupported schema_version {schema!r} (supported: {list(SUPPORTED_SCHEMA_VERSIONS)})")
    if schema == MANIFEST_SCHEMA_VERSION:
        if manifest.get("portable") is not None:
            raise ValueError("a schema_version 1 manifest must have a null portable")
        return set(FILE_ROLES)
    if set(manifest) != TOP_LEVEL_KEYS:
        raise ValueError(f"a schema_version 2 manifest must have exactly the keys {sorted(TOP_LEVEL_KEYS)}")
    check_portable_block(manifest)
    return {*FILE_ROLES, PORTABLE_PARAMS_FILENAME, PORTABLE_REFERENCE_FILENAME, *manifest["portable"]["booster_files"]}
