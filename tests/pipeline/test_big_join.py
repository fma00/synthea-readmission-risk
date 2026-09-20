import json
import warnings
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
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
    flag_inpatient_stays,
    join_dimension_attributes,
    join_patient_demographics,
    load_synthea_tables,
)
from readmission_risk.pipeline.gold_metadata import (
    PLANNED_PROCEDURE_CODES,
    read_gold_metadata,
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


PROCEDURES_TEST_SCHEMA = StructType(
    [StructField("ENCOUNTER", StringType(), True), StructField("CODE", StringType(), True)]
)


def _empty_procedures(spark):
    return spark.createDataFrame([], PROCEDURES_TEST_SCHEMA)


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


def test_load_synthea_tables_rejects_empty_procedures(spark, tmp_path):
    """procedures.csv now feeds the planned-stay flag: an empty one would silently revert the label to (nearly) all-cause."""
    csv_dir = tmp_path / "csv"
    _write_all_tables(csv_dir, empty_table="procedures.csv")

    with pytest.raises(ValueError, match="procedures.csv"):
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

    result = build_index_encounters(encounters, patients, _empty_procedures(spark), date(2026, 9, 16), 30).collect()
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

    result = build_index_encounters(encounters, patients, _empty_procedures(spark), date(2026, 9, 16), 30).collect()

    assert len(result) == 1
    assert result[0]["is_readmitted"] == 0


def test_build_index_encounters_death_within_window_no_readmission_dropped(spark):
    encounters = spark.createDataFrame(
        [make_encounter_row("idx1", ts("2020-01-01T00:00:00"), ts("2020-01-03T00:00:00"))],
        ENCOUNTERS_TEST_SCHEMA,
    )
    patients = spark.createDataFrame([("p1", date(2020, 1, 10))], PATIENTS_TEST_SCHEMA)

    result = build_index_encounters(encounters, patients, _empty_procedures(spark), date(2026, 9, 16), 30).collect()

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

    result = build_index_encounters(encounters, patients, _empty_procedures(spark), date(2026, 9, 16), 30).collect()
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
    result = build_index_encounters(encounters, patients, _empty_procedures(spark), date(2026, 9, 14), 30).collect()

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
    result = build_index_encounters(encounters, patients, _empty_procedures(spark), date(2026, 9, 10), 30).collect()
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

    result = build_index_encounters(encounters, patients, _empty_procedures(spark), date(2026, 9, 16), 30).collect()

    assert len(result) == 0


def test_build_index_encounters_null_stop_excluded(spark):
    encounters = spark.createDataFrame(
        [make_encounter_row("idx1", ts("2020-01-01T00:00:00"), None)], ENCOUNTERS_TEST_SCHEMA
    )
    patients = spark.createDataFrame([("p1", None)], PATIENTS_TEST_SCHEMA)

    result = build_index_encounters(encounters, patients, _empty_procedures(spark), date(2026, 9, 16), 30).collect()

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

    result = build_index_encounters(shared_encounters, patients, _empty_procedures(spark), date(2026, 9, 16), 30).collect()
    by_id = {row["encounter_id"]: row for row in result}

    # idx1 (readmitted) and readmit1 (itself a candidate, not further readmitted, not censored --
    # plenty of follow-up exists before reference_date) both survive; amb1 is never a candidate.
    assert len(result) == 2
    assert by_id["idx1"]["is_readmitted"] == 1
    assert by_id["readmit1"]["is_readmitted"] == 0
    assert "amb1" not in by_id


# --- flag_inpatient_stays / label rules (slice 2b) ---
# In the fixtures, times are 2020-...T00:00:00Z unless stated and all encounters are inpatient unless stated.
# Every expected output below was verified in Spark on these exact expressions, and every named mutant was executed and
# confirmed to change it (design session scratch harness).

PLANNED = "703423002"


def _t(month_day: str, clock: str = "00:00:00") -> datetime:
    return ts(f"2020-{month_day}T{clock}")


def _stay(id_, start, stop, patient="p1", encounterclass="inpatient"):
    return make_encounter_row(
        id_,
        _t(start) if isinstance(start, str) else start,
        _t(stop) if isinstance(stop, str) else stop,
        patient=patient,
        encounterclass=encounterclass,
    )


def _procs(spark, pairs):
    return spark.createDataFrame(list(pairs), PROCEDURES_TEST_SCHEMA)


def _flags(spark, stays, procedures=()):
    encounters = spark.createDataFrame(stays, ENCOUNTERS_TEST_SCHEMA)
    rows = flag_inpatient_stays(encounters, _procs(spark, procedures)).collect()
    return {r["Id"]: (r["is_continuation"], r["is_planned"], r["is_terminal"]) for r in rows}


def _labels(spark, stays, procedures=(), deaths=None, reference=date(2026, 9, 16), window=30):
    """{encounter_id: is_readmitted}. `stays` go into ONE createDataFrame (shared lineage for the internal self-joins)."""
    encounters = spark.createDataFrame(stays, ENCOUNTERS_TEST_SCHEMA)
    deaths = deaths or {}
    patients = spark.createDataFrame([(p, deaths.get(p)) for p in sorted({s[3] for s in stays})], PATIENTS_TEST_SCHEMA)
    rows = build_index_encounters(encounters, patients, _procs(spark, procedures), reference, window).collect()
    return {r["encounter_id"]: r["is_readmitted"] for r in rows}


def test_flag_max_over_all_earlier_stays_not_just_the_predecessor(spark):
    flags = _flags(
        spark, [_stay("L", "01-01", "01-30"), _stay("S", "01-02", "01-03"), _stay("T", "01-10", "01-12")]
    )
    assert flags["L"] == (False, False, True)
    assert flags["S"] == (True, False, False)
    assert flags["T"] == (True, False, False)  # a lag(STOP) implementation would give T a False


def test_flag_terminal_and_continuation_boundaries(spark):
    # each pair Ak/Bk is its OWN patient rk: the pairs reuse the same dates, so sharing a patient would change the answers
    stays = [
        _stay("A1", "02-01", "02-05", "r1"),
        _stay("B1", "02-05", "02-08", "r1"),  # START == STOP
        _stay("A2", "02-01", "02-05", "r2"),
        _stay("B2", _t("02-05", "00:00:01"), "02-08", "r2"),  # +1 s
        _stay("A3", "02-01", "02-05", "r3"),
        _stay("B3", "02-03", "02-05", "r3"),  # same STOP
        _stay("A4", "02-01", "02-05", "r4"),
        _stay("B4", "02-03", None, "r4"),  # the other stay is open (null STOP)
        _stay("A5", "02-01", "02-05", "r5"),
        _stay("B5", "01-20", "02-20", "r5"),  # B5 contains A5
    ]
    flags = _flags(spark, stays)
    assert flags["A1"] == (False, False, False)  # non-terminal: B1 starts exactly when A1 ends and runs on
    assert flags["B1"] == (True, False, True)  # the continuation boundary is START <= max STOP
    assert flags["A2"] == (False, False, True)  # +1 s: A2 is terminal
    assert flags["B2"] == (False, False, True)  # ... and B2 is not a continuation
    assert flags["A3"] == (False, False, True)
    assert flags["B3"] == (True, False, True)  # same STOP: both terminal, B3 a continuation
    assert flags["A4"] == (False, False, True)  # a null-STOP stay never makes another stay non-terminal
    assert flags["B4"] == (True, False, True)
    assert flags["A5"] == (True, False, False)
    assert flags["B5"] == (False, False, True)


def test_flag_patient_isolation(spark):
    flags = _flags(spark, [_stay("X", "01-01", "01-10", "P"), _stay("Y", "01-05", "01-08", "Q")])
    assert flags["X"] == (False, False, True)
    assert flags["Y"] == (False, False, True)  # no partitionBy -> Y a continuation; no patient equality -> Y non-terminal


@pytest.mark.parametrize("reverse", [False, True])
def test_flag_tie_follows_id_order_not_row_order(spark, reverse):
    stays = [_stay("a", "03-01", "03-04"), _stay("b", "03-01", "03-05")]
    flags = _flags(spark, stays[::-1] if reverse else stays)
    assert flags["a"][0] is False
    assert flags["b"][0] is True


def test_flag_zero_length_stays(spark):
    flags = _flags(spark, [_stay("a1", "03-01", "03-01"), _stay("b1", "03-01", "03-01")])
    assert flags["a1"] == (False, False, True)
    assert flags["b1"] == (True, False, True)


def test_flag_only_inpatient_stays_take_part(spark):
    stays = [
        _stay("x1", "04-01", "04-03", "w", "emergency"),
        _stay("i1", "04-02", "04-04", "w"),
        _stay("i2", "05-01", "05-03", "w2"),
        _stay("x2", "05-02", "05-10", "w2", "ambulatory"),
    ]
    flags = _flags(spark, stays)
    assert set(flags) == {"i1", "i2"}
    assert flags["i1"] == (False, False, True)  # windowing over all classes would flag i1 a continuation
    assert flags["i2"] == (False, False, True)  # a terminal self-join over all classes would flag i2 non-terminal


def test_flag_planned_by_each_code_and_not_by_others(spark):
    stays = [_stay(f"s{i}", f"0{i + 1}-01", f"0{i + 1}-03") for i in range(4)]
    procedures = [
        ("s0", PLANNED_PROCEDURE_CODES[0]),
        ("s1", PLANNED_PROCEDURE_CODES[1]),
        ("s2", PLANNED_PROCEDURE_CODES[2]),
        ("s3", "123"),
    ]
    flags = _flags(spark, stays, procedures)
    assert [flags[f"s{i}"][1] for i in range(4)] == [True, True, True, False]


def test_flag_planned_has_no_fan_out_and_ignores_null_and_non_inpatient_rows(spark):
    stays = [_stay("d1", "06-01", "06-03"), _stay("amb", "06-10", "06-11", encounterclass="ambulatory")]
    procedures = [
        ("d1", "703423002"),
        ("d1", "703423002"),
        ("d1", "367336001"),  # three listed rows on ONE stay
        (None, "703423002"),
        ("d1", None),
        ("amb", "703423002"),
        ("ghost", "703423002"),
    ]
    encounters = spark.createDataFrame(stays, ENCOUNTERS_TEST_SCHEMA)
    out = flag_inpatient_stays(encounters, _procs(spark, procedures))
    assert out.count() == 1  # without .distinct() the join fans out to 3 rows
    assert out.collect()[0]["is_planned"] is True
    empty = flag_inpatient_stays(encounters, _empty_procedures(spark)).collect()
    assert [r["Id"] for r in empty] == ["d1"]
    assert not empty[0]["is_planned"]


def test_flag_continuation_is_judged_against_an_earlier_planned_stay(spark):
    flags = _flags(spark, [_stay("Pp", "01-01", "01-10"), _stay("Qq", "01-05", "01-06")], [("Pp", PLANNED)])
    assert flags["Pp"] == (False, True, True)
    assert flags["Qq"] == (True, False, False)  # a running max over non-planned stays only would give Qq a False


def test_flag_null_stop_pin_current_behaviour(spark):
    flags = _flags(spark, [_stay("N", "02-01", None), _stay("M", "02-05", "02-06")])
    assert flags["N"] == (False, False, True)
    assert flags["M"] == (False, False, True)  # neither a continuation of, nor made non-terminal by, the open stay N


def test_flag_planned_tail_is_kept_documented_limitation(spark):
    stays = [_stay("b", "05-10", "05-20"), _stay("c", "05-15", "05-25")]
    flags = _flags(spark, stays, [("b", PLANNED)])
    assert flags["b"] == (False, True, False)
    assert flags["c"] == (True, False, True)
    assert _labels(spark, stays, [("b", PLANNED)]) == {"c": 0}  # the tail of a planned episode is still an index row


def test_labels_a_planned_readmit_is_ignored_and_the_index_is_kept(spark):
    labels = _labels(spark, [_stay("idx", "01-01", "01-03"), _stay("plan", "01-10", "01-12")], [("plan", PLANNED)])
    assert labels == {"idx": 0}  # retained (observable, alive) with label 0; the planned stay is never an index row


@pytest.mark.parametrize("code", PLANNED_PROCEDURE_CODES)
def test_labels_each_planned_code_is_ignored(spark, code):
    stays = [_stay("idx", "01-01", "01-03"), _stay("plan", "01-10", "01-12")]
    assert _labels(spark, stays, [("plan", code)]) == {"idx": 0}


def test_labels_a_non_listed_procedure_code_still_counts(spark):
    stays = [_stay("idx", "01-01", "01-03"), _stay("other", "01-10", "01-12")]
    assert _labels(spark, stays, [("other", "123")]) == {"idx": 1, "other": 0}


def test_labels_planned_readmit_plus_a_genuine_later_readmit_is_positive(spark):
    stays = [_stay("idx", "01-01", "01-03"), _stay("plan", "01-10", "01-12"), _stay("gen", "01-20", "01-22")]
    assert _labels(spark, stays, [("plan", PLANNED)]) == {"idx": 1, "gen": 0}


def test_labels_boundary_at_the_earlier_stop(spark):
    # START == earlier STOP: the earlier stay is non-terminal, hence absent; the later stay is present with 0
    assert _labels(spark, [_stay("c", "02-01", "02-05"), _stay("a", "02-05", "02-08")]) == {"a": 0}
    # +1 s: the earlier stay is present and positive. (The strict `a.START > c.STOP` bound is pinned by the zero-length test.)
    stays = [_stay("c", "02-01", "02-05"), _stay("a", _t("02-05", "00:00:01"), "02-08")]
    assert _labels(spark, stays) == {"c": 1, "a": 0}


@pytest.mark.parametrize(
    "window, readmit_start, expected",
    [
        (30, ("02-02", "00:00:00"), 1),  # exactly STOP + 30 d (STOP is 01-03T00:00:00)
        (30, ("02-02", "00:00:01"), 0),  # one second later
        (7, ("01-10", "00:00:00"), 1),
        (7, ("01-10", "00:00:01"), 0),
    ],
)
def test_labels_upper_bound_of_the_window_is_inclusive(spark, window, readmit_start, expected):
    start = _t(*readmit_start)
    stays = [_stay("c", "01-01", "01-03"), _stay("a", start, start + (_t("01-02") - _t("01-01")))]
    assert _labels(spark, stays, window=window)["c"] == expected


def test_labels_reapplied_exclusion_for_a_planned_only_readmit(spark):
    stays = [_stay("idx", "01-01", "01-03"), _stay("plan", "01-10", "01-12")]
    procedures = [("plan", PLANNED)]
    assert _labels(spark, stays, procedures) == {"idx": 0}  # observable -> kept as 0
    # censored: STOP + 30 d >= reference_date + 1 d -> dropped (no longer a positive kept "despite" censoring)
    late = [
        make_encounter_row("idx", ts("2026-08-20T00:00:00"), ts("2026-08-22T00:00:00")),
        make_encounter_row("plan", ts("2026-08-25T00:00:00"), ts("2026-08-27T00:00:00")),
    ]
    assert _labels(spark, late, procedures) == {}
    # death within the window with no unplanned readmission -> dropped
    assert _labels(spark, stays, procedures, deaths={"p1": date(2020, 1, 20)}) == {}


def test_labels_empty_procedures_leaves_only_the_continuation_and_terminal_rules(spark):
    stays = [_stay("idx", "01-01", "01-03"), _stay("later", "01-10", "01-12")]
    assert _labels(spark, stays) == {"idx": 1, "later": 0}


def test_labels_continuation_filter_in_the_readmit_pool(spark):
    # c is terminal; the planned stay b starts after c; a starts INSIDE b (a continuation of a planned stay). Without
    # `~is_continuation` in readmit_pool, c would be labeled 1 through a. Deleting that filter must make this test fail.
    stays = [_stay("c", "01-01", "01-03"), _stay("b", "01-10", "01-20"), _stay("a", "01-15", "01-17")]
    assert _labels(spark, stays, [("b", PLANNED)]) == {"c": 0}


def test_labels_terminal_rule_keeps_only_the_stay_that_ends_the_episode(spark):
    stays = [_stay("L", "01-01", "01-30"), _stay("S", "01-02", "01-03"), _stay("T", "01-10", "01-12")]
    assert _labels(spark, stays) == {"L": 0}


def test_labels_strict_lower_bound_is_pinned_by_zero_length_stays(spark):
    # a `>=` lower bound would make b1 positive through a1 (same instant, smaller Id, hence not a continuation of b1)
    assert _labels(spark, [_stay("a1", "03-01", "03-01"), _stay("b1", "03-01", "03-01")]) == {"a1": 0, "b1": 0}


def test_labels_a_terminal_continuation_stay_is_an_index_row(spark):
    # j starts inside i (a continuation) but ends last: it is the real discharge. Dropping continuations from the index set
    # would give {} (on the real data that would silently remove 423 rows and 39 positives).
    assert _labels(spark, [_stay("i", "03-01", "03-05"), _stay("j", "03-03", "03-09")]) == {"j": 0}


def test_labels_an_open_stay_counts_as_a_readmission(spark):
    # slice 2's behaviour, preserved: restricting the readmit pool to non-null STOP would give c=0
    assert _labels(spark, [_stay("c", "01-01", "01-03"), _stay("n", "01-10", None)]) == {"c": 1}


def test_labels_patient_isolation(spark):
    # another patient's stay inside P's 30-day window must not make c positive
    stays = [_stay("c", "01-01", "01-03", "P"), _stay("q", "01-10", "01-12", "Q")]
    assert _labels(spark, stays) == {"c": 0, "q": 0}


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

    input_dir = tmp_path / "in"
    input_dir.mkdir()
    (input_dir / "generation_summary.json").write_text(json.dumps({"reference_date": "20260101"}))
    output_dir = tmp_path / "out"

    with pytest.raises(ValueError, match="does not match"):
        build_big_join(
            spark, BigJoinConfig(input_dir=input_dir, output_dir=output_dir, reference_date="20260916")
        )


def test_build_big_join_rejects_generation_summary_missing_reference_date_key(spark, tmp_path):

    input_dir = tmp_path / "in"
    input_dir.mkdir()
    (input_dir / "generation_summary.json").write_text(json.dumps({"population_size": 1000}))
    output_dir = tmp_path / "out"

    with pytest.raises(ValueError, match="reference_date"):
        build_big_join(
            spark, BigJoinConfig(input_dir=input_dir, output_dir=output_dir, reference_date="20260916")
        )


def test_build_big_join_end_to_end(spark, tmp_path):
    from pyspark.testing import assertDataFrameEqual

    from readmission_risk.pipeline.big_join import GOLD_TABLE_COLUMNS

    input_dir = tmp_path / "in"
    csv_dir = input_dir / "csv"
    (input_dir).mkdir()
    (input_dir / "generation_summary.json").write_text(json.dumps({"reference_date": "20260916"}))
    _write_all_tables(csv_dir)
    output_dir = tmp_path / "out"

    # FIXTURE_ROWS' one procedure has CODE 123, so no inpatient stay is planned: the zero-match warning must fire
    with pytest.warns(UserWarning, match="PLANNED_PROCEDURE_CODES"):
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

    # the _gold_metadata.json sidecar: exact content, and ignored by BOTH readers (its name starts with "_")
    metadata = read_gold_metadata(output_dir)
    assert metadata.reference_date == "20260916"
    assert (metadata.readmission_window_days, metadata.lookback_years) == (30, 1)
    assert metadata.planned_procedure_codes == PLANNED_PROCEDURE_CODES
    assert (metadata.n_rows, metadata.n_positive) == (1, 0)
    assert (metadata.n_inpatient_stays, metadata.n_planned_stays) == (1, 0)
    assert (metadata.n_continuation_stays, metadata.n_nonterminal_stays) == (0, 0)
    assert (output_dir / "_gold_metadata.json").is_file()
    pandas_frame = pd.read_parquet(output_dir)
    assert list(pandas_frame.columns) == list(GOLD_TABLE_COLUMNS)
    assert len(pandas_frame) == reread.count() == 1
    assert pandas_frame.loc[0, "encounter_id"] == "e1"


# --- slice 2b: planned-stay wiring, metadata, config validation ---

_ENCOUNTER_HEADER = (
    "Id,START,STOP,PATIENT,ORGANIZATION,PROVIDER,PAYER,ENCOUNTERCLASS,CODE,DESCRIPTION,BASE_ENCOUNTER_COST,"
    "TOTAL_CLAIM_COST,PAYER_COVERAGE,REASONCODE,REASONDESCRIPTION"
)
_PROCEDURE_HEADER = "START,STOP,PATIENT,ENCOUNTER,SYSTEM,CODE,DESCRIPTION,BASE_COST,REASONCODE,REASONDESCRIPTION"


def _enc_csv(id_, start, stop, patient, encounterclass="inpatient"):
    stop_text = "" if stop is None else f"2020-{stop}T00:00:00Z"
    return f"{id_},2020-{start}T00:00:00Z,{stop_text},{patient},o1,pr1,pay1,{encounterclass},123,Desc,100.0,200.0,50.0,,"


def _proc_csv(encounter, code, patient="p1"):
    return f"2020-01-01T00:00:00Z,2020-01-01T01:00:00Z,{patient},{encounter},SNOMED-CT,{code},Desc,50.0,,"


def _write_slice_2b_input(tmp_path, encounter_rows, procedure_rows, patient_ids):
    from tests.pipeline.test_schemas import FIXTURE_ROWS

    input_dir = tmp_path / "in"
    csv_dir = input_dir / "csv"
    input_dir.mkdir()
    (input_dir / "generation_summary.json").write_text(json.dumps({"reference_date": "20260916"}))
    _write_all_tables(csv_dir)
    header, p1_row = FIXTURE_ROWS["patients.csv"]
    _write_csv(csv_dir / "patients.csv", header, [p1_row.replace("p1,", f"{pid},", 1) for pid in patient_ids])
    _write_csv(csv_dir / "encounters.csv", _ENCOUNTER_HEADER, encounter_rows)
    _write_csv(csv_dir / "procedures.csv", _PROCEDURE_HEADER, procedure_rows)
    return input_dir


def test_build_big_join_end_to_end_label_wiring(spark, tmp_path):
    """Fails if build_big_join passes the wrong table to build_index_encounters, if the continuation filter is dropped from
    the readmission pool (e7 would be 1), if the terminal filter is dropped (an extra e9 row), or if the metadata's
    continuation / non-terminal aggregates are swapped (p4 makes them 2 vs 1)."""
    encounters = [
        _enc_csv("e1", "01-01", "01-02", "p1"),
        _enc_csv("e2", "01-10", "01-11", "p1"),  # planned
        _enc_csv("e3", "03-01", "03-02", "p1"),  # planned
        _enc_csv("e4", "03-10", "03-11", "p1"),
        _enc_csv("e5", "03-20", "03-21", "p1"),
        _enc_csv("e7", "05-01", "05-03", "p2"),
        _enc_csv("e8", "05-10", "05-20", "p2"),  # planned
        _enc_csv("e9", "05-15", "05-17", "p2"),  # inside the planned e8: a continuation, and non-terminal
        _enc_csv("e10", "06-01", None, "p3"),  # open stay: never an index row
        _enc_csv("e11", "06-01", "06-02", "p3", "ambulatory"),
        _enc_csv("e12", "07-01", "07-05", "p4"),
        _enc_csv("e13", "07-03", "07-05", "p4"),  # same STOP as e12: both terminal, e13 a continuation
    ]
    procedures = [_proc_csv("e2", PLANNED), _proc_csv("e3", PLANNED), _proc_csv("e8", PLANNED, "p2"), _proc_csv("e1", "123")]
    input_dir = _write_slice_2b_input(tmp_path, encounters, procedures, ["p1", "p2", "p3", "p4"])
    output_dir = tmp_path / "out"

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        build_big_join(spark, BigJoinConfig(input_dir=input_dir, output_dir=output_dir, reference_date="20260916"))
    assert not [w for w in caught if "PLANNED_PROCEDURE_CODES" in str(w.message)]  # planned stays exist -> no warning

    frame = pd.read_parquet(output_dir)
    assert sorted(zip(frame["patient_id"], frame["encounter_id"], frame["is_readmitted"], strict=True)) == [
        ("p1", "e1", 0),  # its only in-window stay e2 is planned
        ("p1", "e4", 1),
        ("p1", "e5", 0),
        ("p2", "e7", 0),  # 0 only because the continuation filter removes e9 from the readmission pool
        ("p4", "e12", 0),  # its only in-window stay e13 is a continuation
        ("p4", "e13", 0),
    ]
    metadata = read_gold_metadata(output_dir)
    assert (metadata.n_rows, metadata.n_positive) == (6, 1)
    assert metadata.n_inpatient_stays == 11  # not 12 (ambulatory e11 excluded) and not 10 (open stay e10 included)
    assert metadata.n_planned_stays == 3
    assert (metadata.n_continuation_stays, metadata.n_nonterminal_stays) == (2, 1)  # 2 vs 1 pins the two fields apart


def test_build_big_join_no_inpatient_stays(spark, tmp_path):
    input_dir = _write_slice_2b_input(
        tmp_path, [_enc_csv("a1", "01-01", "01-02", "p1", "ambulatory")], [_proc_csv("a1", "123")], ["p1"]
    )
    output_dir = tmp_path / "out"

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        build_big_join(spark, BigJoinConfig(input_dir=input_dir, output_dir=output_dir, reference_date="20260916"))
    assert not [w for w in caught if "PLANNED_PROCEDURE_CODES" in str(w.message)]  # the n_inpatient_stays > 0 guard

    assert len(pd.read_parquet(output_dir)) == spark.read.parquet(str(output_dir)).count() == 0
    metadata = read_gold_metadata(output_dir)
    assert (metadata.n_rows, metadata.n_positive, metadata.n_inpatient_stays) == (0, 0, 0)
    assert (metadata.n_planned_stays, metadata.n_continuation_stays, metadata.n_nonterminal_stays) == (0, 0, 0)


@pytest.mark.parametrize(
    "overrides",
    [
        {"readmission_window_days": 0},
        {"readmission_window_days": True},
        {"readmission_window_days": 30.0},
        {"lookback_years": 0},
        {"lookback_years": True},
        {"lookback_years": 1.0},
        {"reference_date": "2026-09-16"},
    ],
)
def test_build_big_join_rejects_invalid_config_before_any_io(spark, tmp_path, overrides):
    output_dir = tmp_path / "out"
    config = {"input_dir": tmp_path / "does-not-exist", "output_dir": output_dir, "reference_date": "20260916"}
    config.update(overrides)

    with pytest.raises(ValueError, match=next(iter(overrides))):  # ValueError, not the FileNotFoundError I/O would give
        build_big_join(spark, BigJoinConfig(**config))
    assert not output_dir.exists()
