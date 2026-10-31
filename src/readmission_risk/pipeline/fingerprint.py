"""The `spark-rowhash-v1` gold-table content fingerprint (v2 PRD FR12): ONE Python canonicalizer, run inside Spark's Python
workers by the Big Join (big_join.spark_rowhash_fingerprint, via mapInPandas) and directly by the loader and the scorer's
guard 6. See notes/eg-new-feature/v2-slice5-gs-big-join-fingerprint-pilot-2026-10-12.md (Interfaces section 1).

Why Python and not Spark SQL string formatting: every Spark double -> string conversion goes through Java, whose shortest-digit
output changed in JDK 19, and this project runs Java 17 (CI) and Java 21 (Dataproc runtime 3.0). float.hex is exact everywhere.

Stdlib + pandas only, no pyspark: the scorer imports this module. ANY change to the canonical form below is a new definition
name, never an edit of this one (test_known_answer_vector pins it).
"""

from __future__ import annotations

import datetime as dt
import hashlib

import numpy as np
import pandas as pd

from .gold_metadata import FINGERPRINT_DEFINITION_ROWHASH_V1

_STRING_COLUMNS = (
    "patient_id",
    "encounter_id",
    "admission_reason_code",
    "admission_reason_description",
    "payer_ownership",
    "provider_specialty",
    "gender",
    "race",
    "ethnicity",
    "marital_status",
)
_INT_COLUMNS = (
    "is_readmitted",
    "organization_utilization",
    "prior_encounter_count",
    "prior_inpatient_count",
    "prior_emergency_count",
    "active_condition_count",
    "active_medication_count",
    "procedure_count_window",
    "active_careplan_count",
    "observation_count_window",
)
GOLD_COLUMN_KINDS: dict[str, str] = {
    **dict.fromkeys(_STRING_COLUMNS, "string"),
    **dict.fromkeys(_INT_COLUMNS, "int"),
    "age_at_index_years": "double",
    "index_start": "timestamp",
    "index_stop": "timestamp",
    "label_horizon": "timestamp",
}
# The gold schema's 24 columns, by kind. Kept in sync with big_join.GOLD_TABLE_COLUMNS and models.data.EXPECTED_GOLD_COLUMNS
# by tests/pipeline/test_fingerprint.py (names) and with the Big Join's output schema (types).

_SEPARATOR = "\x1f"
_PERSON = b"rowhash-v1"


def _is_null(value: object) -> bool:
    # `is True`: pd.isna of a non-scalar returns an array, which is not a null cell (it then fails its kind's type check)
    return pd.isna(value) is True


def _token(column: str, kind: str, value: object) -> str:
    if _is_null(value):
        return "N"
    if kind == "string":
        if not isinstance(value, str):
            raise ValueError(f"column {column!r} (string) holds a {type(value).__name__}: {value!r}")
        return f"S{len(value)}:{value}"
    if isinstance(value, (bool, np.bool_)):
        raise ValueError(f"column {column!r} ({kind}) holds a bool: {value!r}")  # noqa: TRY004 -- one exception type for every bad cell
    if kind == "int":
        if isinstance(value, (int, np.integer)):
            return f"I{int(value)}"
        if isinstance(value, (float, np.floating)) and float(value).is_integer():
            return f"I{int(value)}"  # Arrow -> pandas turns a nullable int column with nulls into float64
        raise ValueError(f"column {column!r} (int) holds a non-integer value: {value!r}")
    if kind == "double":
        if not isinstance(value, (int, float, np.integer, np.floating)):
            raise ValueError(f"column {column!r} (double) holds a {type(value).__name__}: {value!r}")
        return f"D{float(value).hex()}"
    # timestamp
    if not isinstance(value, (pd.Timestamp, dt.datetime, np.datetime64)):
        raise ValueError(f"column {column!r} (timestamp) holds a {type(value).__name__}: {value!r}")  # noqa: TRY004
    stamp = pd.Timestamp(value)
    if stamp.nanosecond:
        raise ValueError(f"column {column!r} (timestamp) has sub-microsecond precision: {value!r}")
    stamp = stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")
    return "T" + stamp.strftime("%Y-%m-%dT%H:%M:%S.%fZ")



# Public alias (v3 slice 1): the gold publish's column diagnostic compares cells by their canonical token, so dtype and
# time-zone differences between BigQuery's and pyarrow's frames never count as differences. Not a new canonical form.
canonical_token = _token

def canonical_row_bytes(frame: pd.DataFrame) -> list[bytes]:
    """One UTF-8 byte string per row: `<name>=<token>` cells in column-name order, joined by U+001F. Never mutates `frame`."""
    missing = sorted(set(GOLD_COLUMN_KINDS) - set(frame.columns))
    extra = sorted(set(frame.columns) - set(GOLD_COLUMN_KINDS))
    if missing or extra:
        raise ValueError(f"fingerprint input must have exactly the 24 gold columns: missing {missing}, extra {extra}")
    columns = []
    for name in sorted(GOLD_COLUMN_KINDS):
        kind = GOLD_COLUMN_KINDS[name]
        values = frame[name].astype(object).tolist()
        columns.append([f"{name}={_token(name, kind, v)}" for v in values])
    return [_SEPARATOR.join(cells).encode("utf-8") for cells in zip(*columns, strict=True)]


def partial_sums(frame: pd.DataFrame) -> tuple[int, int, int]:
    """(n_rows, sum of h1, sum of h2) as exact Python ints. h1 = first 8 bytes of sha256, h2 = 8-byte blake2b (two independent
    hash functions). Sums, unlike XOR, change when a row is duplicated, and do not depend on row order."""
    rows = canonical_row_bytes(frame)
    sum_h1 = sum(int.from_bytes(hashlib.sha256(row).digest()[:8], "big") for row in rows)
    sum_h2 = sum(int.from_bytes(hashlib.blake2b(row, digest_size=8, person=_PERSON).digest(), "big") for row in rows)
    return len(rows), sum_h1, sum_h2


def combine(n_rows: int, sum_h1: int, sum_h2: int) -> str:
    return hashlib.sha256(f"{FINGERPRINT_DEFINITION_ROWHASH_V1}\n{n_rows}\n{sum_h1}\n{sum_h2}\n".encode("ascii")).hexdigest()


def rowhash_fingerprint(frame: pd.DataFrame) -> str:
    """The `spark-rowhash-v1` fingerprint of a gold frame (64 lowercase hex); the frame's row and column order are irrelevant."""
    return combine(*partial_sums(frame))
