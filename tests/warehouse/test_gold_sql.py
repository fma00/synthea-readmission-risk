"""serving.gold's schema file and the GOLD data tests (v3 slice 1): gold.json against the gold column constants, GOLD-05
against the loader's required non-null columns, render_script's table rules and run_data_tests' gold parameters. The shape
rules shared with the other scripts are in test_data_tests.py."""

from __future__ import annotations

import re

import pytest

from readmission_risk.models.data import (
    _REQUIRED_NONNULL_COLUMNS,
    EXPECTED_GOLD_COLUMNS,
)
from readmission_risk.pipeline.big_join import GOLD_TABLE_COLUMNS
from readmission_risk.pipeline.fingerprint import GOLD_COLUMN_KINDS
from readmission_risk.warehouse import bigquery as wbq
from readmission_risk.warehouse import data_tests
from readmission_risk.warehouse.data_tests import render_script, run_data_tests
from readmission_risk.warehouse.gold import GOLD_NOT_NULL_COLUMNS
from tests.warehouse.fake_bigquery import FakeBigQuery, FakeJob, assertion_error

STAGING = "synthea-readmission-risk.scratch_gold.gold_staging_abcdef012345"
PREDICTIONS = "synthea-readmission-risk.serving.predictions"
GOLD_KW = {"predictions_table": PREDICTIONS, "n_rows": 10, "n_positive": 2, "predicted_fingerprint": "f" * 64}


# ---------------------------------------------------------------------------------------------------------- gold.json
def test_gold_schema_is_the_gold_columns_in_order_all_nullable_with_their_kinds():
    schema = wbq.load_schema("gold")
    assert [f["name"] for f in schema] == list(GOLD_TABLE_COLUMNS)
    assert set(GOLD_TABLE_COLUMNS) == set(EXPECTED_GOLD_COLUMNS)
    kinds = {"string": "STRING", "int": "INTEGER", "double": "FLOAT", "timestamp": "TIMESTAMP"}
    # GOLD_COLUMN_KINDS is kept equal to the Big Join's Spark output types by tests/pipeline/test_fingerprint.py
    assert [f["type"] for f in schema] == [kinds[GOLD_COLUMN_KINDS[c]] for c in GOLD_TABLE_COLUMNS]
    assert {f["mode"] for f in schema} == {"NULLABLE"}  # the load accepts the Parquet's nullable columns; GOLD-01/05 check nulls
    assert all(isinstance(f["description"], str) and f["description"].strip() for f in schema)


def test_query_config_takes_a_larger_cap_for_the_gold_publish():
    assert wbq.query_config().maximum_bytes_billed == wbq.MAX_BYTES_BILLED
    assert wbq.query_config(max_bytes_billed=wbq.GOLD_PUBLISH_MAX_BYTES_BILLED).maximum_bytes_billed == 1024**3


# ---------------------------------------------------------------------------------------------------------- GOLD-05
def test_gold_not_null_columns_are_the_loaders_minus_the_two_keys():
    assert set(GOLD_NOT_NULL_COLUMNS) == set(_REQUIRED_NONNULL_COLUMNS) - {"encounter_id", "index_stop"}
    assert len(GOLD_NOT_NULL_COLUMNS) == 13 and "age_at_index_years" in GOLD_NOT_NULL_COLUMNS


def test_gold_05_in_the_sql_checks_exactly_those_columns():
    text = data_tests._script_text("gold")
    block = re.search(r"COUNTIF\(\(([^()]*)\) IS NOT TRUE\) AS bad_not_null", text)
    assert block is not None
    assert re.findall(r"`(\w+)` IS NOT NULL", block.group(1)) == list(GOLD_NOT_NULL_COLUMNS)


