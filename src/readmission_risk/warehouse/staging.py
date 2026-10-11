"""Write-audit-publish for one run date (owner decision D5): the scored rows are loaded into a short-lived staging table in
the scratch dataset, tested there, and only then published onto serving.predictions by ONE DML transaction (delete the
run date's rows, insert the staging rows). DML, not a copy or load job, because those need bigquery.tables.create on
the destination dataset, which the scorer deliberately lacks on serving (D6). See
notes/eg-new-feature/v2-slice4-score-to-bigquery-2026-10-11.md (Interfaces > warehouse/staging.py).
"""

from __future__ import annotations

import io
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from readmission_risk.scoring.store import predictions_arrow_table

from .bigquery import (
    SCRATCH_DATASET,
    SERVING_DATASET,
    column_names,
    load_schema,
    query_config,
    quoted,
    table_id,
    to_bq_schema,
)


def staging_table_id(scoring_run_id: str) -> str:
    return table_id(SCRATCH_DATASET, f"predictions_{scoring_run_id}")


def predictions_table_id() -> str:
    return table_id(SERVING_DATASET, "predictions")


def publish_job_id(scoring_run_id: str) -> str:
    return f"publish_{scoring_run_id}"


def with_run_id(arrow: pa.Table, scoring_run_id: str) -> pa.Table:
    """The frame's table plus a NON-nullable scoring_run_id column: a default (nullable) field would be written as Parquet
    OPTIONAL, and a load into the REQUIRED column would fail."""
    field = pa.field("scoring_run_id", pa.string(), nullable=False)
    return arrow.append_column(field, pa.array([scoring_run_id] * arrow.num_rows, type=pa.string()))


def make_staging_writer(client, scoring_run_id: str) -> Callable[[pd.DataFrame, Path, date], str]:
    """A writer with score_run_date's signature (output_dir is ignored): validates exactly like the local writer, before any
    call, then creates the staging table and loads the rows into its run-date partition. Returns the staging partition."""
    from google.cloud import bigquery

    staging = staging_table_id(scoring_run_id)

    def writer(predictions: pd.DataFrame, _output_dir: Path, run_date: date) -> str:
        arrow = with_run_id(predictions_arrow_table(predictions, run_date), scoring_run_id)
        buffer = io.BytesIO()
        pq.write_table(arrow, buffer, compression="snappy")
        buffer.seek(0)
        table = bigquery.Table(staging, schema=to_bq_schema(load_schema("predictions")))
        table.time_partitioning = bigquery.TimePartitioning(type_=bigquery.TimePartitioningType.DAY, field="as_of_date")
        client.create_table(table, exists_ok=False)
        destination = f"{staging}${run_date:%Y%m%d}"
        job = client.load_table_from_file(
            buffer,
            destination,
            rewind=True,
            job_config=bigquery.LoadJobConfig(
                source_format=bigquery.SourceFormat.PARQUET,
                write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
                create_disposition=bigquery.CreateDisposition.CREATE_NEVER,
            ),
        )
        job.result()  # no client timeout: the job's final state is what counts
        if job.output_rows != len(predictions):
            raise RuntimeError(f"staging load wrote {job.output_rows} rows; expected {len(predictions)}")
        return f"bigquery://{destination}"

    return writer


def publish_script() -> str:
    """The publish transaction; @run_date is its only parameter. Every column is back-quoted."""
    live, columns = quoted(predictions_table_id()), ", ".join(quoted(c) for c in column_names("predictions"))
    return (
        "BEGIN TRANSACTION;\n"  # noqa: S608 -- identifiers validated by table_id() and back-quoted; values are query parameters
        f"DELETE FROM {live} WHERE `as_of_date` = @run_date;\n"
        f"INSERT INTO {live} ({columns})\n"
        f"  SELECT {columns} FROM {{staging}} WHERE `as_of_date` = @run_date;\n"
        "COMMIT TRANSACTION;\n"
    )


def publish(client, scoring_run_id: str, run_date: date) -> None:
    from google.cloud import bigquery

    script = publish_script().replace("{staging}", quoted(staging_table_id(scoring_run_id)))
    parameters = [bigquery.ScalarQueryParameter("run_date", "DATE", run_date)]
    # A fixed job id (resolve_job looks it up after a client-side error) cannot be retried under the same id: job_retry=None.
    job = client.query(
        script,
        job_config=query_config(query_parameters=parameters),
        job_id=publish_job_id(scoring_run_id),
        job_retry=None,
    )
    job.result()  # no client timeout


def resolve_job(
    client,
    job_id: str,
    *,
    poll_seconds: float = 2.0,
    max_wait_seconds: float = 300.0,
    sleep: Callable[[float], None] | None = None,
    monotonic: Callable[[], float] | None = None,
) -> bool | None:
    """What really happened to a job whose .result() raised client-side: True = DONE without error, False = DONE with an
    error OR never created (NotFound: the id is caller-chosen and nothing re-inserts it), None = could not be observed
    within the budget."""
    from google.api_core.exceptions import NotFound

    sleep, monotonic = sleep or time.sleep, monotonic or time.monotonic  # resolved per call, so tests can patch time
    deadline = monotonic() + max_wait_seconds
    while True:
        try:
            job = client.get_job(job_id)
        except NotFound:
            return False
        except Exception:  # noqa: BLE001 -- any error is retried within the wait budget
            job = None
        if job is not None and job.state == "DONE":
            return job.error_result is None
        if monotonic() >= deadline:
            return None
        sleep(poll_seconds)


def publish_landed(client, scoring_run_id: str, run_date: date) -> bool | None:
    """Only when resolve_job could not observe the publish job: did this run's rows land? A count > 0 means the
    all-or-nothing transaction committed; 0 means unknown (it may not have committed YET), never False."""
    from google.cloud import bigquery

    sql = (
        f"SELECT COUNT(*) AS n FROM {quoted(predictions_table_id())} "  # noqa: S608 -- identifier validated by table_id() and back-quoted; values are query parameters
        "WHERE `as_of_date` = @run_date AND `scoring_run_id` = @scoring_run_id"
    )
    parameters = [
        bigquery.ScalarQueryParameter("run_date", "DATE", run_date),
        bigquery.ScalarQueryParameter("scoring_run_id", "STRING", scoring_run_id),
    ]
    try:
        rows = list(client.query(sql, job_config=query_config(query_parameters=parameters)).result())
    except Exception:  # noqa: BLE001 -- a failed check means "unknown", not an error of the run
        return None
    return True if rows and rows[0]["n"] > 0 else None


def drop_staging(client, scoring_run_id: str) -> None:
    client.delete_table(staging_table_id(scoring_run_id), not_found_ok=True)
