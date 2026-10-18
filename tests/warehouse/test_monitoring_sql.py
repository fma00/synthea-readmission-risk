"""The serving.monitoring schema and its ASSERT script (v2 slice 7): literals pinned to deploy/monitoring.json, the
NULL-safety shape of every row check, and the run_data_tests routing. The SQL itself is proven live by make m5-monitoring."""

from __future__ import annotations

import json
import re
from dataclasses import fields
from pathlib import Path

import pytest

from readmission_risk.monitoring.config import read_monitoring_config
from readmission_risk.monitoring.store import MonitoringRecord
from readmission_risk.warehouse import bigquery as wbq
from readmission_risk.warehouse.data_tests import _script_text, run_data_tests
from tests.warehouse.fake_bigquery import FakeBigQuery, FakeJob, assertion_error

ROOT = Path(__file__).resolve().parents[2]
CONFIG = read_monitoring_config(ROOT / "deploy" / "monitoring.json")
TABLE = "synthea-readmission-risk.serving.monitoring"


def test_schema_matches_the_record_and_has_descriptions():
    schema = wbq.load_schema("monitoring")
    assert [f["name"] for f in schema] == [f.name for f in fields(MonitoringRecord)]
    assert all(f["mode"] in ("REQUIRED", "NULLABLE") and f["description"].strip() for f in schema)
    required = {f["name"] for f in schema if f["mode"] == "REQUIRED"}
    assert required == {"monitoring_run_id", "status", "started_at", "finished_at", "trigger"}


def test_literals_equal_the_config():
    sql = _script_text("monitoring")
    assert f"TIMESTAMP '{CONFIG.epoch.isoformat()}'" in sql
    assert f"DATE_DIFF(`sim_date`, DATE '{CONFIG.first_sim_date.isoformat()}', DAY), 7" in sql
    assert f"BETWEEN DATE '{CONFIG.first_sim_date.isoformat()}' AND DATE '{CONFIG.last_sim_date.isoformat()}'" in sql


def test_every_nullable_operand_is_guarded():
    sql = _script_text("monitoring")
    assert "`verdict` IS NULL OR `verdict` != 'alert'" in sql  # MON-08 would otherwise be NULL on every failed row
    assert "`sim_date` IS NULL OR MOD" in sql and "`cadence` IS NULL OR `cadence` IN" in sql
    assert "`oe_ratio` IS NULL OR" in sql and "`status` != 'succeeded'" in sql


def test_monitoring_data_tests_take_no_parameters_and_report_a_failure():
    fake = FakeBigQuery()
    assert run_data_tests(fake, "monitoring", table=TABLE).passed
    [(kind, sql, config, _)] = fake.calls
    assert kind == "query" and f"FROM `{TABLE}`" in sql and config.query_parameters == []
    label = "MON-08: an alert does not meet the pre-registered rule with full coverage"

    def fail(sql, config, job_id):
        return FakeJob(raise_on_result=assertion_error(label))

    result = run_data_tests(FakeBigQuery(on_query=fail), "monitoring", table=TABLE)
    assert not result.passed and result.failure == label
    with pytest.raises(ValueError, match="take no parameters"):
        run_data_tests(FakeBigQuery(), "monitoring", table=TABLE, scoring_run_id="x")


def test_labels_are_in_order():
    ids = re.findall(r"AS '(MON-\d{2}):", _script_text("monitoring"))
    assert ids == [f"MON-{i:02d}" for i in range(1, 13)]
    assert json.loads((ROOT / "src/readmission_risk/warehouse/schemas/monitoring.json").read_text())[0]["name"] == "monitoring_run_id"
