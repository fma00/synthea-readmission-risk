from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from pyspark.sql.types import (
    DateType,
    IntegerType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

from readmission_risk.pipeline.big_join import (
    BigJoinConfig,
    build_big_join,
    build_index_encounters,
    compute_lookback_features,
    join_dimension_attributes,
    join_patient_demographics,
    load_synthea_tables,
)
from readmission_risk.pipeline.schemas import (
    ORGANIZATIONS_SCHEMA,
    PATIENTS_SCHEMA,
    PAYERS_SCHEMA,
    PROVIDERS_SCHEMA,
    TABLE_SCHEMAS,
)


def ts(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


ENCOUNTERS_TEST_SCHEMA = StructType(
    [
        StructField("Id", StringType(), False),
        StructField("START", TimestampType(), False),
        StructField("STOP", TimestampType(), True),
        StructField("PATIENT", StringType(), False),
        StructField("ORGANIZATION", StringType(), True),
        StructField("PROVIDER", StringType(), True),
        StructField("PAYER", StringType(), True),
        StructField("ENCOUNTERCLASS", StringType(), True),
        StructField("REASONCODE", StringType(), True),
        StructField("REASONDESCRIPTION", StringType(), True),
    ]
)

PATIENTS_TEST_SCHEMA = StructType(
    [
        StructField("Id", StringType(), False),
        StructField("DEATHDATE", DateType(), True),
    ]
)


def make_encounter_row(
    id_,
    start,
    stop,
    patient="p1",
    org="o1",
    provider="pr1",
    payer="pay1",
    encounterclass="inpatient",
    reasoncode=None,
    reasondescription=None,
):
    return (id_, start, stop, patient, org, provider, payer, encounterclass, reasoncode, reasondescription)


# --- load_synthea_tables ---


def _write_csv(path: Path, header: str, rows: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join([header, *rows]) + "\n")


def _write_all_tables(csv_dir: Path, empty_table: str | None = None) -> None:
    from tests.pipeline.test_schemas import FIXTURE_ROWS

    for table_name, lines in FIXTURE_ROWS.items():
        header, *data_rows = lines
        if table_name == empty_table:
            data_rows = []
        _write_csv(csv_dir / table_name, header, data_rows)


def test_load_synthea_tables_missing_table(spark, tmp_path):
    csv_dir = tmp_path / "csv"
    _write_all_tables(csv_dir)
    (csv_dir / "organizations.csv").unlink()

    with pytest.raises(FileNotFoundError, match="organizations.csv"):
        load_synthea_tables(spark, tmp_path)


def test_load_synthea_tables_rejects_empty_dimension_table(spark, tmp_path):
    csv_dir = tmp_path / "csv"
    _write_all_tables(csv_dir, empty_table="payers.csv")

    with pytest.raises(ValueError, match="payers.csv"):
        load_synthea_tables(spark, tmp_path)


def test_load_synthea_tables_rejects_empty_encounters(spark, tmp_path):
    """Regression test for a precommit-review finding: an empty encounters.csv (e.g. via a
    bypassed input_dir that skipped slice 1's own validation) would otherwise flow silently
    through to a successfully-written, empty gold table with exit code 0."""
    csv_dir = tmp_path / "csv"
    _write_all_tables(csv_dir, empty_table="encounters.csv")

    with pytest.raises(ValueError, match="encounters.csv"):
        load_synthea_tables(spark, tmp_path)


def test_load_synthea_tables_reads_all_tables(spark, tmp_path):
    csv_dir = tmp_path / "csv"
    _write_all_tables(csv_dir)

    tables = load_synthea_tables(spark, tmp_path)

    assert set(tables.keys()) == set(TABLE_SCHEMAS.keys())
    assert tables["patients.csv"].count() == 1


# --- build_index_encounters ---


def test_build_index_encounters_readmitted_case(spark):
    encounters = spark.createDataFrame(
        [
            make_encounter_row("idx1", ts("2020-01-01T00:00:00"), ts("2020-01-03T00:00:00")),
            make_encounter_row("readmit1", ts("2020-01-10T00:00:00"), ts("2020-01-12T00:00:00")),
        ],
        ENCOUNTERS_TEST_SCHEMA,
    )
    patients = spark.createDataFrame([("p1", None)], PATIENTS_TEST_SCHEMA)

    result = build_index_encounters(encounters, patients, date(2026, 9, 16), 30).collect()
    by_id = {row["encounter_id"]: row for row in result}

    # readmit1 is itself a legitimate inpatient encounter and becomes its own index row too
    # (is_readmitted=0, since nothing follows it) -- both rows are expected, not just idx1's.
    assert len(result) == 2
    assert by_id["idx1"]["is_readmitted"] == 1
    assert by_id["readmit1"]["is_readmitted"] == 0


def test_build_index_encounters_non_readmitted_case(spark):
    encounters = spark.createDataFrame(
        [make_encounter_row("idx1", ts("2020-01-01T00:00:00"), ts("2020-01-03T00:00:00"))],
        ENCOUNTERS_TEST_SCHEMA,
    )
    patients = spark.createDataFrame([("p1", None)], PATIENTS_TEST_SCHEMA)

    result = build_index_encounters(encounters, patients, date(2026, 9, 16), 30).collect()

    assert len(result) == 1
    assert result[0]["is_readmitted"] == 0


def test_build_index_encounters_death_within_window_no_readmission_dropped(spark):
    encounters = spark.createDataFrame(
        [make_encounter_row("idx1", ts("2020-01-01T00:00:00"), ts("2020-01-03T00:00:00"))],
        ENCOUNTERS_TEST_SCHEMA,
    )
    patients = spark.createDataFrame([("p1", date(2020, 1, 10))], PATIENTS_TEST_SCHEMA)

    result = build_index_encounters(encounters, patients, date(2026, 9, 16), 30).collect()

    assert len(result) == 0


def test_build_index_encounters_death_within_window_with_readmission_kept(spark):
    encounters = spark.createDataFrame(
        [
            make_encounter_row("idx1", ts("2020-01-01T00:00:00"), ts("2020-01-03T00:00:00")),
            make_encounter_row("readmit1", ts("2020-01-05T00:00:00"), ts("2020-01-06T00:00:00")),
        ],
        ENCOUNTERS_TEST_SCHEMA,
    )
    # Patient dies shortly after the readmission -- still within the 30-day window of idx1.
    patients = spark.createDataFrame([("p1", date(2020, 1, 10))], PATIENTS_TEST_SCHEMA)

    result = build_index_encounters(encounters, patients, date(2026, 9, 16), 30).collect()
    by_id = {row["encounter_id"]: row for row in result}

    # idx1 is readmitted (readmit1 follows within 30 days) -> death never drops a positive, kept.
    # readmit1 is itself a candidate too, but has no further readmission and the patient's death
    # falls within ITS OWN 30-day window -> dropped (death-without-readmission exclusion).
    assert len(result) == 1
    assert by_id["idx1"]["is_readmitted"] == 1


def test_build_index_encounters_censored_no_readmission_dropped(spark):
    encounters = spark.createDataFrame(
        [make_encounter_row("idx1", ts("2026-09-10T00:00:00"), ts("2026-09-12T00:00:00"))],
        ENCOUNTERS_TEST_SCHEMA,
    )
    patients = spark.createDataFrame([("p1", None)], PATIENTS_TEST_SCHEMA)

    # reference_date is only 2 days after STOP -- far short of the 30-day follow-up needed.
    result = build_index_encounters(encounters, patients, date(2026, 9, 14), 30).collect()

    assert len(result) == 0


def test_build_index_encounters_censored_with_readmission_before_cutoff_kept(spark):
    encounters = spark.createDataFrame(
        [
            make_encounter_row("idx1", ts("2026-09-01T00:00:00"), ts("2026-09-03T00:00:00")),
            make_encounter_row("readmit1", ts("2026-09-05T00:00:00"), ts("2026-09-06T00:00:00")),
        ],
        ENCOUNTERS_TEST_SCHEMA,
    )
    patients = spark.createDataFrame([("p1", None)], PATIENTS_TEST_SCHEMA)

    # reference_date is well before idx1's 30-day deadline, but the readmission already happened.
    result = build_index_encounters(encounters, patients, date(2026, 9, 10), 30).collect()
    by_id = {row["encounter_id"]: row for row in result}

    # idx1 is readmitted -> kept regardless of censoring. readmit1 is itself a candidate too, but
    # has no further readmission and its own 30-day deadline extends past reference_date ->
    # dropped (censored).
    assert len(result) == 1
    assert by_id["idx1"]["is_readmitted"] == 1


def test_build_index_encounters_non_inpatient_excluded(spark):
    encounters = spark.createDataFrame(
        [
            make_encounter_row(
                "amb1", ts("2020-01-01T00:00:00"), ts("2020-01-01T01:00:00"), encounterclass="ambulatory"
            )
        ],
        ENCOUNTERS_TEST_SCHEMA,
    )
    patients = spark.createDataFrame([("p1", None)], PATIENTS_TEST_SCHEMA)

    result = build_index_encounters(encounters, patients, date(2026, 9, 16), 30).collect()

    assert len(result) == 0


def test_build_index_encounters_null_stop_excluded(spark):
    encounters = spark.createDataFrame(
        [make_encounter_row("idx1", ts("2020-01-01T00:00:00"), None)], ENCOUNTERS_TEST_SCHEMA
    )
    patients = spark.createDataFrame([("p1", None)], PATIENTS_TEST_SCHEMA)

    result = build_index_encounters(encounters, patients, date(2026, 9, 16), 30).collect()

    assert len(result) == 0


def test_build_index_encounters_self_join_shared_lineage(spark):
    """Regression test for the round-5 self-join aliasing fix: candidates and all_inpatient are
    both derived from ONE shared parent DataFrame (via repeated .filter()), not two independently
    constructed fixtures -- independent fixtures would not exercise Spark's self-join
    column-ambiguity risk."""
    shared_encounters = spark.createDataFrame(
        [
            make_encounter_row("idx1", ts("2020-01-01T00:00:00"), ts("2020-01-03T00:00:00")),
            make_encounter_row("readmit1", ts("2020-01-10T00:00:00"), ts("2020-01-12T00:00:00")),
            make_encounter_row(
                "amb1", ts("2020-01-05T00:00:00"), ts("2020-01-05T01:00:00"), encounterclass="ambulatory"
            ),
        ],
        ENCOUNTERS_TEST_SCHEMA,
    )
    patients = spark.createDataFrame([("p1", None)], PATIENTS_TEST_SCHEMA)

    result = build_index_encounters(shared_encounters, patients, date(2026, 9, 16), 30).collect()
    by_id = {row["encounter_id"]: row for row in result}

    # idx1 (readmitted) and readmit1 (itself a candidate, not further readmitted, not censored --
    # plenty of follow-up exists before reference_date) both survive; amb1 is never a candidate.
    assert len(result) == 2
    assert by_id["idx1"]["is_readmitted"] == 1
    assert by_id["readmit1"]["is_readmitted"] == 0
    assert "amb1" not in by_id


# --- compute_lookback_features ---

INDEX_TEST_SCHEMA = StructType(
    [
        StructField("encounter_id", StringType(), False),
        StructField("PATIENT", StringType(), False),
        StructField("index_start", TimestampType(), False),
    ]
)
GENERIC_ENCOUNTER_SCHEMA = StructType(
    [
        StructField("PATIENT", StringType(), False),
        StructField("START", TimestampType(), False),
        StructField("STOP", TimestampType(), True),
        StructField("ENCOUNTERCLASS", StringType(), True),
    ]
)
DATE_EVENT_SCHEMA = StructType(
    [
        StructField("PATIENT", StringType(), False),
        StructField("START", DateType(), False),
        StructField("STOP", DateType(), True),
        StructField("CODE", StringType(), True),
    ]
)
TIMESTAMP_EVENT_WITH_STOP_SCHEMA = StructType(
    [
        StructField("PATIENT", StringType(), False),
        StructField("START", TimestampType(), False),
        StructField("STOP", TimestampType(), True),
        StructField("CODE", StringType(), True),
    ]
)
TIMESTAMP_EVENT_SCHEMA = StructType(
    [StructField("PATIENT", StringType(), False), StructField("START", TimestampType(), False)]
)
OBSERVATION_TEST_SCHEMA = StructType(
    [StructField("PATIENT", StringType(), False), StructField("DATE", TimestampType(), False)]
)


def _empty(schema):
    return lambda spark: spark.createDataFrame([], schema)


def test_compute_lookback_features_leap_year_boundary(spark):
    idx = spark.createDataFrame([("idx1", "p1", ts("2024-02-29T10:00:00"))], INDEX_TEST_SCHEMA)
    all_encounters = spark.createDataFrame([], GENERIC_ENCOUNTER_SCHEMA)
    # Both RESOLVED (STOP set, not None) -- a STOP=None ("still ongoing") condition always counts
    # regardless of window_start (no lower bound by design), so it wouldn't exercise this boundary
    # at all; only a resolved condition's STOP is actually compared against window_start.
    conditions = spark.createDataFrame(
        [
            ("p1", date(2020, 1, 1), date(2023, 2, 28), "resolved_on_window_start"),
            ("p1", date(2020, 1, 1), date(2023, 2, 27), "resolved_one_day_before_window_start"),
        ],
        DATE_EVENT_SCHEMA,
    )
    medications = spark.createDataFrame([], TIMESTAMP_EVENT_WITH_STOP_SCHEMA)
    procedures = spark.createDataFrame([], TIMESTAMP_EVENT_SCHEMA)
    careplans = spark.createDataFrame([], DATE_EVENT_SCHEMA)
    observations = spark.createDataFrame([], OBSERVATION_TEST_SCHEMA)

    result = compute_lookback_features(
        idx, all_encounters, conditions, medications, procedures, careplans, observations, lookback_years=1
    ).collect()

    # window_start = 2023-02-28 (add_months clamps Feb 29 - 12mo to Feb 28, not Mar 1, per the
    # round-2 leap-year fix). A condition resolved exactly ON window_start (inclusive boundary)
    # counts; one resolved one day before window_start does not.
    assert result[0]["active_condition_count"] == 1


def test_compute_lookback_features_window_start_inclusive_boundary(spark):
    idx = spark.createDataFrame([("idx1", "p1", ts("2021-06-15T10:00:00"))], INDEX_TEST_SCHEMA)
    all_encounters = spark.createDataFrame([], GENERIC_ENCOUNTER_SCHEMA)
    # window_start = 2020-06-15. A condition still active (STOP=None) with STOP-equivalent
    # boundary check: use STOP == window_start exactly to test the inclusive ">=" boundary.
    conditions = spark.createDataFrame(
        [
            ("p1", date(2019, 1, 1), date(2020, 6, 15), "in_window"),
            ("p1", date(2019, 1, 1), date(2020, 6, 14), "before_window"),
        ],
        DATE_EVENT_SCHEMA,
    )
    medications = spark.createDataFrame([], TIMESTAMP_EVENT_WITH_STOP_SCHEMA)
    procedures = spark.createDataFrame([], TIMESTAMP_EVENT_SCHEMA)
    careplans = spark.createDataFrame([], DATE_EVENT_SCHEMA)
    observations = spark.createDataFrame([], OBSERVATION_TEST_SCHEMA)

    result = compute_lookback_features(
        idx, all_encounters, conditions, medications, procedures, careplans, observations, lookback_years=1
    ).collect()

    assert result[0]["active_condition_count"] == 1


def test_compute_lookback_features_zero_matches_is_zero_not_null(spark):
    idx = spark.createDataFrame([("idx1", "p1", ts("2021-06-15T10:00:00"))], INDEX_TEST_SCHEMA)
    all_encounters = spark.createDataFrame([], GENERIC_ENCOUNTER_SCHEMA)
    conditions = spark.createDataFrame([], DATE_EVENT_SCHEMA)
    medications = spark.createDataFrame([], TIMESTAMP_EVENT_WITH_STOP_SCHEMA)
    procedures = spark.createDataFrame([], TIMESTAMP_EVENT_SCHEMA)
    careplans = spark.createDataFrame([], DATE_EVENT_SCHEMA)
    observations = spark.createDataFrame([], OBSERVATION_TEST_SCHEMA)

    row = compute_lookback_features(
        idx, all_encounters, conditions, medications, procedures, careplans, observations, lookback_years=1
    ).collect()[0]

    for col in (
        "prior_encounter_count",
        "prior_inpatient_count",
        "prior_emergency_count",
        "active_condition_count",
        "active_medication_count",
        "procedure_count_window",
        "active_careplan_count",
        "observation_count_window",
    ):
        assert row[col] == 0
        assert row[col] is not None


def test_compute_lookback_features_encounter_class_distinction(spark):
    idx = spark.createDataFrame([("idx1", "p1", ts("2021-06-15T10:00:00"))], INDEX_TEST_SCHEMA)
    all_encounters = spark.createDataFrame(
        [
            ("p1", ts("2021-01-01T00:00:00"), ts("2021-01-02T00:00:00"), "inpatient"),
            ("p1", ts("2021-02-01T00:00:00"), ts("2021-02-01T05:00:00"), "emergency"),
            ("p1", ts("2021-03-01T00:00:00"), ts("2021-03-01T01:00:00"), "wellness"),
        ],
        GENERIC_ENCOUNTER_SCHEMA,
    )
    conditions = spark.createDataFrame([], DATE_EVENT_SCHEMA)
    medications = spark.createDataFrame([], TIMESTAMP_EVENT_WITH_STOP_SCHEMA)
    procedures = spark.createDataFrame([], TIMESTAMP_EVENT_SCHEMA)
    careplans = spark.createDataFrame([], DATE_EVENT_SCHEMA)
    observations = spark.createDataFrame([], OBSERVATION_TEST_SCHEMA)

    row = compute_lookback_features(
        idx, all_encounters, conditions, medications, procedures, careplans, observations, lookback_years=1
    ).collect()[0]

    assert row["prior_encounter_count"] == 3
    assert row["prior_inpatient_count"] == 1
    assert row["prior_emergency_count"] == 1


def test_compute_lookback_features_same_day_leakage_fix(spark):
    idx = spark.createDataFrame([("idx1", "p1", ts("2024-06-15T14:00:00"))], INDEX_TEST_SCHEMA)
    all_encounters = spark.createDataFrame([], GENERIC_ENCOUNTER_SCHEMA)
    conditions = spark.createDataFrame(
        [
            ("p1", date(2024, 6, 15), None, "same_day_admitting_diagnosis"),
            ("p1", date(2024, 6, 14), None, "one_day_before"),
        ],
        DATE_EVENT_SCHEMA,
    )
    careplans = spark.createDataFrame(
        [
            ("p1", date(2024, 6, 15), None, "same_day_careplan"),
            ("p1", date(2024, 6, 14), None, "one_day_before_careplan"),
        ],
        DATE_EVENT_SCHEMA,
    )
    medications = spark.createDataFrame([], TIMESTAMP_EVENT_WITH_STOP_SCHEMA)
    procedures = spark.createDataFrame([], TIMESTAMP_EVENT_SCHEMA)
    observations = spark.createDataFrame([], OBSERVATION_TEST_SCHEMA)

    row = compute_lookback_features(
        idx, all_encounters, conditions, medications, procedures, careplans, observations, lookback_years=1
    ).collect()[0]

    # Same-calendar-day records must be EXCLUDED (leakage fix); one-day-earlier records included.
    assert row["active_condition_count"] == 1
    assert row["active_careplan_count"] == 1


# --- join_dimension_attributes / join_patient_demographics (chained, collision test) ---


def _add_stub_feature_columns(df, index_start_value):
    """Adds literal stand-ins for every column join_patient_demographics' final .select()
    requires beyond what join_dimension_attributes itself produces -- used by tests that call
    join_patient_demographics directly against a minimal features fixture."""
    import pyspark.sql.functions as F

    return (
        df.withColumn("index_start", F.lit(index_start_value))
        .withColumn("index_stop", F.lit(index_start_value))
        .withColumn("is_readmitted", F.lit(0))
        .withColumn("REASONCODE", F.lit(None).cast(StringType()))
        .withColumn("REASONDESCRIPTION", F.lit(None).cast(StringType()))
        .withColumn("prior_encounter_count", F.lit(0))
        .withColumn("prior_inpatient_count", F.lit(0))
        .withColumn("prior_emergency_count", F.lit(0))
        .withColumn("active_condition_count", F.lit(0))
        .withColumn("active_medication_count", F.lit(0))
        .withColumn("procedure_count_window", F.lit(0))
        .withColumn("active_careplan_count", F.lit(0))
        .withColumn("observation_count_window", F.lit(0))
    )


def test_join_dimension_attributes_and_demographics_no_gender_collision(spark):
    features = _add_stub_feature_columns(
        spark.createDataFrame(
            [("e1", "p1", "pay1", "pr1", "o1")],
            StructType(
                [
                    StructField("encounter_id", StringType(), False),
                    StructField("PATIENT", StringType(), False),
                    StructField("PAYER", StringType(), True),
                    StructField("PROVIDER", StringType(), True),
                    StructField("ORGANIZATION", StringType(), True),
                ]
            ),
        ),
        ts("2020-01-01T00:00:00"),
    )

    # Full real-shaped fixtures -- providers.GENDER is deliberately "F", patients.GENDER is "M".
    payers = spark.createDataFrame(
        [("pay1", "Medicare", "GOVERNMENT", None, None, None, None, None, 0.0, 0.0, 0.0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0.0, 0)],
        PAYERS_SCHEMA,
    )
    providers = spark.createDataFrame(
        [("pr1", "o1", "Doc", "F", "GENERAL PRACTICE", None, None, None, None, None, None, 0, 0)],
        PROVIDERS_SCHEMA,
    )
    organizations = spark.createDataFrame(
        [("o1", "Clinic", None, None, None, None, None, None, None, 0.0, 5)], ORGANIZATIONS_SCHEMA
    )
    patients = spark.createDataFrame(
        [{"Id": "p1", "BIRTHDATE": date(1980, 1, 1), "GENDER": "M", "RACE": "white", "ETHNICITY": "nonhispanic", "MARITAL": "M"}],
        PATIENTS_SCHEMA,
    )

    with_dims = join_dimension_attributes(features, payers, providers, organizations)
    gold = join_patient_demographics(with_dims, patients)  # must not raise AnalysisException
    row = gold.collect()[0]

    assert row["gender"] == "M"  # patients.GENDER wins; providers.GENDER never leaked through
    assert row["payer_ownership"] == "GOVERNMENT"
    assert row["provider_specialty"] == "GENERAL PRACTICE"
    assert row["organization_utilization"] == 5
    # join_dimension_attributes' output still carries encounter_id/PATIENT/index_start alongside
    # the resolved attributes (it's join_patient_demographics that produces the final,
    # GOLD_TABLE_COLUMNS-shaped output -- asserted precisely in the end-to-end test below).
    assert "GENDER" not in gold.columns


def test_join_patient_demographics_age_calculation(spark):
    import pyspark.sql.functions as F

    features = (
        _add_stub_feature_columns(
            spark.createDataFrame(
                [("e1", "p1")],
                StructType(
                    [
                        StructField("encounter_id", StringType(), False),
                        StructField("PATIENT", StringType(), False),
                    ]
                ),
            ),
            ts("2020-01-01T00:00:00"),
        )
        # payer_ownership/provider_specialty/organization_utilization are normally added by
        # join_dimension_attributes -- stubbed here since this test calls
        # join_patient_demographics directly.
        .withColumn("payer_ownership", F.lit(None).cast(StringType()))
        .withColumn("provider_specialty", F.lit(None).cast(StringType()))
        .withColumn("organization_utilization", F.lit(None).cast(IntegerType()))
    )
    patients = spark.createDataFrame(
        [{"Id": "p1", "BIRTHDATE": date(1990, 1, 1), "GENDER": "M", "RACE": "white", "ETHNICITY": "nonhispanic", "MARITAL": "M"}],
        PATIENTS_SCHEMA,
    )

    row = join_patient_demographics(features, patients).collect()[0]

    expected_age = (date(2020, 1, 1) - date(1990, 1, 1)).days / 365.25
    assert abs(row["age_at_index_years"] - expected_age) < 0.01


# --- build_big_join ---


def test_build_big_join_rejects_existing_output_dir(spark, tmp_path):
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    input_dir = tmp_path / "in"

    with pytest.raises(FileExistsError):
        build_big_join(
            spark, BigJoinConfig(input_dir=input_dir, output_dir=output_dir, reference_date="20260916")
        )


def test_build_big_join_rejects_missing_generation_summary(spark, tmp_path):
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    output_dir = tmp_path / "out"

    with pytest.raises(FileNotFoundError, match="generation_summary.json"):
        build_big_join(
            spark, BigJoinConfig(input_dir=input_dir, output_dir=output_dir, reference_date="20260916")
        )


def test_build_big_join_rejects_reference_date_mismatch(spark, tmp_path):
    import json

    input_dir = tmp_path / "in"
    input_dir.mkdir()
    (input_dir / "generation_summary.json").write_text(json.dumps({"reference_date": "20260101"}))
    output_dir = tmp_path / "out"

    with pytest.raises(ValueError, match="does not match"):
        build_big_join(
            spark, BigJoinConfig(input_dir=input_dir, output_dir=output_dir, reference_date="20260916")
        )


def test_build_big_join_rejects_generation_summary_missing_reference_date_key(spark, tmp_path):
    import json

    input_dir = tmp_path / "in"
    input_dir.mkdir()
    (input_dir / "generation_summary.json").write_text(json.dumps({"population_size": 1000}))
    output_dir = tmp_path / "out"

    with pytest.raises(ValueError, match="reference_date"):
        build_big_join(
            spark, BigJoinConfig(input_dir=input_dir, output_dir=output_dir, reference_date="20260916")
        )


def test_build_big_join_end_to_end(spark, tmp_path):
    import json

    from pyspark.testing import assertDataFrameEqual

    from readmission_risk.pipeline.big_join import GOLD_TABLE_COLUMNS

    input_dir = tmp_path / "in"
    csv_dir = input_dir / "csv"
    (input_dir).mkdir()
    (input_dir / "generation_summary.json").write_text(json.dumps({"reference_date": "20260916"}))
    _write_all_tables(csv_dir)
    output_dir = tmp_path / "out"

    gold = build_big_join(
        spark,
        BigJoinConfig(
            input_dir=input_dir, output_dir=output_dir, reference_date="20260916", lookback_years=1
        ),
    )

    assert list(gold.columns) == list(GOLD_TABLE_COLUMNS)
    assert output_dir.exists()

    # FIXTURE_ROWS' single patient/encounter has every condition/medication/procedure/careplan/
    # observation dated the SAME day/instant as the index encounter itself (all tied to the one
    # fixture encounter e1) -- none of them are genuinely prior history, so all 8 lookback
    # features are correctly 0 (this also exercises the same-day leakage exclusion for free: if
    # that fix regressed, active_condition_count/active_careplan_count would come back 1, not 0).
    expected_age = (date(2020, 1, 1) - date(1980, 1, 1)).days / 365.25
    # Built against gold's own real schema rather than a hand-typed one, so the comparison checks
    # values (and their types), not a second, independently-typo-prone schema.
    expected = spark.createDataFrame(
        [
            (
                "p1", "e1", ts("2020-01-01T00:00:00"), ts("2020-01-02T00:00:00"), 0,
                None, None, "GOVERNMENT", "GENERAL PRACTICE", 5,
                0, 0, 0, 0, 0, 0, 0, 0,
                expected_age, "M", "white", "nonhispanic", "M",
            )
        ],
        schema=gold.schema,
    )
    assertDataFrameEqual(gold, expected)

    reread = spark.read.parquet(str(output_dir))
    assertDataFrameEqual(reread, expected)
