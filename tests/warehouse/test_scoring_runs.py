"""The scoring_runs row: a parameterised DML INSERT of all 26 columns (D6: no load jobs into serving)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from readmission_risk.warehouse.bigquery import column_names
from readmission_risk.warehouse.scoring_runs import (
    RunRecord,
    append_run,
    insert_statement,
    record_parameters,
)
from tests.warehouse.fake_bigquery import FakeBigQuery, FakeJob

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


def make_record(**overrides) -> RunRecord:
    fields = dict(
        scoring_run_id="r" * 32, status="succeeded", started_at=NOW, finished_at=NOW, run_date=date(2025, 6, 30),
        trigger="laptop", github_run_url=None, git_commit="abc", git_dirty=False, python_version="3.14.7",
        gold_uri="gs://g/full_run_2b", gold_fingerprint="f", champion_sha256=None, model_bundle_id="b", model_name="m",
        predictions_table="synthea-readmission-risk.serving.predictions", top_n=10, window_days=30, n_predictions=12,
        n_top_n=10, history_tests_passed=True, predictions_tests_passed=True, published=True, data_test_failure=None,
        error_type=None, error_message=None,
    )  # fmt: skip
    return RunRecord(**{**fields, **overrides})


def test_append_run_is_a_parameterised_insert_of_all_26_columns():
    fake = FakeBigQuery()
    append_run(fake, make_record())
    [(kind, sql, config, job_id)] = fake.calls
    assert kind == "query" and job_id == "record_" + "r" * 32
    names = column_names("scoring_runs")
    assert sql == insert_statement()
    assert sql.startswith("INSERT INTO `synthea-readmission-risk.serving.scoring_runs` (")
    assert sql.count("`") == 2 * (len(names) + 1)  # every column and the table back-quoted
    assert [p.name for p in config.query_parameters] == names and len(names) == 26
    types = {p.name: p.type_ for p in config.query_parameters}
    assert (types["started_at"], types["run_date"], types["published"], types["top_n"]) == ("TIMESTAMP", "DATE", "BOOL", "INT64")
    nulls = {p.name: p.value for p in config.query_parameters}
    assert nulls["github_run_url"] is None and nulls["champion_sha256"] is None  # typed NULLs
    assert config.maximum_bytes_billed == 100 * 1024**2


def test_append_run_affected_rows_postcondition():
    fake = FakeBigQuery(on_query=lambda sql, config, job_id: FakeJob(num_dml_affected_rows=0))
    with pytest.raises(RuntimeError, match="affected 0 rows"):
        append_run(fake, make_record())


def test_append_run_refuses_an_unfinished_record():
    with pytest.raises(ValueError, match="only once"):
        append_run(FakeBigQuery(), make_record(status=None))


def test_record_parameters_follow_the_schema():
    params = record_parameters(replace(make_record(), run_date=None))
    assert params[[p.name for p in params].index("run_date")].value is None
