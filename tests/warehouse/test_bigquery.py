"""Schemas, identifiers and the query-config factory of the warehouse package (v2 slice 4)."""

from __future__ import annotations

import re
from dataclasses import fields
from pathlib import Path

import pyarrow as pa
import pytest

from readmission_risk.scoring.store import PREDICTION_SCHEMA
from readmission_risk.warehouse import bigquery as wbq
from readmission_risk.warehouse.scoring_runs import RunRecord

_SRC = Path(__file__).resolve().parents[2] / "src" / "readmission_risk"
_BQ_TYPES = {
    pa.date32(): "DATE",
    pa.int32(): "INTEGER",
    pa.bool_(): "BOOLEAN",
    pa.float64(): "FLOAT",
    pa.string(): "STRING",
    pa.timestamp("us", tz="UTC"): "TIMESTAMP",
}


def test_predictions_schema_json_is_prediction_schema_plus_run_id():
    schema = wbq.load_schema("predictions")
    expected = [(f.name, _BQ_TYPES[f.type], "NULLABLE" if f.nullable else "REQUIRED") for f in PREDICTION_SCHEMA]
    expected.append(("scoring_run_id", "STRING", "REQUIRED"))
    assert [(f["name"], f["type"], f["mode"]) for f in schema] == expected
    assert all(isinstance(f["description"], str) and f["description"].strip() for f in schema)


def test_scoring_runs_schema_json_matches_run_record_fields():
    schema = wbq.load_schema("scoring_runs")
    assert [f["name"] for f in schema] == [f.name for f in fields(RunRecord)]
    assert len(schema) == 26
    assert all(f["mode"] in ("REQUIRED", "NULLABLE") and f["description"].strip() for f in schema)


def test_load_schema_refuses_unknown_names():
    with pytest.raises(ValueError, match="unknown table schema"):
        wbq.load_schema("monitoring")


def test_query_config_sets_maximum_bytes_billed():
    config = wbq.query_config()
    assert config.maximum_bytes_billed == wbq.MAX_BYTES_BILLED == 100 * 1024**2
    assert config.use_legacy_sql is False


def test_only_query_config_builds_query_job_configs():
    sources = {p: p.read_text() for p in _SRC.rglob("*.py")}
    builders = [str(p) for p, text in sources.items() if "QueryJobConfig(" in text]
    assert builders == [str(_SRC / "warehouse" / "bigquery.py")]
    # every .query( call in src/ passes job_config=query_config(...)
    for path, text in sources.items():
        for match in re.finditer(r"\.query\(", text):
            call = text[match.start() : match.start() + 400]
            assert "job_config=query_config(" in call, f"{path}: a query without query_config()"


@pytest.mark.parametrize("dataset, table", [("serving", "pre-dictions"), ("ser ving", "x"), ("1x", "y"), ("a", "")])
def test_table_id_refuses_bad_identifiers(dataset, table):
    with pytest.raises(ValueError, match="invalid BigQuery identifier"):
        wbq.table_id(dataset, table)


def test_table_id_and_quoted():
    assert wbq.table_id("serving", "predictions") == "synthea-readmission-risk.serving.predictions"
    assert wbq.quoted("partition") == "`partition`"
    with pytest.raises(ValueError):
        wbq.quoted("a`b")
