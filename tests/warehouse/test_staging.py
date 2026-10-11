"""Write-audit-publish (D5): the staging writer, the publish transaction, and how a client-side error after a job was
submitted is resolved (resolve_job / publish_landed)."""

from __future__ import annotations

import io
import re
from datetime import date

import pyarrow.parquet as pq
import pytest
from google.api_core import exceptions as api
from google.cloud import bigquery

from readmission_risk.warehouse import staging
from readmission_risk.warehouse.bigquery import column_names
from tests.scoring.helpers import make_predictions
from tests.warehouse.fake_bigquery import FakeBigQuery, FakeJob

RUN_ID = "0123456789abcdef0123456789abcdef"
RUN_DATE = date(2026, 6, 30)


def test_staging_writer_validates_before_any_call():
    fake = FakeBigQuery()
    writer = staging.make_staging_writer(fake, RUN_ID)
    bad = make_predictions().drop(columns=["risk_score"])
    with pytest.raises(ValueError, match="exactly the columns"):
        writer(bad, None, RUN_DATE)
    with pytest.raises(ValueError, match="as_of_date"):
        writer(make_predictions(), None, date(2026, 7, 1))  # rows belong to another run date
    assert fake.calls == []


def test_staging_writer_creates_partitioned_table_then_loads_decorator_write_truncate():
    fake = FakeBigQuery()
    predictions = make_predictions()
    result = staging.make_staging_writer(fake, RUN_ID)(predictions, None, RUN_DATE)
    staging_id = f"synthea-readmission-risk.scratch.predictions_{RUN_ID}"
    assert result == f"bigquery://{staging_id}$20260630"
    assert fake.kinds() == ["create_table", "load"]
    table = fake.calls[0][1]
    assert (table.project, table.dataset_id, table.table_id) == tuple(staging_id.split("."))
    assert table.time_partitioning.field == "as_of_date" and table.time_partitioning.type_ == "DAY"
    assert [f.name for f in table.schema] == column_names("predictions")
    _, destination, config = fake.calls[1]
    assert destination == f"{staging_id}$20260630"
    assert config.source_format == bigquery.SourceFormat.PARQUET
    assert config.write_disposition == bigquery.WriteDisposition.WRITE_TRUNCATE
    assert config.create_disposition == bigquery.CreateDisposition.CREATE_NEVER
    loaded = pq.read_table(io.BytesIO(fake.loaded[destination]))
    assert loaded.column_names == column_names("predictions")
    assert loaded.schema.field("scoring_run_id").nullable is False  # Parquet REQUIRED, matching the table
    assert set(loaded.column("scoring_run_id").to_pylist()) == {RUN_ID}
    assert loaded.drop(["scoring_run_id"]).to_pandas()["encounter_id"].tolist() == predictions["encounter_id"].tolist()


def test_staging_writer_row_count_postcondition():
    fake = FakeBigQuery(load_output_rows=1)
    with pytest.raises(RuntimeError, match="staging load wrote 1 rows"):
        staging.make_staging_writer(fake, RUN_ID)(make_predictions(), None, RUN_DATE)


def test_publish_is_one_transaction_delete_then_insert_with_explicit_columns():
    fake = FakeBigQuery()
    staging.publish(fake, RUN_ID, RUN_DATE)
    [(kind, sql, config, job_id)] = fake.calls
    assert kind == "query" and job_id == f"publish_{RUN_ID}"
    statements = [s.strip() for s in sql.split(";") if s.strip()]
    assert statements[0] == "BEGIN TRANSACTION" and statements[-1] == "COMMIT TRANSACTION"
    live = "`synthea-readmission-risk.serving.predictions`"
    assert statements[1] == f"DELETE FROM {live} WHERE `as_of_date` = @run_date"  # noqa: S608 -- the expected SQL text, not a query
    columns = ", ".join(f"`{c}`" for c in column_names("predictions"))
    assert statements[2] == (
        f"INSERT INTO {live} ({columns})\n  SELECT {columns} FROM "  # noqa: S608 -- the expected SQL text, not a query
        f"`synthea-readmission-risk.scratch.predictions_{RUN_ID}` WHERE `as_of_date` = @run_date"
    )
    assert "`partition`" in columns and len(column_names("predictions")) == 19
    assert config.maximum_bytes_billed == 100 * 1024**2
    assert [(p.name, p.type_, p.value) for p in config.query_parameters] == [("run_date", "DATE", RUN_DATE)]


def test_no_client_timeouts():
    from pathlib import Path

    src = Path(staging.__file__).parent
    offenders = [p.name for p in src.glob("*.py") if re.search(r"\.result\(\s*timeout", p.read_text())]
    assert offenders == []


class _Clock:
    def __init__(self):
        self.now, self.sleeps = 0.0, []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def test_resolve_job_waits_for_done():
    clock = _Clock()
    fake = FakeBigQuery(jobs={"j": [FakeJob(state="RUNNING"), FakeJob(state="RUNNING"), FakeJob(state="DONE")]})
    assert staging.resolve_job(fake, "j", sleep=clock.sleep, monotonic=clock.monotonic) is True
    assert clock.sleeps == [2.0, 2.0]

    failed = FakeBigQuery(jobs={"j": [FakeJob(state="DONE", error_result={"reason": "invalidQuery"})]})
    assert staging.resolve_job(failed, "j", sleep=clock.sleep, monotonic=clock.monotonic) is False

    # never created: NotFound means False at once, no waiting
    clock = _Clock()
    assert staging.resolve_job(FakeBigQuery(), "missing", sleep=clock.sleep, monotonic=clock.monotonic) is False
    assert clock.sleeps == []

    # never DONE within the budget -> None
    clock = _Clock()
    stuck = FakeBigQuery(jobs={"j": [FakeJob(state="RUNNING")]})
    assert staging.resolve_job(stuck, "j", max_wait_seconds=10, sleep=clock.sleep, monotonic=clock.monotonic) is None
    assert sum(clock.sleeps) >= 10

    # get_job failing throughout -> None (retried within the budget)
    clock = _Clock()
    broken = FakeBigQuery(get_job_error=api.ServiceUnavailable("503"))
    assert staging.resolve_job(broken, "j", max_wait_seconds=6, sleep=clock.sleep, monotonic=clock.monotonic) is None
    assert broken.kinds().count("get_job") >= 3


def test_publish_landed_is_true_only_on_a_positive_count():
    def answer(n):
        return FakeBigQuery(on_query=lambda sql, config, job_id: FakeJob(rows=[{"n": n}]))

    assert staging.publish_landed(answer(5), RUN_ID, RUN_DATE) is True
    assert staging.publish_landed(answer(0), RUN_ID, RUN_DATE) is None  # not yet committed is possible: unknown
    broken = FakeBigQuery(on_query=lambda sql, config, job_id: FakeJob(raise_on_result=api.Forbidden("403")))
    assert staging.publish_landed(broken, RUN_ID, RUN_DATE) is None
    fake = answer(1)
    staging.publish_landed(fake, RUN_ID, RUN_DATE)
    sql, config = fake.calls[0][1], fake.calls[0][2]
    assert "`scoring_run_id` = @scoring_run_id" in sql and "`as_of_date` = @run_date" in sql
    assert {p.name for p in config.query_parameters} == {"run_date", "scoring_run_id"}


def test_drop_staging_is_not_found_ok():
    fake = FakeBigQuery()
    staging.drop_staging(fake, RUN_ID)
    assert fake.calls == [("delete_table", f"synthea-readmission-risk.scratch.predictions_{RUN_ID}", True)]
