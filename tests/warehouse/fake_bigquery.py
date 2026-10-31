"""An in-memory stand-in for google.cloud.bigquery.Client, wide enough for readmission_risk.warehouse: it records every
call with its config, returns scripted job outcomes, and refuses the two job kinds the design must not use (copy jobs and
load_table_from_json into serving: D5/D6). A test scripts query outcomes with `on_query(sql, config, job_id)`."""

from __future__ import annotations

import io
from collections.abc import Callable
from dataclasses import dataclass, field

import pyarrow.parquet as pq
from google.api_core import exceptions as api


@dataclass
class FakeJob:
    rows: list = field(default_factory=list)
    output_rows: int | None = None
    num_dml_affected_rows: int | None = 1
    state: str = "DONE"
    error_result: dict | None = None
    raise_on_result: BaseException | None = None
    total_bytes_billed: int | None = None

    def result(self, *args, **kwargs):
        assert "timeout" not in kwargs, "no client-side timeouts (the job's final state decides)"
        if self.raise_on_result is not None:
            raise self.raise_on_result
        return list(self.rows)


@dataclass
class FakeBigQuery:
    on_query: Callable[[str, object, str | None], FakeJob] | None = None
    jobs: dict[str, list[FakeJob]] = field(default_factory=dict)  # job_id -> successive get_job answers
    get_job_error: BaseException | None = None
    calls: list[tuple] = field(default_factory=list)
    loaded: dict[str, bytes] = field(default_factory=dict)  # destination -> Parquet bytes
    tables: set[str] = field(default_factory=set)
    load_output_rows: int | None = None  # None -> the Parquet row count
    raise_on_create: BaseException | None = None
    raise_on_delete: BaseException | None = None
    # v3 slice 1 (the gold publish): load jobs from GCS URIs, table metadata and tabledata.list, all scripted by the test
    on_load_uri: Callable[[list, str, object, str | None], FakeJob] | None = None
    table_objects: dict[str, object] = field(default_factory=dict)  # table id -> a real google.cloud.bigquery.Table
    frames: dict[str, object] = field(default_factory=dict)  # table id -> pyarrow.Table returned by list_rows(...).to_arrow()
    raise_on_get_table: BaseException | None = None
    raise_on_list_rows: BaseException | None = None

    def query(self, sql, job_config=None, job_id=None, **kwargs):
        if job_id is not None:  # google-cloud-bigquery: a fixed job id needs job_retry=None (a FutureWarning, later an error)
            assert "job_retry" in kwargs and kwargs["job_retry"] is None, "a fixed job_id needs job_retry=None"
        self.calls.append(("query", sql, job_config, job_id))
        if self.on_query is not None:
            return self.on_query(sql, job_config, job_id)
        return FakeJob()

    def create_table(self, table, exists_ok=False):
        self.calls.append(("create_table", table))
        if self.raise_on_create is not None:
            raise self.raise_on_create
        name = f"{table.project}.{table.dataset_id}.{table.table_id}"
        if name in self.tables and not exists_ok:
            raise api.Conflict(f"409 Already Exists: Table {name}")
        self.tables.add(name)
        return table

    def load_table_from_file(self, file_obj, destination, rewind=False, job_config=None):
        if rewind:
            file_obj.seek(0)
        data = file_obj.read()
        self.calls.append(("load", destination, job_config))
        self.loaded[destination] = data
        n = pq.read_table(io.BytesIO(data)).num_rows
        return FakeJob(output_rows=n if self.load_output_rows is None else self.load_output_rows)

    def load_table_from_uri(self, source_uris, destination, job_id=None, job_config=None, **kwargs):
        self.calls.append(("load_uri", list(source_uris), destination, job_config, job_id))
        if self.on_load_uri is not None:
            return self.on_load_uri(list(source_uris), destination, job_config, job_id)
        return FakeJob()

    def get_table(self, table):
        name = _table_name(table)
        self.calls.append(("get_table", name))
        if self.raise_on_get_table is not None:
            raise self.raise_on_get_table
        if name not in self.table_objects:
            raise api.NotFound(f"404 Not found: Table {name}")
        return self.table_objects[name]

    def list_rows(self, table, **kwargs):
        name = _table_name(table)
        self.calls.append(("list_rows", name, kwargs))
        if self.raise_on_list_rows is not None:
            raise self.raise_on_list_rows
        if name not in self.frames:
            raise api.NotFound(f"404 Not found: Table {name}")
        return _Rows(self.frames[name])

    def delete_table(self, table, not_found_ok=False):
        self.calls.append(("delete_table", table, not_found_ok))
        if self.raise_on_delete is not None:
            raise self.raise_on_delete
        self.tables.discard(table)

    def get_job(self, job_id, *args, **kwargs):
        self.calls.append(("get_job", job_id))
        if self.get_job_error is not None:
            raise self.get_job_error
        answers = self.jobs.get(job_id)
        if not answers:
            raise api.NotFound(f"404 Not found: Job {job_id}")
        return answers.pop(0) if len(answers) > 1 else answers[0]

    def copy_table(self, *args, **kwargs):  # pragma: no cover - must never be called
        raise AssertionError("copy jobs need dataset-level tables.create on serving (D6): publish is DML")

    def load_table_from_json(self, *args, **kwargs):  # pragma: no cover - must never be called
        raise AssertionError("scoring_runs is written by a DML INSERT, not a load job (D6)")

    def kinds(self) -> list[str]:
        return [c[0] for c in self.calls]


def _table_name(table) -> str:
    if isinstance(table, str):
        return table
    return f"{table.project}.{table.dataset_id}.{table.table_id}"


@dataclass
class _Rows:
    arrow: object

    def to_arrow(self, create_bqstorage_client=True, **kwargs):
        assert create_bqstorage_client is False, "the gold publish reads rows over REST (no bigquery-storage pin)"
        return self.arrow


def assertion_error(description: str) -> api.BadRequest:
    """What a failing ASSERT looks like client-side (the shape the L0 probe records)."""
    return api.BadRequest(description, errors=[{"reason": "invalidQuery", "message": description}])
