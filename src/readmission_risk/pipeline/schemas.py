"""Hand-declared PySpark StructType schemas for the 10 Synthea CSV tables the Big Join reads.
See notes/eg-new-feature/pyspark-big-join-2026-09-19.md for the design this implements.

Field order in each StructType below MUST exactly match its CSV's real column order --
spark.read.schema(...).csv(...) aligns columns POSITIONALLY, not by name. Verified directly
against data/raw/full_run/csv/*.csv during design. `nullable=False` below is schema metadata
only -- Spark's CSV reader does not enforce non-null constraints on read.
"""

from __future__ import annotations

from pyspark.sql.types import (
    DateType,
    DoubleType,
    IntegerType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

PATIENTS_SCHEMA = StructType(
    [
        StructField("Id", StringType(), nullable=False),
        StructField("BIRTHDATE", DateType(), nullable=False),
        StructField("DEATHDATE", DateType(), nullable=True),
        StructField("SSN", StringType(), nullable=True),
        StructField("DRIVERS", StringType(), nullable=True),
        StructField("PASSPORT", StringType(), nullable=True),
        StructField("PREFIX", StringType(), nullable=True),
        StructField("FIRST", StringType(), nullable=True),
        StructField("MIDDLE", StringType(), nullable=True),
        StructField("LAST", StringType(), nullable=True),
        StructField("SUFFIX", StringType(), nullable=True),
        StructField("MAIDEN", StringType(), nullable=True),
        StructField("MARITAL", StringType(), nullable=True),
        StructField("RACE", StringType(), nullable=True),
        StructField("ETHNICITY", StringType(), nullable=True),
        StructField("GENDER", StringType(), nullable=True),
        StructField("BIRTHPLACE", StringType(), nullable=True),
        StructField("ADDRESS", StringType(), nullable=True),
        StructField("CITY", StringType(), nullable=True),
        StructField("STATE", StringType(), nullable=True),
        StructField("COUNTY", StringType(), nullable=True),
        StructField("FIPS", StringType(), nullable=True),
        StructField("ZIP", StringType(), nullable=True),
        StructField("LAT", DoubleType(), nullable=True),
        StructField("LON", DoubleType(), nullable=True),
        StructField("HEALTHCARE_EXPENSES", DoubleType(), nullable=True),
        StructField("HEALTHCARE_COVERAGE", DoubleType(), nullable=True),
        StructField("INCOME", DoubleType(), nullable=True),
    ]
)

ENCOUNTERS_SCHEMA = StructType(
    [
        StructField("Id", StringType(), nullable=False),
        StructField("START", TimestampType(), nullable=False),
        StructField("STOP", TimestampType(), nullable=True),
        StructField("PATIENT", StringType(), nullable=False),
        StructField("ORGANIZATION", StringType(), nullable=True),
        StructField("PROVIDER", StringType(), nullable=True),
        StructField("PAYER", StringType(), nullable=True),
        StructField("ENCOUNTERCLASS", StringType(), nullable=True),
        StructField("CODE", StringType(), nullable=True),
        StructField("DESCRIPTION", StringType(), nullable=True),
        StructField("BASE_ENCOUNTER_COST", DoubleType(), nullable=True),
        StructField("TOTAL_CLAIM_COST", DoubleType(), nullable=True),
        StructField("PAYER_COVERAGE", DoubleType(), nullable=True),
        StructField("REASONCODE", StringType(), nullable=True),
        StructField("REASONDESCRIPTION", StringType(), nullable=True),
    ]
)

CONDITIONS_SCHEMA = StructType(
    [
        StructField("START", DateType(), nullable=False),
        StructField("STOP", DateType(), nullable=True),
        StructField("PATIENT", StringType(), nullable=False),
        StructField("ENCOUNTER", StringType(), nullable=True),
        StructField("SYSTEM", StringType(), nullable=True),
        StructField("CODE", StringType(), nullable=True),
        StructField("DESCRIPTION", StringType(), nullable=True),
    ]
)

MEDICATIONS_SCHEMA = StructType(
    [
        StructField("START", TimestampType(), nullable=False),
        StructField("STOP", TimestampType(), nullable=True),
        StructField("PATIENT", StringType(), nullable=False),
        StructField("PAYER", StringType(), nullable=True),
        StructField("ENCOUNTER", StringType(), nullable=True),
        StructField("CODE", StringType(), nullable=True),
        StructField("DESCRIPTION", StringType(), nullable=True),
        StructField("BASE_COST", DoubleType(), nullable=True),
        StructField("PAYER_COVERAGE", DoubleType(), nullable=True),
        StructField("DISPENSES", IntegerType(), nullable=True),
        StructField("TOTALCOST", DoubleType(), nullable=True),
        StructField("REASONCODE", StringType(), nullable=True),
        StructField("REASONDESCRIPTION", StringType(), nullable=True),
    ]
)

PROCEDURES_SCHEMA = StructType(
    [
        StructField("START", TimestampType(), nullable=False),
        StructField("STOP", TimestampType(), nullable=True),
        StructField("PATIENT", StringType(), nullable=False),
        StructField("ENCOUNTER", StringType(), nullable=True),
        StructField("SYSTEM", StringType(), nullable=True),
        StructField("CODE", StringType(), nullable=True),
        StructField("DESCRIPTION", StringType(), nullable=True),
        StructField("BASE_COST", DoubleType(), nullable=True),
        StructField("REASONCODE", StringType(), nullable=True),
        StructField("REASONDESCRIPTION", StringType(), nullable=True),
    ]
)

CAREPLANS_SCHEMA = StructType(
    [
        StructField("Id", StringType(), nullable=False),
        StructField("START", DateType(), nullable=False),
        StructField("STOP", DateType(), nullable=True),
        StructField("PATIENT", StringType(), nullable=False),
        StructField("ENCOUNTER", StringType(), nullable=True),
        StructField("CODE", StringType(), nullable=True),
        StructField("DESCRIPTION", StringType(), nullable=True),
        StructField("REASONCODE", StringType(), nullable=True),
        StructField("REASONDESCRIPTION", StringType(), nullable=True),
    ]
)

OBSERVATIONS_SCHEMA = StructType(
    [
        StructField("DATE", TimestampType(), nullable=False),
        StructField("PATIENT", StringType(), nullable=False),
        StructField("ENCOUNTER", StringType(), nullable=True),
        StructField("CATEGORY", StringType(), nullable=True),
        StructField("CODE", StringType(), nullable=True),
        StructField("DESCRIPTION", StringType(), nullable=True),
        # VALUE deliberately NOT DoubleType -- mixes numeric and text observations (see TYPE);
        # casting to Double would silently NULL every non-numeric row. This slice only counts
        # rows in the window, never reads VALUE's content.
        StructField("VALUE", StringType(), nullable=True),
        StructField("UNITS", StringType(), nullable=True),
        StructField("TYPE", StringType(), nullable=True),
    ]
)

PAYERS_SCHEMA = StructType(
    [
        StructField("Id", StringType(), nullable=False),
        StructField("NAME", StringType(), nullable=True),
        StructField("OWNERSHIP", StringType(), nullable=True),
        StructField("ADDRESS", StringType(), nullable=True),
        StructField("CITY", StringType(), nullable=True),
        StructField("STATE_HEADQUARTERED", StringType(), nullable=True),
        StructField("ZIP", StringType(), nullable=True),
        StructField("PHONE", StringType(), nullable=True),
        StructField("AMOUNT_COVERED", DoubleType(), nullable=True),
        StructField("AMOUNT_UNCOVERED", DoubleType(), nullable=True),
        StructField("REVENUE", DoubleType(), nullable=True),
        StructField("COVERED_ENCOUNTERS", IntegerType(), nullable=True),
        StructField("UNCOVERED_ENCOUNTERS", IntegerType(), nullable=True),
        StructField("COVERED_MEDICATIONS", IntegerType(), nullable=True),
        StructField("UNCOVERED_MEDICATIONS", IntegerType(), nullable=True),
        StructField("COVERED_PROCEDURES", IntegerType(), nullable=True),
        StructField("UNCOVERED_PROCEDURES", IntegerType(), nullable=True),
        StructField("COVERED_IMMUNIZATIONS", IntegerType(), nullable=True),
        StructField("UNCOVERED_IMMUNIZATIONS", IntegerType(), nullable=True),
        StructField("UNIQUE_CUSTOMERS", IntegerType(), nullable=True),
        StructField("QOLS_AVG", DoubleType(), nullable=True),
        StructField("MEMBER_MONTHS", IntegerType(), nullable=True),
    ]
)

PROVIDERS_SCHEMA = StructType(
    [
        StructField("Id", StringType(), nullable=False),
        StructField("ORGANIZATION", StringType(), nullable=True),
        StructField("NAME", StringType(), nullable=True),
        StructField("GENDER", StringType(), nullable=True),
        StructField("SPECIALITY", StringType(), nullable=True),
        StructField("ADDRESS", StringType(), nullable=True),
        StructField("CITY", StringType(), nullable=True),
        StructField("STATE", StringType(), nullable=True),
        StructField("ZIP", StringType(), nullable=True),
        StructField("LAT", DoubleType(), nullable=True),
        StructField("LON", DoubleType(), nullable=True),
        StructField("ENCOUNTERS", IntegerType(), nullable=True),
        StructField("PROCEDURES", IntegerType(), nullable=True),
    ]
)

ORGANIZATIONS_SCHEMA = StructType(
    [
        StructField("Id", StringType(), nullable=False),
        StructField("NAME", StringType(), nullable=True),
        StructField("ADDRESS", StringType(), nullable=True),
        StructField("CITY", StringType(), nullable=True),
        StructField("STATE", StringType(), nullable=True),
        StructField("ZIP", StringType(), nullable=True),
        StructField("LAT", DoubleType(), nullable=True),
        StructField("LON", DoubleType(), nullable=True),
        StructField("PHONE", StringType(), nullable=True),
        StructField("REVENUE", DoubleType(), nullable=True),
        StructField("UTILIZATION", IntegerType(), nullable=True),
    ]
)

TABLE_SCHEMAS: dict[str, StructType] = {
    "patients.csv": PATIENTS_SCHEMA,
    "encounters.csv": ENCOUNTERS_SCHEMA,
    "conditions.csv": CONDITIONS_SCHEMA,
    "medications.csv": MEDICATIONS_SCHEMA,
    "procedures.csv": PROCEDURES_SCHEMA,
    "careplans.csv": CAREPLANS_SCHEMA,
    "observations.csv": OBSERVATIONS_SCHEMA,
    "payers.csv": PAYERS_SCHEMA,
    "providers.csv": PROVIDERS_SCHEMA,
    "organizations.csv": ORGANIZATIONS_SCHEMA,
}
