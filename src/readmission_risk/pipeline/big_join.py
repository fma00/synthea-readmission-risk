"""PySpark Big Join: joins Synthea CSV output into a patient-encounter-level gold feature table.
See notes/eg-new-feature/pyspark-big-join-2026-09-19.md for the design this implements.
"""

from __future__ import annotations

import json
import warnings
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pyspark.sql.functions as F
from pyspark.sql import DataFrame, SparkSession, Window

from .gold_metadata import (
    LABEL_DEFINITION,
    PLANNED_PROCEDURE_CODES,
    GoldMetadata,
    parse_yyyymmdd,
    write_gold_metadata,
)
from .schemas import TABLE_SCHEMAS

TABLES_JOINED: tuple[str, ...] = (
    "patients.csv",
    "encounters.csv",
    "conditions.csv",
    "medications.csv",
    "procedures.csv",
    "careplans.csv",
    "observations.csv",
    "payers.csv",
    "providers.csv",
    "organizations.csv",
)
# Deliberately excludes claims.csv/claims_transactions.csv (billing, not clinical -- CLAUDE.md)
# and immunizations/allergies/devices/imaging_studies/payer_transitions/supplies (excluded by
# explicit decision -- see the design doc's Scope > Out). This tuple IS the single source of
# truth for "which tables does the join read" -- load_synthea_tables iterates exactly this list,
# looking up TABLE_SCHEMAS[name] for each entry.

TABLES_MUST_BE_NONEMPTY: tuple[str, ...] = (
    "patients.csv",
    "encounters.csv",
    "procedures.csv",
    "payers.csv",
    "providers.csv",
    "organizations.csv",
)
# procedures.csv (slice 2b): the planned-stay flag (PLANNED_PROCEDURE_CODES) is read from it, so an empty one would make every
# stay unplanned and silently revert the label to (nearly) all-cause, with no error anywhere. Slice 1's own
# REQUIRED_NONEMPTY_TABLES deliberately still allows an empty procedures.csv (named follow-up).
# payers.csv/providers.csv/organizations.csv feed join_dimension_attributes as unconditional
# left-join sources with no fallback -- an empty one would silently produce all-null
# payer_ownership/provider_specialty/organization_utilization columns rather than erroring
# anywhere. patients.csv/encounters.csv are just as reachable via a bypassed input_dir (a stale
# directory, hand-copied data) and an empty encounters.csv is arguably worse: build_index_encounters
# on zero rows produces a zero-row DataFrame that flows silently through every downstream join to
# a successfully-written, silently-empty gold table with exit code 0 -- a bad failure mode for a
# table destined for BigQuery and a public dashboard. This is an independent, in-function guard
# (not relying solely on slice 1's own REQUIRED_NONEMPTY_TABLES check, which only fires if
# input_dir was produced by this session's own run_synthea_generation call). This is distinct from
# (and does not contradict) build_big_join's own documented tolerance for a LEGITIMATELY empty
# gold table (e.g. a tiny population where every index encounter is excluded by death/censoring,
# not a bypassed-validation empty raw input) -- see the design doc's Failure modes section.