def test_gold_sql_filters_predictions_by_partition_and_reads_both_tables():
    text = data_tests._script_text("gold")
    assert text.count("p.`as_of_date` BETWEEN DATE '2000-01-01' AND DATE '2100-12-31'") == 2
    assert "CURRENT_" not in text
    rendered = render_script("gold", table=STAGING, predictions_table=PREDICTIONS)
    assert f"FROM `{STAGING}`" in rendered and f"FROM `{PREDICTIONS}` AS p" in rendered
    assert "{table}" not in rendered and "{predictions_table}" not in rendered


# --------------------------------------------------------------------------------------------------- render_script
def test_render_script_table_rules_for_gold():
    seeded = "synthea-readmission-risk.scratch_gold.gold_seed_1234abcd_gold_07_pred"
    assert f"`{seeded}`" in render_script("gold", table=STAGING, predictions_table=seeded)
    with pytest.raises(ValueError, match="only on a staging table in scratch_gold"):
        render_script("gold", table="synthea-readmission-risk.serving.gold", predictions_table=PREDICTIONS)
    with pytest.raises(ValueError, match="need predictions_table"):
        render_script("gold", table=STAGING)
    with pytest.raises(ValueError, match=r"must be synthea-readmission-risk\.serving\.predictions or a table in scratch_gold"):
        render_script("gold", table=STAGING, predictions_table="synthea-readmission-risk.scratch.predictions_x")
    with pytest.raises(ValueError, match="data tests run only"):
        render_script("gold", table="other-project.scratch_gold.x", predictions_table=PREDICTIONS)
    with pytest.raises(ValueError, match="take no predictions_table"):
        render_script("predictions", table="synthea-readmission-risk.scratch.p", predictions_table=PREDICTIONS)


# -------------------------------------------------------------------------------------------------- run_data_tests
def test_run_data_tests_gold_sends_typed_parameters_the_cap_and_a_fixed_job_id():
    fake = FakeBigQuery()
    result = run_data_tests(fake, "gold", table=STAGING, max_bytes_billed=wbq.GOLD_PUBLISH_MAX_BYTES_BILLED, job_id="gold-audit-x", **GOLD_KW)
    assert result.passed
    _, _sql, config, job_id = fake.calls[0]  # the fake asserts job_retry=None whenever job_id is given
    assert job_id == "gold-audit-x" and config.maximum_bytes_billed == 1024**3
    params = {p.name: (p.type_, p.value) for p in config.query_parameters}
    assert params == {"n_rows": ("INT64", 10), "n_positive": ("INT64", 2), "predicted_fingerprint": ("STRING", "f" * 64)}
    # without job_id the call is unchanged (no job_id, the default cap)
    run_data_tests(fake, "gold", table=STAGING, **GOLD_KW)
    assert fake.calls[1][3] is None and fake.calls[1][2].maximum_bytes_billed == wbq.MAX_BYTES_BILLED


def test_run_data_tests_gold_returns_the_gold_verdict():
    label = data_tests.assertion_descriptions("gold")["GOLD-07"]
    fake = FakeBigQuery(on_query=lambda sql, config, job_id: FakeJob(raise_on_result=assertion_error(label)))
    result = run_data_tests(fake, "gold", table=STAGING, **GOLD_KW)
    assert not result.passed and result.failure == label


def test_run_data_tests_gold_keyword_rules():
    fake = FakeBigQuery()
    with pytest.raises(ValueError, match="need n_rows"):
        run_data_tests(fake, "gold", table=STAGING, predictions_table=PREDICTIONS, n_rows=10)
    with pytest.raises(ValueError, match="take no run_date"):
        run_data_tests(fake, "gold", table=STAGING, scoring_run_id="r", **GOLD_KW)
    with pytest.raises(ValueError, match="take no n_rows"):
        run_data_tests(fake, "scoring_runs", table="synthea-readmission-risk.serving.scoring_runs", n_rows=1)
    with pytest.raises(ValueError, match="need n_rows"):
        run_data_tests(fake, "gold", table=STAGING, **{**GOLD_KW, "n_rows": True})  # a bool is not an int
    assert fake.calls == []
