"""serving.monitoring and the reads the tick needs from serving.predictions / serving.scoring_runs. google.cloud.bigquery is
imported inside the functions; every query goes through warehouse.bigquery.query_config (maximum_bytes_billed) and filters
the partition column. Frames are built by hand from result rows (no to_dataframe(): it needs db-dtypes, which is not
pinned). See notes/eg-new-feature/v2-slice7-monitoring-tick-ledger-2026-10-18.md (Interfaces > monitoring/store.py).
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from dataclasses import dataclass, fields
from datetime import date, datetime

import pandas as pd

from readmission_risk.warehouse.bigquery import (
    SERVING_DATASET,
    load_schema,
    query_config,
    quoted,
    table_id,
)
from readmission_risk.warehouse.staging import resolve_job

from .config import MonitoringConfig

ERROR_MESSAGE_LIMIT = 4000
_PARAMETER_TYPES = {
    "STRING": "STRING", "TIMESTAMP": "TIMESTAMP", "DATE": "DATE", "BOOLEAN": "BOOL", "INTEGER": "INT64", "FLOAT": "FLOAT64",
}  # fmt: skip


@dataclass
class MonitoringRecord:
    monitoring_run_id: str
    status: str | None  # set before the insert
    failed_stage: str | None
    started_at: datetime
    finished_at: datetime | None
    sim_date: date | None
    cadence: str | None
    trigger: str
    github_run_url: str | None
    git_commit: str | None
    git_dirty: bool | None
    github_run_attempt: int | None
    history_tests_passed: bool | None
    scoring_run_id: str | None
    model_bundle_id: str | None
    gold_fingerprint: str | None
    gold_fingerprint_definition: str | None
    verdict: str | None
    cohort_start: date | None
    cohort_end: date | None
    n_matured: int | None
    observed: int | None
    expected: float | None
    oe_ratio: float | None
    oe_ci_low: float | None
    oe_ci_high: float | None
    auc: float | None
    n_label_after_sim: int | None
    n_immature: int | None
    expected_immature: float | None
    coverage_required: int | None
    coverage_found: int | None
    min_positives_floor: int | None
    band_low: float | None
    band_high: float | None
    error_type: str | None
    error_message: str | None


def new_record(monitoring_run_id: str, started_at: datetime, trigger: str) -> MonitoringRecord:
    values = {f.name: None for f in fields(MonitoringRecord)}
    values.update(monitoring_run_id=monitoring_run_id, started_at=started_at, trigger=trigger)
    return MonitoringRecord(**values)


def monitoring_table_id() -> str:
    return table_id(SERVING_DATASET, "monitoring")


def _scalar(name: str, type_: str, value):
    from google.cloud import bigquery

    return bigquery.ScalarQueryParameter(name, type_, value)


def _array(name: str, type_: str, values):
    from google.cloud import bigquery

    return bigquery.ArrayQueryParameter(name, type_, list(values))


def _rows(client, sql: str, parameters: list) -> list[dict]:
    return [dict(row.items()) for row in client.query(sql, job_config=query_config(query_parameters=parameters)).result()]


def _frame(rows: list[dict], columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame([{c: row.get(c) for c in columns} for row in rows], columns=columns)


def _epoch(config: MonitoringConfig):
    return _scalar("epoch", "DATE", config.epoch)


def read_ticks(client, config: MonitoringConfig) -> pd.DataFrame:
    columns = [f["name"] for f in load_schema("monitoring")]
    sql = (
        f"SELECT {', '.join(quoted(c) for c in columns)} FROM {quoted(monitoring_table_id())} "  # noqa: S608 -- identifiers validated by table_id() and back-quoted; values are query parameters
        "WHERE `started_at` >= TIMESTAMP(@epoch) ORDER BY `started_at`, `monitoring_run_id`"
    )
    return _frame(_rows(client, sql, [_epoch(config)]), columns)


def succeeded_sim_dates(ticks: pd.DataFrame, config: MonitoringConfig) -> set[date]:
    mask = (ticks["status"] == "succeeded") & (ticks["model_bundle_id"] == config.model_bundle_id)
    return {d for d in ticks.loc[mask, "sim_date"] if d is not None and not pd.isna(d)}


def published_runs(client, config: MonitoringConfig, lo: date, hi: date) -> pd.DataFrame:
    sql = (
        "SELECT `as_of_date`, `scoring_run_id`, COUNT(*) AS `n_rows` "  # noqa: S608 -- identifiers validated by table_id() and back-quoted; values are query parameters
        f"FROM {quoted(table_id(SERVING_DATASET, 'predictions'))} "
        "WHERE `as_of_date` BETWEEN @lo AND @hi AND `model_bundle_id` = @bundle GROUP BY 1, 2"
    )
    params = [_scalar("lo", "DATE", lo), _scalar("hi", "DATE", hi), _scalar("bundle", "STRING", config.model_bundle_id)]
    return _frame(_rows(client, sql, params), ["as_of_date", "scoring_run_id", "n_rows"])


_FACT_COLUMNS = [
    "scoring_run_id", "run_date", "status", "published", "n_predictions", "model_bundle_id", "window_days", "top_n",
    "gold_fingerprint", "gold_fingerprint_definition",
]  # fmt: skip


def scoring_run_facts(client, scoring_run_ids: Iterable[str], config: MonitoringConfig) -> pd.DataFrame:
    ids = sorted({i for i in scoring_run_ids if i})
    if not ids:
        return _frame([], _FACT_COLUMNS)
    sql = (
        f"SELECT {', '.join(quoted(c) for c in _FACT_COLUMNS)} FROM {quoted(table_id(SERVING_DATASET, 'scoring_runs'))} "  # noqa: S608 -- identifiers validated by table_id() and back-quoted; values are query parameters
        "WHERE `started_at` >= TIMESTAMP(@epoch) AND `scoring_run_id` IN UNNEST(@ids)"
    )
    return _frame(_rows(client, sql, [_epoch(config), _array("ids", "STRING", ids)]), _FACT_COLUMNS)


_RUN_COLUMNS = ["scoring_run_id", "started_at", "run_date", "status", "trigger", "github_run_url"]


def read_scoring_runs(client, config: MonitoringConfig, limit: int = 500) -> pd.DataFrame:
    sql = (
        f"SELECT {', '.join(quoted(c) for c in _RUN_COLUMNS)} FROM {quoted(table_id(SERVING_DATASET, 'scoring_runs'))} "  # noqa: S608 -- identifiers validated by table_id() and back-quoted; values are query parameters
        "WHERE `started_at` >= TIMESTAMP(@epoch) ORDER BY `started_at` DESC, `scoring_run_id` LIMIT @limit"
    )
    return _frame(_rows(client, sql, [_epoch(config), _scalar("limit", "INT64", limit)]), _RUN_COLUMNS)


def found_ticks(
    published: pd.DataFrame, facts: pd.DataFrame, config: MonitoringConfig, candidates: Iterable[date]
) -> dict[date, str]:
    """{date: the scoring_run_id published for it} for each candidate whose published rows come from exactly one run that
    succeeded, was published, scored this date with this bundle, a 7-day window and the config's top_n, and whose row
    count equals its n_predictions. Judged by what is published, not by monitoring rows."""
    by_id = {row["scoring_run_id"]: row for row in facts.to_dict("records")}
    found: dict[date, str] = {}
    for day in candidates:
        runs = published[published["as_of_date"] == day]
        if len(runs) != 1:
            continue
        run_id, n_rows = runs.iloc[0]["scoring_run_id"], int(runs.iloc[0]["n_rows"])
        fact = by_id.get(run_id)
        if fact is None:
            continue
        if (
            fact["status"] == "succeeded"
            and fact["published"] is True
            and fact["run_date"] == day
            and fact["model_bundle_id"] == config.model_bundle_id
            and fact["window_days"] == config.window_days
            and fact["top_n"] == config.top_n
            and fact["n_predictions"] == n_rows
        ):
            found[day] = run_id
    return found


_PREDICTION_COLUMNS = [
    "encounter_id", "discharge_time", "risk_score", "as_of_date", "scoring_run_id", "gold_fingerprint",
    "gold_fingerprint_definition",
]  # fmt: skip


def read_cohort_predictions(client, config: MonitoringConfig, lo: date, hi: date, scoring_run_ids) -> pd.DataFrame:
    ids = sorted(set(scoring_run_ids))
    if not ids:
        frame = _frame([], _PREDICTION_COLUMNS)
    else:
        sql = (
            f"SELECT {', '.join(quoted(c) for c in _PREDICTION_COLUMNS)} FROM {quoted(table_id(SERVING_DATASET, 'predictions'))} "  # noqa: S608 -- identifiers validated by table_id() and back-quoted; values are query parameters
            "WHERE `as_of_date` BETWEEN @lo AND @hi AND `scoring_run_id` IN UNNEST(@ids)"
        )
        params = [_scalar("lo", "DATE", lo), _scalar("hi", "DATE", hi), _array("ids", "STRING", ids)]
        frame = _frame(_rows(client, sql, params), _PREDICTION_COLUMNS)
    frame["discharge_time"] = pd.to_datetime(frame["discharge_time"], utc=True)
    frame["risk_score"] = frame["risk_score"].astype("float64")
    return frame.sort_values("encounter_id", kind="stable").reset_index(drop=True)


def monitor_job_id(monitoring_run_id: str) -> str:
    return f"monitor_{monitoring_run_id}"


def record_parameters(record: MonitoringRecord) -> list:
    schema = load_schema("monitoring")
    if [f["name"] for f in schema] != [f.name for f in fields(MonitoringRecord)]:
        raise RuntimeError("MonitoringRecord's fields do not match warehouse/schemas/monitoring.json")
    return [_scalar(f["name"], _PARAMETER_TYPES[f["type"]], getattr(record, f["name"])) for f in schema]


def insert_statement() -> str:
    names = [f["name"] for f in load_schema("monitoring")]
    columns = ", ".join(quoted(n) for n in names)
    values = ", ".join(f"@{n}" for n in names)
    return f"INSERT INTO {quoted(monitoring_table_id())} ({columns}) VALUES ({values})"  # noqa: S608 -- identifiers validated by table_id() and back-quoted; values are query parameters


def append_monitoring(client, record: MonitoringRecord) -> None:
    if record.status is None or record.finished_at is None:
        raise ValueError("a tick is recorded only once its status and finished_at are set")
    if record.error_message is not None:
        record.error_message = record.error_message[:ERROR_MESSAGE_LIMIT]
    job = client.query(
        insert_statement(),
        job_config=query_config(query_parameters=record_parameters(record)),
        job_id=monitor_job_id(record.monitoring_run_id),
        job_retry=None,  # a fixed job id cannot be retried under the same id
    )
    job.result()
    if job.num_dml_affected_rows != 1:
        raise RuntimeError(f"the monitoring insert affected {job.num_dml_affected_rows} rows; expected 1")


def record_monitoring(client, record: MonitoringRecord) -> tuple[bool, BaseException | None]:
    """Inserts the row; a client-side error is resolved by the insert job's id before declaring it unrecorded."""
    try:
        append_monitoring(client, record)
    except RuntimeError as exc:
        print(f"WARNING: the tick was not recorded in serving.monitoring: {exc}", file=sys.stderr)
        return False, exc
    except Exception as exc:  # noqa: BLE001 -- resolved by the job id below; the tick's own outcome must not be lost
        outcome = resolve_job(client, monitor_job_id(record.monitoring_run_id))
        if outcome is True:
            return True, None
        unknown = " (outcome unknown)" if outcome is None else ""
        print(f"WARNING: the tick was not recorded in serving.monitoring{unknown}: {exc}", file=sys.stderr)
        return False, exc
    return True, None