GOLD_TABLE_COLUMNS: tuple[str, ...] = (
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


@dataclass(frozen=True)
class BigJoinConfig:
    input_dir: Path
    output_dir: Path
    reference_date: str
    lookback_years: int = 1
    readmission_window_days: int = 30


def _count_data_rows(csv_path: Path) -> int:
    with csv_path.open("r", encoding="utf-8", errors="replace") as f:
        return max(sum(1 for _ in f) - 1, 0)


def load_synthea_tables(spark: SparkSession, input_dir: Path) -> dict[str, DataFrame]:
    """Reads each table in TABLES_JOINED from input_dir/csv/<table> with its hand-declared
    schema. Raises FileNotFoundError for the first missing table, or ValueError for a table that's
    present but empty (see TABLES_MUST_BE_NONEMPTY), before any Spark read of that file."""
    tables: dict[str, DataFrame] = {}
    csv_dir = input_dir / "csv"
    for name in TABLES_JOINED:
        path = csv_dir / name
        if not path.is_file():
            raise FileNotFoundError(f"Required table not found: {path}")
        if name in TABLES_MUST_BE_NONEMPTY and _count_data_rows(path) == 0:
            raise ValueError(f"Required table is present but empty: {path}")
        tables[name] = spark.read.schema(TABLE_SCHEMAS[name]).option("header", True).csv(str(path))
    return tables


def flag_inpatient_stays(encounters: DataFrame, procedures: DataFrame) -> DataFrame:
    """All inpatient stays with four added columns: prev_max_stop (timestamp, nullable), is_continuation, is_planned and
    is_terminal (all boolean, non-null). Extra input columns are preserved; one row per inpatient stay (no fan-out).

    - is_planned: the stay's Id is the ENCOUNTER of a procedures row whose CODE is in PLANNED_PROCEDURE_CODES.
    - is_continuation: ordering the patient's inpatient stays by (START, Id), the maximum STOP over ALL strictly earlier stays
      is non-null and START <= it (so a stay starting exactly when an earlier one ends IS a continuation). Computed over every
      inpatient stay, planned ones included. A null STOP contributes nothing to the maximum.
    - is_terminal: NO other stay of the same patient has START <= this STOP and STOP > this STOP (both comparisons are
      null-false, so an open stay never makes another stay non-terminal). Also computed over every inpatient stay.
    Non-inpatient encounters take no part in any of the three."""
    inpatient = encounters.filter(F.col("ENCOUNTERCLASS") == "inpatient")
    w = Window.partitionBy("PATIENT").orderBy("START", "Id").rowsBetween(Window.unboundedPreceding, -1)
    planned_ids = (
        procedures.filter(F.col("CODE").isin(*PLANNED_PROCEDURE_CODES))
        .select(F.col("ENCOUNTER").alias("_planned_encounter_id"))
        .distinct()  # required: a stay has many planned procedure rows (19,666 rows / 3,320 stays on the real data)
        .withColumn("_planned_marker", F.lit(True))
    )
    x, y = inpatient.alias("x"), inpatient.alias("y")
    nonterminal_ids = (
        x.join(
            y,
            (F.col("x.PATIENT") == F.col("y.PATIENT"))
            & (F.col("x.Id") != F.col("y.Id"))
            & (F.col("y.START") <= F.col("x.STOP"))
            & (F.col("y.STOP") > F.col("x.STOP")),
            "left_semi",
        )
        .select(F.col("x.Id").alias("_nonterminal_id"))
        .distinct()
        .withColumn("_nonterminal_marker", F.lit(True))
    )
    return (
        inpatient.withColumn("prev_max_stop", F.max("STOP").over(w))
        .withColumn(
            "is_continuation", F.col("prev_max_stop").isNotNull() & (F.col("START") <= F.col("prev_max_stop"))
        )
        .join(planned_ids, F.col("Id") == F.col("_planned_encounter_id"), "left")
        .withColumn("is_planned", F.coalesce(F.col("_planned_marker"), F.lit(False)))
        .join(nonterminal_ids, F.col("Id") == F.col("_nonterminal_id"), "left")
        .withColumn("is_terminal", ~F.coalesce(F.col("_nonterminal_marker"), F.lit(False)))
        .drop("_planned_encounter_id", "_planned_marker", "_nonterminal_id", "_nonterminal_marker")
    )


def build_index_encounters(
    encounters: DataFrame,
    patients: DataFrame,
    procedures: DataFrame,
    reference_date: date,
    readmission_window_days: int = 30,
) -> DataFrame:
    """Filters encounters to index candidates (inpatient, non-null STOP, not planned, terminal), labels each with a 30-day
    (default) UNPLANNED readmission flag (a readmission is an inpatient stay that is neither planned nor a continuation,
    starting in (STOP, STOP + W], upper bound inclusive), and drops candidates whose outcome can't be observed (death or
    administrative censoring before a readmission occurred). See flag_inpatient_stays for the three stay flags and
    LABEL_DEFINITION (gold_metadata.py) for the identity rule."""
    flagged = flag_inpatient_stays(encounters, procedures)
    candidates = flagged.filter(
        F.col("STOP").isNotNull() & ~F.col("is_planned") & F.col("is_terminal")
    ).withColumn("readmit_deadline", F.expr(f"STOP + INTERVAL {readmission_window_days} DAYS"))
    readmit_pool = flagged.filter(~F.col("is_planned") & ~F.col("is_continuation"))

    c = candidates.alias("c")
    a = readmit_pool.alias("a")

    readmitted_ids = (
        c.join(
            a,
            (F.col("c.PATIENT") == F.col("a.PATIENT"))
            & (F.col("a.Id") != F.col("c.Id"))
            & (F.col("a.START") > F.col("c.STOP"))
            & (F.col("a.START") <= F.col("c.readmit_deadline")),
            "left_semi",
        )
        .select(F.col("c.Id").alias("readmitted_id"))
        .distinct()
    )

    labeled = (
        c.join(readmitted_ids, F.col("c.Id") == F.col("readmitted_id"), "left")
        .withColumn("is_readmitted", F.when(F.col("readmitted_id").isNotNull(), 1).otherwise(0))
        .drop("readmitted_id")
    )

    reference_date_exclusive_end = F.lit(reference_date) + F.expr("INTERVAL 1 DAY")

    labeled = labeled.join(
        patients.select(F.col("Id").alias("_death_patient_id"), F.col("DEATHDATE")),
        F.col("c.PATIENT") == F.col("_death_patient_id"),
        "left",
    ).drop("_death_patient_id")

    keep_condition = (F.col("is_readmitted") == 1) | (
        (F.col("DEATHDATE").isNull() | (F.col("DEATHDATE") > F.col("c.readmit_deadline")))
        & (F.col("c.readmit_deadline") < reference_date_exclusive_end)
    )

    return labeled.filter(keep_condition).select(
        F.col("c.PATIENT").alias("PATIENT"),
        F.col("c.Id").alias("encounter_id"),
        F.col("c.START").alias("index_start"),
        F.col("c.STOP").alias("index_stop"),
        F.col("c.REASONCODE").alias("REASONCODE"),
        F.col("c.REASONDESCRIPTION").alias("REASONDESCRIPTION"),
        F.col("c.PAYER").alias("PAYER"),
        F.col("c.PROVIDER").alias("PROVIDER"),
        F.col("c.ORGANIZATION").alias("ORGANIZATION"),
        F.col("is_readmitted"),
    )


def compute_lookback_features(
    index_encounters: DataFrame,
    all_encounters: DataFrame,
    conditions: DataFrame,
    medications: DataFrame,
    procedures: DataFrame,
    careplans: DataFrame,
    observations: DataFrame,
    lookback_years: int = 1,
) -> DataFrame:
    """Appends 8 lookback-window aggregate feature columns, each computed over
    [window_start, index_start) -- index_start itself always excluded."""
    idx = index_encounters.withColumn(
        "window_start", F.add_months(F.to_date(F.col("index_start")), -12 * lookback_years)
    )
    idx_keys = idx.select("encounter_id", "PATIENT", "window_start", "index_start")

    def _left_count(other: DataFrame, filter_expr, distinct_col: str | None, out_col: str) -> DataFrame:
        nonlocal idx
        joined = idx_keys.join(other, on="PATIENT", how="inner").filter(filter_expr)
        if distinct_col:
            joined = joined.select("encounter_id", distinct_col).distinct()
        counted = joined.groupBy("encounter_id").count().withColumnRenamed("count", out_col)
        idx = idx.join(counted, on="encounter_id", how="left").withColumn(
            out_col, F.coalesce(F.col(out_col), F.lit(0))
        )

    prior_filter = (
        (F.col("START") >= F.col("window_start"))
        & (F.col("START") < F.col("index_start"))
        & (F.col("STOP") <= F.col("index_start"))
    )
    _left_count(all_encounters, prior_filter, None, "prior_encounter_count")
    _left_count(
        all_encounters.filter(F.col("ENCOUNTERCLASS") == "inpatient"),
        prior_filter,
        None,
        "prior_inpatient_count",
    )
    _left_count(
        all_encounters.filter(F.col("ENCOUNTERCLASS") == "emergency"),
        prior_filter,
        None,
        "prior_emergency_count",
    )
    # Leakage fix: both sides explicitly truncated to whole calendar days so a condition/careplan
    # coded the SAME DAY as the index admission is never counted as pre-existing history (see
    # design doc's "Leakage fix" note -- relying on the implicit DateType-to-midnight cast alone
    # would incorrectly count same-day records as "before" the admission's exact time).
    active_condition_filter = (F.to_date(F.col("START")) < F.to_date(F.col("index_start"))) & (
        F.col("STOP").isNull() | (F.col("STOP") >= F.col("window_start"))
    )
    _left_count(conditions, active_condition_filter, "CODE", "active_condition_count")
    _left_count(careplans, active_condition_filter, "CODE", "active_careplan_count")
    # medications.START is already TimestampType (unlike conditions/careplans.START, which are
    # DateType) -- START < index_start is already an exact instant-to-instant comparison, no
    # implicit-cast leakage risk to fix here.
    active_medication_filter = (F.col("START") < F.col("index_start")) & (
        F.col("STOP").isNull() | (F.col("STOP") >= F.col("window_start"))
    )
    _left_count(medications, active_medication_filter, "CODE", "active_medication_count")
    _left_count(
        procedures,
        (F.col("START") >= F.col("window_start")) & (F.col("START") < F.col("index_start")),
        None,
        "procedure_count_window",
    )
    _left_count(
        observations,
        (F.col("DATE") >= F.col("window_start")) & (F.col("DATE") < F.col("index_start")),
        None,
        "observation_count_window",
    )

    return idx.drop("window_start")


def join_dimension_attributes(
    features: DataFrame,
    payers: DataFrame,
    providers: DataFrame,
    organizations: DataFrame,
) -> DataFrame:
    """Left-joins static, index-encounter-level dimension attributes. Each dimension table is
    narrowed to just its join key + the one needed attribute BEFORE joining -- joining the
    full-width tables would leak their other raw columns (e.g. providers.GENDER) into the
    output, colliding with patients.GENDER in join_patient_demographics."""
    payers_narrow = payers.select(
        F.col("Id").alias("_payer_id"), F.col("OWNERSHIP").alias("payer_ownership")
    )
    providers_narrow = providers.select(
        F.col("Id").alias("_provider_id"), F.col("SPECIALITY").alias("provider_specialty")
    )
    organizations_narrow = organizations.select(
        F.col("Id").alias("_organization_id"),
        F.col("UTILIZATION").alias("organization_utilization"),
    )

    return (
        features.join(payers_narrow, F.col("PAYER") == F.col("_payer_id"), "left")
        .join(providers_narrow, F.col("PROVIDER") == F.col("_provider_id"), "left")
        .join(organizations_narrow, F.col("ORGANIZATION") == F.col("_organization_id"), "left")
        .drop("_payer_id", "_provider_id", "_organization_id", "PAYER", "PROVIDER", "ORGANIZATION")
    )


def join_patient_demographics(features: DataFrame, patients: DataFrame) -> DataFrame:
    """Left-joins patient demographics and produces the gold table's final, canonical shape via
    one closing .select() in GOLD_TABLE_COLUMNS order. Everything not in that list is
    deliberately dropped, not carried through by accident."""
    patients_narrow = patients.select(
        F.col("Id").alias("_patient_id"),
        F.col("BIRTHDATE"),
        F.col("GENDER").alias("gender"),
        F.col("RACE").alias("race"),
        F.col("ETHNICITY").alias("ethnicity"),
        F.col("MARITAL").alias("marital_status"),
    )

    joined = features.join(
        patients_narrow, F.col("PATIENT") == F.col("_patient_id"), "left"
    ).withColumn(
        "age_at_index_years", F.datediff(F.col("index_start"), F.col("BIRTHDATE")) / 365.25
    )

    return joined.select(
        F.col("PATIENT").alias("patient_id"),
        F.col("encounter_id"),
        F.col("index_start"),
        F.col("index_stop"),
        F.col("is_readmitted"),
        F.col("REASONCODE").alias("admission_reason_code"),
        F.col("REASONDESCRIPTION").alias("admission_reason_description"),
        F.col("payer_ownership"),
        F.col("provider_specialty"),
        F.col("organization_utilization"),
        F.col("prior_encounter_count"),
        F.col("prior_inpatient_count"),
        F.col("prior_emergency_count"),
        F.col("active_condition_count"),
        F.col("active_medication_count"),
        F.col("procedure_count_window"),
        F.col("active_careplan_count"),
        F.col("observation_count_window"),
        F.col("age_at_index_years"),
        F.col("gender"),
        F.col("race"),
        F.col("ethnicity"),
        F.col("marital_status"),
    )


def build_big_join(spark: SparkSession, config: BigJoinConfig) -> DataFrame:
    """Orchestrates the full join and writes the result to config.output_dir as Parquet, then (last) the
    _gold_metadata.json sidecar that downstream code validates against."""
    # Validated with exactly the metadata validator's strictness, before any I/O, so a bad config can never leave a Parquet
    # directory without metadata behind. `type(...) is int` rejects bool and float.
    if type(config.readmission_window_days) is not int or config.readmission_window_days < 1:
        raise ValueError(f"readmission_window_days must be an int >= 1; got {config.readmission_window_days!r}")
    if type(config.lookback_years) is not int or config.lookback_years < 1:
        # >= 1 is a deliberate tightening: 0/negative gives an empty/inverted window in which every windowed feature is 0
        raise ValueError(f"lookback_years must be an int >= 1; got {config.lookback_years!r}")
    reference_date = parse_yyyymmdd(config.reference_date, "reference_date")

    if config.output_dir.exists():
        raise FileExistsError(f"output_dir already exists: {config.output_dir}")

    summary_path = config.input_dir / "generation_summary.json"
    if not summary_path.is_file():
        raise FileNotFoundError(f"generation_summary.json not found: {summary_path}")
    summary = json.loads(summary_path.read_text())
    if "reference_date" not in summary:
        raise ValueError(f"{summary_path} is missing a 'reference_date' key")
    recorded_reference_date = summary["reference_date"]
    if recorded_reference_date != config.reference_date:
        raise ValueError(
            f"config.reference_date ({config.reference_date}) does not match the input "
            f"data's own reference_date ({recorded_reference_date}) -- see {summary_path}"
        )

    tables = load_synthea_tables(spark, config.input_dir)

    index_encounters = build_index_encounters(
        tables["encounters.csv"],
        tables["patients.csv"],
        tables["procedures.csv"],
        reference_date,
        config.readmission_window_days,
    )
    features = compute_lookback_features(
        index_encounters,
        tables["encounters.csv"],
        tables["conditions.csv"],
        tables["medications.csv"],
        tables["procedures.csv"],
        tables["careplans.csv"],
        tables["observations.csv"],
        config.lookback_years,
    )
    features = join_dimension_attributes(
        features, tables["payers.csv"], tables["providers.csv"], tables["organizations.csv"]
    )
    gold = join_patient_demographics(features, tables["patients.csv"])

    # Diagnostics recorded in the metadata: they make a silently non-matching PLANNED_PROCEDURE_CODES visible (n_planned_stays == 0).
    stay_stats = (
        flag_inpatient_stays(tables["encounters.csv"], tables["procedures.csv"])
        .agg(
            F.count(F.lit(1)).alias("n_inpatient_stays"),
            F.sum(F.col("is_planned").cast("int")).alias("n_planned_stays"),
            F.sum(F.col("is_continuation").cast("int")).alias("n_continuation_stays"),
            F.sum((~F.col("is_terminal")).cast("int")).alias("n_nonterminal_stays"),
        )
        .first()
    )

    gold.write.parquet(str(config.output_dir))
    written_stats = (
        spark.read.parquet(str(config.output_dir))
        .agg(F.count(F.lit(1)).alias("n_rows"), F.sum("is_readmitted").alias("n_positive"))
        .first()
    )
    # Written LAST, so its presence is a completion marker (like _SUCCESS). `sum` over zero rows is null -> 0.
    metadata = GoldMetadata(
        label_definition=LABEL_DEFINITION,
        reference_date=config.reference_date,
        readmission_window_days=config.readmission_window_days,
        lookback_years=config.lookback_years,
        planned_procedure_codes=PLANNED_PROCEDURE_CODES,
        n_rows=int(written_stats["n_rows"]),
        n_positive=int(written_stats["n_positive"] or 0),
        n_inpatient_stays=int(stay_stats["n_inpatient_stays"] or 0),
        n_planned_stays=int(stay_stats["n_planned_stays"] or 0),
        n_continuation_stays=int(stay_stats["n_continuation_stays"] or 0),
        n_nonterminal_stays=int(stay_stats["n_nonterminal_stays"] or 0),
    )
    write_gold_metadata(config.output_dir, metadata)
    if metadata.n_planned_stays == 0 and metadata.n_inpatient_stays > 0:
        warnings.warn(
            "no inpatient stay matched PLANNED_PROCEDURE_CODES; the label reduces to the continuation/terminal rules only "
            "(check procedures.csv and the code list)",
            UserWarning,
            stacklevel=2,
        )
    return gold
