"""The `_gold_metadata.json` sidecar written next to the gold Parquet by the Big Join and required by model training.
See notes/eg-new-feature/readmission-label-planned-exclusion-2026-09-20.md for the design this implements.

Stdlib only, on purpose: readmission_risk.models imports this module, and the models package must never need Spark.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, fields
from datetime import date
from itertools import pairwise
from pathlib import Path

GOLD_METADATA_FILENAME = "_gold_metadata.json"
GOLD_METADATA_VERSION = 2  # what the Big Join writes
SUPPORTED_GOLD_METADATA_VERSIONS: tuple[int, ...] = (1, 2)  # what readers accept: v1 = the slice-2b gold (full_run_2b)
# The content fingerprint the Big Join computes in Spark (pipeline/fingerprint.py) and records in version-2 metadata. Defined
# here, not in fingerprint.py, because this module is stdlib-only: bundles/manifest.py re-exports it without importing pandas.
FINGERPRINT_DEFINITION_ROWHASH_V1 = "spark-rowhash-v1"
# Fields that exist only in version 2 (all None in a version-1 object, and absent from a version-1 file), in order.
V2_ONLY_FIELDS: tuple[str, ...] = (
    "gold_fingerprint",
    "gold_fingerprint_definition",
    "spark_version",
    "generation_summary",
    "build_provenance",
)
_HEX64_RE = re.compile(r"[0-9a-f]{64}")
LABEL_DEFINITION = "unplanned_readmission_v1"
PLANNED_PROCEDURE_CODES: tuple[str, ...] = ("33195004", "367336001", "703423002")
# SNOMED-CT: 33195004 External beam radiation therapy procedure; 367336001 Chemotherapy (procedure);
# 703423002 Combined chemotherapy and radiation therapy (procedure). Evidence: on inpatient stays these are the ONLY
# chemo/radiation codes present (703423002 on 3,315 stays, 367336001 on 4, 33195004 on 1). Strictly sorted, unique.
# Deliberately NOT included: transplant / bone-marrow codes (70536003, 88039007, 58776007, 234336002, 58390007). Measured
# on the pre-terminal-rule cohort: adding them would exclude 270 more index rows (9,921 -> 9,651) and change positives 207 -> 202
# (not re-measured for the final terminal-only cohort). A defensible extension ("scheduled surgery") but not part of the code set
# the user approved; named follow-up.
#
# IDENTITY RULE: LABEL_DEFINITION identifies the label's semantics. ANY change to PLANNED_PROCEDURE_CODES, the continuation
# rule (incl. its boundary/tie-break), the terminal rule, the index-candidate definition (inpatient class, non-null STOP, not
# planned, terminal), the readmission window bounds (stop, stop+W] (strict lower, inclusive upper), the planned-index exclusion,
# or the death/censoring keep-rule MUST bump it (..._v2). Consumers check BOTH constants against the metadata, and a
# change-detector test pins the constants; the rules themselves are pinned only by the behavioural tests, so this is a process rule.

_INT_FIELDS: tuple[str, ...] = (
    "metadata_version",
    "readmission_window_days",
    "lookback_years",
    "n_rows",
    "n_positive",
    "n_inpatient_stays",
    "n_planned_stays",
    "n_continuation_stays",
    "n_nonterminal_stays",
)


def parse_yyyymmdd(value: object, field: str) -> date:
    """THE one YYYYMMDD parser (used by big_join, training and the metadata validator). Exactly 8 ASCII digits."""
    if not (isinstance(value, str) and value.isascii() and value.isdigit() and len(value) == 8):
        raise ValueError(f"{field} must be a YYYYMMDD string; got {value!r}")
    try:
        return date(int(value[:4]), int(value[4:6]), int(value[6:8]))
    except ValueError as exc:
        raise ValueError(f"{field} is not a valid calendar date: {value!r}") from exc


@dataclass(frozen=True)
class GoldMetadata:
    label_definition: str  # non-empty
    reference_date: str  # exactly 8 ASCII digits, a valid calendar date (YYYYMMDD)
    readmission_window_days: int  # >= 1
    lookback_years: int  # >= 1
    planned_procedure_codes: tuple[str, ...]  # strictly sorted, unique, non-empty strings; non-empty tuple
    n_rows: int  # >= 0: rows in the gold table
    n_positive: int  # 0 <= n_positive <= n_rows
    n_inpatient_stays: int  # >= 0: ALL inpatient stays in encounters.csv
    n_planned_stays: int  # 0 <= . <= n_inpatient_stays: inpatient stays matching PLANNED_PROCEDURE_CODES
    n_continuation_stays: int  # 0 <= . <= n_inpatient_stays: inpatient stays flagged is_continuation
    n_nonterminal_stays: int  # 0 <= . <= n_inpatient_stays: inpatient stays flagged NOT is_terminal
    metadata_version: int = GOLD_METADATA_VERSION
    # Version 2 only (None in version 1). Frozen but NOT hashable: the two dicts make the generated __hash__ raise TypeError;
    # nothing hashes GoldMetadata. The dicts are deep-copied by the writer and the parser, so a caller's later mutation of
    # its own dict never changes validated metadata.
    gold_fingerprint: str | None = None  # 64 lowercase hex, FINGERPRINT_DEFINITION_ROWHASH_V1 over the written Parquet
    gold_fingerprint_definition: str | None = None  # == FINGERPRINT_DEFINITION_ROWHASH_V1
    spark_version: str | None = None  # spark.version of the session that built the table
    generation_summary: dict | None = None  # the raw run's generation_summary.json, embedded (lineage outlives raw's 90 days)
    build_provenance: dict | None = None  # str -> str: the code that built the gold (head_commit, zip_sha256, ...); {} locally
    # Cross-field invariant (rows are a subset of the non-planned inpatient stays): n_rows + n_planned_stays <= n_inpatient_stays.
    # Deliberately NOT checked: any relation between n_continuation_stays, n_nonterminal_stays and n_rows (overlaps make it
    # data-dependent; the two counts are equal on the real data, 682 = 682, but not in general).


def _fail(source: str, field: str, message: str) -> None:
    raise ValueError(f"{source}: {field}: {message}")


def _validate(metadata: GoldMetadata, source: str) -> None:
    """Raises ValueError (message prefixed with `source`, naming the offending field) if `metadata` breaks any rule. Deliberately
    a module-level function, not GoldMetadata.__post_init__: tests must be able to construct invalid objects."""
    if not isinstance(metadata.label_definition, str) or not metadata.label_definition:
        _fail(source, "label_definition", f"must be a non-empty str; got {metadata.label_definition!r}")
    for name in _INT_FIELDS:
        value = getattr(metadata, name)
        if type(value) is not int:  # rejects bool and float
            _fail(source, name, f"must be an int; got {value!r}")
    if metadata.metadata_version not in SUPPORTED_GOLD_METADATA_VERSIONS:
        _fail(source, "metadata_version", _unsupported_version_message(metadata.metadata_version))
    try:
        parse_yyyymmdd(metadata.reference_date, "reference_date")
    except ValueError as exc:
        raise ValueError(f"{source}: reference_date: {exc}") from exc
    if metadata.readmission_window_days < 1:
        _fail(source, "readmission_window_days", f"must be >= 1; got {metadata.readmission_window_days}")
    if metadata.lookback_years < 1:
        _fail(source, "lookback_years", f"must be >= 1; got {metadata.lookback_years}")
    if metadata.n_rows < 0:
        _fail(source, "n_rows", f"must be >= 0; got {metadata.n_rows}")
    if not 0 <= metadata.n_positive <= metadata.n_rows:
        _fail(source, "n_positive", f"must be in [0, n_rows={metadata.n_rows}]; got {metadata.n_positive}")
    if metadata.n_inpatient_stays < 0:
        _fail(source, "n_inpatient_stays", f"must be >= 0; got {metadata.n_inpatient_stays}")
    for name in ("n_planned_stays", "n_continuation_stays", "n_nonterminal_stays"):
        value = getattr(metadata, name)
        if not 0 <= value <= metadata.n_inpatient_stays:
            _fail(source, name, f"must be in [0, n_inpatient_stays={metadata.n_inpatient_stays}]; got {value}")
    if metadata.n_rows + metadata.n_planned_stays > metadata.n_inpatient_stays:
        _fail(
            source,
            "n_rows/n_planned_stays/n_inpatient_stays",
            f"n_rows ({metadata.n_rows}) + n_planned_stays ({metadata.n_planned_stays}) must not exceed "
            f"n_inpatient_stays ({metadata.n_inpatient_stays})",
        )
    codes = metadata.planned_procedure_codes
    if not isinstance(codes, tuple) or not codes or not all(isinstance(c, str) and c for c in codes):
        _fail(source, "planned_procedure_codes", f"must be a non-empty tuple of non-empty strings; got {codes!r}")
    if any(a >= b for a, b in pairwise(codes)):
        _fail(source, "planned_procedure_codes", f"must be strictly sorted and duplicate-free; got {codes!r}")
    if metadata.metadata_version == 1:
        for name in V2_ONLY_FIELDS:
            if getattr(metadata, name) is not None:
                _fail(source, name, "exists only in metadata version 2; it must be absent in version 1")
        return
    for name in ("gold_fingerprint", "gold_fingerprint_definition", "spark_version"):
        value = getattr(metadata, name)
        if not isinstance(value, str) or not value:
            _fail(source, name, f"must be a non-empty str in version 2; got {value!r}")
    if not _HEX64_RE.fullmatch(metadata.gold_fingerprint):
        _fail(source, "gold_fingerprint", f"must be 64 lowercase hex characters; got {metadata.gold_fingerprint!r}")
    if metadata.gold_fingerprint_definition != FINGERPRINT_DEFINITION_ROWHASH_V1:
        _fail(
            source,
            "gold_fingerprint_definition",
            f"must be {FINGERPRINT_DEFINITION_ROWHASH_V1!r}; got {metadata.gold_fingerprint_definition!r}",
        )
    summary = metadata.generation_summary
    if not isinstance(summary, dict) or not all(isinstance(k, str) for k in summary):
        _fail(source, "generation_summary", f"must be a JSON object in version 2; got {type(summary).__name__}")
    if summary.get("reference_date") != metadata.reference_date:
        _fail(
            source,
            "generation_summary",
            f"its reference_date {summary.get('reference_date')!r} differs from the metadata's {metadata.reference_date!r}",
        )
    provenance = metadata.build_provenance
    if not isinstance(provenance, dict) or not all(
        isinstance(k, str) and k and isinstance(v, str) and v for k, v in provenance.items()
    ):
        _fail(source, "build_provenance", f"must be an object of non-empty strings in version 2; got {provenance!r}")


def _unsupported_version_message(version: object) -> str:
    supported = ", ".join(str(v) for v in SUPPORTED_GOLD_METADATA_VERSIONS)
    return f"unsupported version {version!r} (this code reads {supported})"


def metadata_to_dict(metadata: GoldMetadata) -> dict:
    """asdict(metadata), without the version-2-only keys for a version-1 object (so a v1 dict has exactly the 12 keys a
    v1 file has). Does not validate."""
    d = asdict(metadata)
    if metadata.metadata_version == 1:
        for name in V2_ONLY_FIELDS:
            del d[name]
    return d


def metadata_to_json(metadata: GoldMetadata) -> str:
    """Validates, then the file text: sorted-key JSON, 2-space indent, trailing newline (a v1 object serializes exactly as
    a v1 file always has)."""
    _validate(metadata, "<in-memory>")
    return json.dumps(metadata_to_dict(metadata), indent=2, sort_keys=True) + "\n"


def write_gold_metadata(gold_dir: Path, metadata: GoldMetadata) -> Path:
    """Validates, then writes `gold_dir/_gold_metadata.json` atomically (temp file + os.replace). Does not sort or repair
    anything: an invalid object is never written."""
    text = metadata_to_json(metadata)
    gold_dir = Path(gold_dir)
    if not gold_dir.is_dir():
        raise FileNotFoundError(f"gold_dir is not an existing directory: {gold_dir}")
    final = gold_dir / GOLD_METADATA_FILENAME
    tmp = gold_dir / (GOLD_METADATA_FILENAME + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, final)
    return final


def read_gold_metadata(gold_dir: Path) -> GoldMetadata:
    """Reads and validates `gold_dir/_gold_metadata.json`. FileNotFoundError if absent (with a rebuild instruction); ValueError
    (message contains the file path) for anything unreadable or invalid. Does NOT compare label_definition or
    planned_procedure_codes to this module's constants: that is the consumer's decision."""
    path = Path(gold_dir) / GOLD_METADATA_FILENAME
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise FileNotFoundError(
            f"Gold metadata not found: {path} -- this gold table predates slice 2b or is incomplete; "
            "rebuild it with scripts/run_big_join.py"
        ) from None
    except OSError as exc:
        raise ValueError(f"{path}: cannot read the metadata file: {exc}") from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{path}: not valid UTF-8: {exc}") from exc
    return parse_gold_metadata(text, str(path))


def parse_gold_metadata(text: str, source: str) -> GoldMetadata:
    """Parses and validates the text of a metadata file (local or read from gs:// by the Big Join's storage). Every problem
    is a ValueError prefixed with `source`. metadata_version is checked first (type(v) is int, and supported), so a wrong
    version is reported as such rather than as a key-set mismatch; then the key set must be exactly that version's."""

    def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        seen: dict[str, object] = {}
        for key, value in pairs:
            if key in seen:
                raise ValueError(f"{source}: duplicate key {key!r}")
            seen[key] = value
        return seen

    try:
        obj = json.loads(text, object_pairs_hook=_no_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{source}: invalid JSON: {exc}") from exc
    if not isinstance(obj, dict):
        # ValueError, not TypeError: the design specifies one exception type for every bad-file case
        raise ValueError(f"{source}: top level must be a JSON object; got {type(obj).__name__}")  # noqa: TRY004
    if "metadata_version" not in obj:
        raise ValueError(f"{source}: missing key(s) ['metadata_version']")
    version = obj["metadata_version"]
    if type(version) is not int or version not in SUPPORTED_GOLD_METADATA_VERSIONS:
        _fail(source, "metadata_version", _unsupported_version_message(version))
    expected = {f.name for f in fields(GoldMetadata)}
    if version == 1:
        expected -= set(V2_ONLY_FIELDS)
    missing = sorted(expected - obj.keys())
    unknown = sorted(obj.keys() - expected)
    if missing:
        raise ValueError(f"{source}: missing key(s) {missing}")
    if unknown:
        raise ValueError(f"{source}: unknown key(s) {unknown}")
    if isinstance(obj["planned_procedure_codes"], list):
        obj["planned_procedure_codes"] = tuple(obj["planned_procedure_codes"])  # any other JSON type is rejected by _validate
    metadata = GoldMetadata(**obj)
    _validate(metadata, source)
    return metadata
