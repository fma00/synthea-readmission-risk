from pyspark.sql.types import (
    DateType,
    DoubleType,
    IntegerType,
    StringType,
    TimestampType,
)

from readmission_risk.pipeline.schemas import TABLE_SCHEMAS

FIXTURE_ROWS: dict[str, list[str]] = {
    "patients.csv": [
        "Id,BIRTHDATE,DEATHDATE,SSN,DRIVERS,PASSPORT,PREFIX,FIRST,MIDDLE,LAST,SUFFIX,MAIDEN,MARITAL,RACE,ETHNICITY,GENDER,BIRTHPLACE,ADDRESS,CITY,STATE,COUNTY,FIPS,ZIP,LAT,LON,HEALTHCARE_EXPENSES,HEALTHCARE_COVERAGE,INCOME",
        "p1,1980-01-01,,111,,,Mr,A,B,C,,,M,white,nonhispanic,M,Boston,1 St,Boston,MA,Suffolk,25025,02108,42.3,-71.0,1000.0,500.0,50000",
    ],
    "encounters.csv": [
        "Id,START,STOP,PATIENT,ORGANIZATION,PROVIDER,PAYER,ENCOUNTERCLASS,CODE,DESCRIPTION,BASE_ENCOUNTER_COST,TOTAL_CLAIM_COST,PAYER_COVERAGE,REASONCODE,REASONDESCRIPTION",
        "e1,2020-01-01T00:00:00Z,2020-01-02T00:00:00Z,p1,o1,pr1,pay1,inpatient,123,Desc,100.0,200.0,50.0,,",
    ],
    "conditions.csv": [
        "START,STOP,PATIENT,ENCOUNTER,SYSTEM,CODE,DESCRIPTION",
        "2020-01-01,,p1,e1,SNOMED-CT,123,Desc",
    ],
    "medications.csv": [
        "START,STOP,PATIENT,PAYER,ENCOUNTER,CODE,DESCRIPTION,BASE_COST,PAYER_COVERAGE,DISPENSES,TOTALCOST,REASONCODE,REASONDESCRIPTION",
        "2020-01-01T00:00:00Z,,p1,pay1,e1,123,Desc,10.0,5.0,1,10.0,,",
    ],
    "procedures.csv": [
        "START,STOP,PATIENT,ENCOUNTER,SYSTEM,CODE,DESCRIPTION,BASE_COST,REASONCODE,REASONDESCRIPTION",
        "2020-01-01T00:00:00Z,2020-01-01T01:00:00Z,p1,e1,SNOMED-CT,123,Desc,50.0,,",
    ],
    "careplans.csv": [
        "Id,START,STOP,PATIENT,ENCOUNTER,CODE,DESCRIPTION,REASONCODE,REASONDESCRIPTION",
        "cp1,2020-01-01,,p1,e1,123,Desc,,",
    ],
    "observations.csv": [
        "DATE,PATIENT,ENCOUNTER,CATEGORY,CODE,DESCRIPTION,VALUE,UNITS,TYPE",
        "2020-01-01T00:00:00Z,p1,e1,vital-signs,123,Desc,70,kg,numeric",
    ],
    "payers.csv": [
        "Id,NAME,OWNERSHIP,ADDRESS,CITY,STATE_HEADQUARTERED,ZIP,PHONE,AMOUNT_COVERED,AMOUNT_UNCOVERED,REVENUE,COVERED_ENCOUNTERS,UNCOVERED_ENCOUNTERS,COVERED_MEDICATIONS,UNCOVERED_MEDICATIONS,COVERED_PROCEDURES,UNCOVERED_PROCEDURES,COVERED_IMMUNIZATIONS,UNCOVERED_IMMUNIZATIONS,UNIQUE_CUSTOMERS,QOLS_AVG,MEMBER_MONTHS",
        "pay1,Medicare,GOVERNMENT,,,,,,100.0,10.0,1000.0,5,0,5,0,5,0,5,0,1,0.9,12",
    ],
    "providers.csv": [
        "Id,ORGANIZATION,NAME,GENDER,SPECIALITY,ADDRESS,CITY,STATE,ZIP,LAT,LON,ENCOUNTERS,PROCEDURES",
        "pr1,o1,Doc,M,GENERAL PRACTICE,1 St,Boston,MA,02108,42.3,-71.0,5,2",
    ],
    "organizations.csv": [
        "Id,NAME,ADDRESS,CITY,STATE,ZIP,LAT,LON,PHONE,REVENUE,UTILIZATION",
        "o1,Clinic,1 St,Boston,MA,02108,42.3,-71.0,555-1234,0.0,5",
    ],
}

EXPECTED_TYPE_SPOT_CHECKS: dict[str, dict[str, type]] = {
    "patients.csv": {"BIRTHDATE": DateType, "DEATHDATE": DateType, "LAT": DoubleType},
    "encounters.csv": {"START": TimestampType, "STOP": TimestampType, "ENCOUNTERCLASS": StringType},
    "conditions.csv": {"START": DateType, "STOP": DateType},
    "medications.csv": {"START": TimestampType, "DISPENSES": IntegerType},
    "procedures.csv": {"START": TimestampType, "BASE_COST": DoubleType},
    "careplans.csv": {"START": DateType},
    "observations.csv": {"DATE": TimestampType, "VALUE": StringType},
    "payers.csv": {"COVERED_ENCOUNTERS": IntegerType, "QOLS_AVG": DoubleType},
    "providers.csv": {"GENDER": StringType, "ENCOUNTERS": IntegerType},
    "organizations.csv": {"UTILIZATION": IntegerType},
}


def test_tables_joined_matches_table_schemas():
    from readmission_risk.pipeline.big_join import TABLES_JOINED

    assert set(TABLES_JOINED) == set(TABLE_SCHEMAS.keys())


def test_all_schemas_parse_real_shaped_fixtures_without_error(spark, tmp_path):
    for table_name, schema in TABLE_SCHEMAS.items():
        csv_path = tmp_path / table_name
        csv_path.write_text("\n".join(FIXTURE_ROWS[table_name]) + "\n")

        df = spark.read.schema(schema).option("header", True).csv(str(csv_path))
        row = df.collect()[0]

        for column, expected_type in EXPECTED_TYPE_SPOT_CHECKS[table_name].items():
            actual_field = next(f for f in schema.fields if f.name == column)
            assert isinstance(actual_field.dataType, expected_type), (
                f"{table_name}.{column} expected {expected_type}, got {type(actual_field.dataType)}"
            )
            assert row is not None
