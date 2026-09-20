"""The `_gold_metadata.json` sidecar written next to the gold Parquet by the Big Join and required by model training.
See notes/eg-new-feature/readmission-label-planned-exclusion-2026-09-20.md for the design this implements.

Stdlib only, on purpose: readmission_risk.models imports this module, and the models package must never need Spark.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from datetime import date
from itertools import pairwise
from pathlib import Path

GOLD_METADATA_FILENAME = "_gold_metadata.json"
GOLD_METADATA_VERSION = 1
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
    if metadata.metadata_version != GOLD_METADATA_VERSION:
        _fail(source, "metadata_version", f"unsupported version {metadata.metadata_version} (this code reads {GOLD_METADATA_VERSION})")
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


def write_gold_metadata(gold_dir: Path, metadata: GoldMetadata) -> Path:
    """Validates, then writes `gold_dir/_gold_metadata.json` atomically (temp file + os.replace). Does not sort or repair
    anything: an invalid object is never written."""
    _validate(metadata, "<in-memory>")
    gold_dir = Path(gold_dir)
    if not gold_dir.is_dir():
        raise FileNotFoundError(f"gold_dir is not an existing directory: {gold_dir}")
    final = gold_dir / GOLD_METADATA_FILENAME
    tmp = gold_dir / (GOLD_METADATA_FILENAME + ".tmp")
    tmp.write_text(json.dumps(asdict(metadata), indent=2, sort_keys=True) + "\n", encoding="utf-8")
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

    def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        seen: dict[str, object] = {}
        for key, value in pairs:
            if key in seen:
                raise ValueError(f"{path}: duplicate key {key!r}")
            seen[key] = value
        return seen

    try:
        obj = json.loads(text, object_pairs_hook=_no_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(obj, dict):
        # ValueError, not TypeError: the design specifies one exception type for every bad-file case
        raise ValueError(f"{path}: top level must be a JSON object; got {type(obj).__name__}")  # noqa: TRY004
    expected = {f.name for f in fields(GoldMetadata)}
    missing = sorted(expected - obj.keys())
    unknown = sorted(obj.keys() - expected)
    if missing:
        raise ValueError(f"{path}: missing key(s) {missing}")
    if unknown:
        raise ValueError(f"{path}: unknown key(s) {unknown}")
    if isinstance(obj["planned_procedure_codes"], list):
        obj["planned_procedure_codes"] = tuple(obj["planned_procedure_codes"])  # any other JSON type is rejected by _validate
    metadata = GoldMetadata(**obj)
    _validate(metadata, str(path))
    return metadata
