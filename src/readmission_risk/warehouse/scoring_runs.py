"""One scoring_runs row per scoring attempt (PRD FR20's source of truth; FR21: refused and failed attempts are recorded
too), inserted by a parameterised DML INSERT: the scorer may not run load jobs into serving (D6). See
notes/eg-new-feature/v2-slice4-score-to-bigquery-2026-10-11.md (Interfaces > warehouse/scoring_runs.py).
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import date, datetime

from .bigquery import SERVING_DATASET, load_schema, query_config, quoted, table_id

_PARAMETER_TYPES = {"STRING": "STRING", "TIMESTAMP": "TIMESTAMP", "DATE": "DATE", "BOOLEAN": "BOOL", "INTEGER": "INT64"}
ERROR_MESSAGE_LIMIT = 4000


@dataclass
class RunRecord:
    scoring_run_id: str
    status: str | None  # set before the insert; None only while the run is in progress
    started_at: datetime
    finished_at: datetime | None
    run_date: date | None
    trigger: str
    github_run_url: str | None
    git_commit: str | None
    git_dirty: bool | None
    python_version: str
    gold_uri: str
    gold_fingerprint: str | None
    champion_sha256: str | None
    model_bundle_id: str | None
    model_name: str | None
    predictions_table: str
    top_n: int
    window_days: int | None
    n_predictions: int | None
    n_top_n: int | None
    history_tests_passed: bool | None
    predictions_tests_passed: bool | None
    published: bool
    data_test_failure: str | None
    error_type: str | None
    error_message: str | None


def scoring_runs_table_id() -> str:
    return table_id(SERVING_DATASET, "scoring_runs")


def record_job_id(scoring_run_id: str) -> str:
    return f"record_{scoring_run_id}"


def record_parameters(record: RunRecord) -> list:
    """One typed parameter per column (a None is a typed NULL), in the schema's order."""
    from google.cloud import bigquery

    schema = load_schema("scoring_runs")
    if [f["name"] for f in schema] != [f.name for f in fields(RunRecord)]:
        raise RuntimeError("RunRecord's fields do not match warehouse/schemas/scoring_runs.json")
    return [
        bigquery.ScalarQueryParameter(f["name"], _PARAMETER_TYPES[f["type"]], getattr(record, f["name"]))
        for f in schema
    ]


def insert_statement() -> str:
    names = [f["name"] for f in load_schema("scoring_runs")]
    columns = ", ".join(quoted(n) for n in names)
    values = ", ".join(f"@{n}" for n in names)
    return f"INSERT INTO {quoted(scoring_runs_table_id())} ({columns}) VALUES ({values})"  # noqa: S608 -- identifiers validated by table_id() and back-quoted; values are query parameters


def append_run(client, record: RunRecord) -> None:
    """Inserts the row; returns None or raises. A RuntimeError is the affected-rows post-condition (the job ran but did
    not insert exactly one row); any other exception is resolved by the caller via the job id."""
    if record.status is None or record.finished_at is None:
        raise ValueError("a run is recorded only once its status and finished_at are set")
    job = client.query(
        insert_statement(),
        job_config=query_config(query_parameters=record_parameters(record)),
        job_id=record_job_id(record.scoring_run_id),
        job_retry=None,  # a fixed job id cannot be retried under the same id
    )
    job.result()  # no client timeout
    if job.num_dml_affected_rows != 1:
        raise RuntimeError(f"the scoring_runs insert affected {job.num_dml_affected_rows} rows; expected 1")
