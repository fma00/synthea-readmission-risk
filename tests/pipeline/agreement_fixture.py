"""The FR12 agreement fixture (v2 slice 5): a 6-row frame with the gold table's exact Spark schema and the value shapes where the
Spark (Arrow batch) and Python (loader) paths could disagree: a null in the nullable int column organization_utilization
(becomes float64 NaN in pandas), null strings, non-integer doubles, a non-ASCII string, and timestamps with microseconds.
Imported by tests/pipeline/test_big_join.py and run as a script by tests/pipeline/tz_agreement_probe.py; no test functions.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql.types import (
    DoubleType,
    IntegerType,
    LongType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

COUNT_COLUMNS = (
    "prior_encounter_count",
    "prior_inpatient_count",
    "prior_emergency_count",
    "active_condition_count",
    "active_medication_count",
    "procedure_count_window",
    "active_careplan_count",
    "observation_count_window",
)
GOLD_SPARK_SCHEMA = StructType(
    [
        StructField("patient_id", StringType()),
        StructField("encounter_id", StringType()),
        StructField("index_start", TimestampType()),
        StructField("index_stop", TimestampType()),
        StructField("is_readmitted", IntegerType()),
        StructField("label_horizon", TimestampType()),
        StructField("admission_reason_code", StringType()),
        StructField("admission_reason_description", StringType()),
        StructField("payer_ownership", StringType()),
        StructField("provider_specialty", StringType()),
        StructField("organization_utilization", IntegerType()),
        *[StructField(name, LongType()) for name in COUNT_COLUMNS],
        StructField("age_at_index_years", DoubleType()),
        StructField("gender", StringType()),
        StructField("race", StringType()),
        StructField("ethnicity", StringType()),
        StructField("marital_status", StringType()),
    ]
)


def _ts(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def agreement_rows() -> list[tuple]:
    rows = []
    for i in range(6):
        rows.append(
            (
                f"p{i % 4}",
                f"e{i}",
                _ts(f"2023-0{1 + i}-1{i}T0{i}:15:30.{123456 + i}"),
                _ts(f"2023-0{1 + i}-2{i}T1{i}:45:00.000001"),
                i % 2,
                _ts(f"2023-0{2 + i}-2{i}T1{i}:45:00"),
                None if i in (0, 3) else str(400000 + i),
                None if i in (0, 3) else f"Réason {i} ✓",
                None if i == 2 else ("MEDICARE" if i % 2 else "PRIVATE"),
                "GENERAL PRACTICE",
                None if i in (1, 4) else 1000 + i,
                *[(i * 7 + k) % 5 for k in range(len(COUNT_COLUMNS))],
                41.23477070499658 + i / 3,
                "F" if i % 2 else "M",
                "white",
                "nonhispanic",
                None if i == 5 else "M",
            )
        )
    return rows


def build_agreement_frame(spark: SparkSession) -> DataFrame:
    return spark.createDataFrame(agreement_rows(), schema=GOLD_SPARK_SCHEMA)
