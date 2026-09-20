"""Loading, validation and feature preparation for the model-training slice (slice 3).
See notes/eg-new-feature/model-training-2026-09-19.md for the design this implements.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd

from readmission_risk.pipeline.gold_metadata import (
    GOLD_METADATA_FILENAME,
    LABEL_DEFINITION,
    PLANNED_PROCEDURE_CODES,
    GoldMetadata,
    read_gold_metadata,
)

LABEL_COLUMN = "is_readmitted"
GROUP_COLUMN = "patient_id"

EXPECTED_GOLD_COLUMNS: tuple[str, ...] = (
    "patient_id",
    "encounter_id",
    "index_start",
    "index_stop",
    "is_readmitted",
    "admission_reason_code",
    "admission_reason_description",
    "payer_ownership",
    "provider_specialty",
    "organization_utilization",
    "prior_encounter_count",
    "prior_inpatient_count",
    "prior_emergency_count",
    "active_condition_count",
    "active_medication_count",
    "procedure_count_window",
    "active_careplan_count",
    "observation_count_window",
    "age_at_index_years",
    "gender",
    "race",
    "ethnicity",
    "marital_status",
)
# The gold schema (23 columns), restated here rather than imported from
# readmission_risk.pipeline.big_join.GOLD_TABLE_COLUMNS: big_join imports pyspark at module level,
# and readmission_risk.models will be imported by slice 4's scorer and possibly the dashboard,
# neither of which should need Spark. tests/models/test_data.py asserts the two stay in sync.
# (readmission_risk.pipeline.gold_metadata, imported above, is stdlib-only for the same reason; a test asserts that
# importing this module never imports pyspark.)

COUNT_FEATURES: tuple[str, ...] = (
    "prior_encounter_count",
    "prior_inpatient_count",
    "prior_emergency_count",
    "active_condition_count",
    "active_medication_count",
    "procedure_count_window",
    "active_careplan_count",
    "observation_count_window",
    "length_of_stay_days",  # derived by prepare_features; NOT a gold column
)
NUMERIC_OTHER_FEATURES: tuple[str, ...] = ("age_at_index_years",)
CATEGORICAL_FEATURES: tuple[str, ...] = (
    "admission_reason_code",
    "payer_ownership",
    "gender",
    "race",
    "ethnicity",
    "marital_status",
)
FEATURE_COLUMNS: tuple[str, ...] = COUNT_FEATURES + NUMERIC_OTHER_FEATURES + CATEGORICAL_FEATURES

EXCLUDED_COLUMN_REASONS: dict[str, str] = {
    "patient_id": "identifier; used only as the split/CV grouping key",
    "encounter_id": "identifier",
    "index_start": "split key; raw calendar time cannot extrapolate across a chronological split",
    "index_stop": "split key / censoring-buffer key; only length_of_stay_days derived from it is a feature",
    "admission_reason_description": "redundant with admission_reason_code",
    "provider_specialty": "constant (GENERAL PRACTICE on every row) -- zero information",
    "organization_utilization": (
        "whole-simulation running total per organization: embeds post-index information "
        "(not point-in-time); user decision 2026-09-19"
    ),
}
# Invariant (enforced by tests/models/test_data.py::test_every_gold_column_is_classified_exactly_once):
# set(EXPECTED_GOLD_COLUMNS) == {LABEL_COLUMN} | set(EXCLUDED_COLUMN_REASONS)
#                               | (set(FEATURE_COLUMNS) - {"length_of_stay_days"})
# with the three groups pairwise disjoint. A future slice-2 column therefore cannot silently
# become a feature OR silently vanish -- the test fails until it is classified.

_GOLD_COUNT_COLUMNS: tuple[str, ...] = tuple(c for c in COUNT_FEATURES if c != "length_of_stay_days")
_REQUIRED_NONNULL_COLUMNS: tuple[str, ...] = (
    "patient_id",
    "encounter_id",
    "index_start",
    "index_stop",
    "is_readmitted",
    *_GOLD_COUNT_COLUMNS,
    "age_at_index_years",
)
_STRING_COLUMNS: tuple[str, ...] = (
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
_MISSING_CATEGORY = "MISSING"


def load_gold_table(gold_dir: Path) -> pd.DataFrame:
    """The gold table only; see load_gold_table_with_metadata (which this wraps) for every check it performs."""
    return load_gold_table_with_metadata(gold_dir)[0]


def load_gold_table_with_metadata(gold_dir: Path) -> tuple[pd.DataFrame, GoldMetadata]:
    """Reads the Spark-written Parquet directory (part files + _SUCCESS) via pd.read_parquet, validates it AND its
    `_gold_metadata.json` sidecar (required: a table without one is refused), and normalizes it to the post-load dtype
    contract: strings -> object, the 8 count columns -> int64, age -> float64, label -> int8, timestamps -> tz-aware UTC
    (organization_utilization is left exactly as read: it is excluded from the features, so it is neither cast nor
    null-checked). Returns (frame sorted by (index_start, encounter_id) with a fresh RangeIndex, metadata). Raises
    FileNotFoundError if gold_dir holds no Parquet file or the metadata file is absent, ValueError (naming the offending
    column/count/file) on any schema, data or metadata problem, including a label_definition / planned_procedure_codes that
    differ from this code's constants and row/positive counts that differ from the metadata's (an incomplete copy, or not the
    table the metadata describes -- edits that preserve both counts are NOT detected)."""
    gold_dir = Path(gold_dir)
    if not gold_dir.is_dir() or not any(gold_dir.glob("*.parquet")):
        raise FileNotFoundError(f"No Parquet files found in gold directory: {gold_dir}")

    metadata = read_gold_metadata(gold_dir)
    if metadata.label_definition != LABEL_DEFINITION or metadata.planned_procedure_codes != PLANNED_PROCEDURE_CODES:
        raise ValueError(
            f"Gold table in {gold_dir} was built with label definition {metadata.label_definition!r} / planned procedure "
            f"codes {metadata.planned_procedure_codes}, but this code trains on {LABEL_DEFINITION!r} / "
            f"{PLANNED_PROCEDURE_CODES}; rebuild it with scripts/run_big_join.py"
        )

    df = pd.read_parquet(gold_dir)

    missing = [c for c in EXPECTED_GOLD_COLUMNS if c not in df.columns]
    unexpected = [c for c in df.columns if c not in EXPECTED_GOLD_COLUMNS]
    if missing or unexpected:
        raise ValueError(
            f"Gold table schema mismatch in {gold_dir}: missing columns {missing}, "
            f"unexpected columns {unexpected} (a slice-2 schema change must be classified in "
            "readmission_risk.models.data before it can be used)"
        )
    if len(df) == 0:
        raise ValueError(f"Gold table in {gold_dir} has zero rows")

    for col in _REQUIRED_NONNULL_COLUMNS:
        n_null = int(df[col].isna().sum())
        if n_null:
            raise ValueError(f"Gold table column {col!r} has {n_null} null value(s)")

    bad_label = ~df[LABEL_COLUMN].isin([0, 1])
    if bad_label.any():
        raise ValueError(
            f"Gold table column {LABEL_COLUMN!r} must be 0/1; found values "
            f"{sorted(df.loc[bad_label, LABEL_COLUMN].unique().tolist())}"
        )
    n_positive = int(df[LABEL_COLUMN].sum())
    if len(df) != metadata.n_rows or n_positive != metadata.n_positive:
        raise ValueError(
            f"Gold table does not match its {GOLD_METADATA_FILENAME}: {len(df)} rows / {n_positive} positives vs "
            f"{metadata.n_rows} / {metadata.n_positive} recorded -- an incomplete copy, or not the table this metadata describes"
        )

    # .copy() so nothing below mutates a view of what pd.read_parquet returned
    df = df.copy()
    df[LABEL_COLUMN] = df[LABEL_COLUMN].astype("int8")
    for col in _GOLD_COUNT_COLUMNS:
        df[col] = df[col].astype("int64")
    df["age_at_index_years"] = df["age_at_index_years"].astype("float64")
    for col in _STRING_COLUMNS:
        df[col] = df[col].astype(object)
    for col in ("index_start", "index_stop"):
        df[col] = pd.to_datetime(df[col], utc=True)

    n_dup = int(df["encounter_id"].duplicated().sum())
    if n_dup:
        raise ValueError(f"Gold table has {n_dup} duplicated encounter_id value(s)")
    n_negative_stay = int((df["index_stop"] < df["index_start"]).sum())
    if n_negative_stay:
        raise ValueError(f"Gold table has {n_negative_stay} row(s) with index_stop before index_start")

    df = df.sort_values(["index_start", "encounter_id"], kind="stable").reset_index(drop=True)
    return df[list(EXPECTED_GOLD_COLUMNS)], metadata


def gold_fingerprint(df: pd.DataFrame) -> str:
    """Pure. `df` = the frame returned by load_gold_table. sha256 over ALL 23 columns of the frame
    sorted by encounter_id, rendered through an explicit CSV recipe. This is the only implementation
    of the recipe: train_and_evaluate's `gold_fingerprint` tag and the tests both call it.
    (lineterminator is explicit because pandas otherwise uses os.linesep. A null and an empty string
    render identically in CSV and so hash identically -- harmless for Spark output.)"""
    csv_text = df.sort_values("encounter_id")[list(EXPECTED_GOLD_COLUMNS)].to_csv(
        index=False,
        lineterminator="\n",
        date_format="%Y-%m-%dT%H:%M:%S.%f",
        float_format="%.17g",
    )
    return hashlib.sha256(csv_text.encode("utf-8")).hexdigest()


def prepare_features(df: pd.DataFrame) -> pd.DataFrame:
    """Pure; does not mutate df. Returns a NEW frame (same index as df) with exactly FEATURE_COLUMNS,
    in that order: length_of_stay_days derived from index_start/index_stop, count/age columns as
    float64, and each categorical with nulls filled by the literal "MISSING" and cast to object dtype
    (explicit, so the input contract is identical on pandas 2.3.3 and a future pandas 3). A constant
    fill is not a fitted statistic, so doing it here (before any train/test split) cannot leak."""
    needed = [*_GOLD_COUNT_COLUMNS, *NUMERIC_OTHER_FEATURES, *CATEGORICAL_FEATURES, "index_start", "index_stop"]
    missing = [c for c in needed if c not in df.columns]
    if missing:
        raise ValueError(f"prepare_features input is missing column(s): {missing}")

    columns: dict[str, pd.Series] = {}
    for col in COUNT_FEATURES:
        if col == "length_of_stay_days":
            columns[col] = ((df["index_stop"] - df["index_start"]) / pd.Timedelta(days=1)).astype("float64")
        else:
            columns[col] = df[col].astype("float64")
    for col in NUMERIC_OTHER_FEATURES:
        columns[col] = df[col].astype("float64")
    for col in CATEGORICAL_FEATURES:
        columns[col] = df[col].fillna(_MISSING_CATEGORY).astype(object)
    return pd.DataFrame(columns, index=df.index)[list(FEATURE_COLUMNS)]
