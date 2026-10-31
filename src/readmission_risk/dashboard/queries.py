"""The dashboard's fixed queries (v3 slice 2, PRD FR8/FR15): each reads only the authorized views, constrains every
filter-required partition column with a query parameter (never CURRENT_DATE/CURRENT_TIMESTAMP: they defeat pruning and the
result cache), carries maximum_bytes_billed through warehouse.bigquery.query_config and the job label component=dashboard.
google.cloud is imported inside config_kwargs only. See notes/eg-new-feature/v3-slice2-authorized-views-2026-10-31.md
(Interfaces section 5).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
from datetime import date, datetime

from readmission_risk.scoring.selection import window_bounds
from readmission_risk.warehouse.bigquery import (
    DASHBOARD_DATASET,
    SERVING_GOLD_FINGERPRINT,
    query_config,
    quoted,
    table_id,
)
from readmission_risk.warehouse.dashboard_views import (
    CALL_SHEET_COLUMNS,
    MONITORING_COLUMNS,
    SCORING_RUNS_COLUMNS,
)

DASHBOARD_LABELS = {"component": "dashboard"}  # FR15 job label
# Constant started_at lower bound for every ops read. 2026-10-01 is before the first BigQuery scoring run (2026-10-11, v2 slice 4),
# so the run history keeps it; deploy/monitoring.json's epoch (2026-10-18) is later and only for the monitor's own reads
# (tests/dashboard/test_queries.py pins OPS_EPOCH <= it).
OPS_EPOCH = date(2026, 10, 1)
PUBLISHED_DATES_LIMIT = 500  # the monitoring grid has 178 tick dates; the live check fails if the limit is reached
SUCCEEDED = "succeeded"


@dataclass(frozen=True)
class DashboardQuery:
    name: str
    sql: str
    parameters: tuple[tuple[str, str, object], ...]  # (name, BigQuery type, value)


@dataclass(frozen=True)
class PublishedDate:
    """One entry of the call sheet's date picker: the newest published run of that date (owner decisions D1, D5)."""

    run_date: date
    scoring_run_id: str
    model_bundle_id: str
    window_days: int
    top_n: int
    n_predictions: int
    clock_sim_date: date

    @classmethod
    def from_row(cls, row: Mapping) -> PublishedDate:
        """KeyError on a missing key; ValueError if a value is None."""
        values = {f.name: row[f.name] for f in fields(cls)}
        missing = sorted(k for k, v in values.items() if v is None)
        if missing:
            raise ValueError(f"published date row has NULL {missing}")
        return cls(**values)


def _view(name: str) -> str:
    return quoted(table_id(DASHBOARD_DATASET, name))


def _limit(limit: int) -> int:
    if type(limit) is not int or limit < 1:
        raise ValueError(f"limit must be an int >= 1; got {limit!r}")
    return limit


def _published_dates_query(*, clock_bound: bool, fingerprint_filter: bool, limit: int) -> DashboardQuery:
    """published_dates() is this with both flags True. clock_bound=False drops the clock CTEs, the run_date <= clock predicate,
    the clock_sim_date column and the succeeded parameter; fingerprint_filter=False drops the fingerprint predicate and its
    parameter and returns gold_fingerprint as the last column. The live check uses the variants as positive controls."""
    ctes = []
    if clock_bound:
        ctes.append(
            "ticks AS (\n"  # noqa: S608 -- identifiers validated by table_id() and back-quoted; values are query parameters
            "  SELECT sim_date, model_bundle_id, started_at, monitoring_run_id\n"
            f"  FROM {_view('v_monitoring')}\n"
            "  WHERE started_at >= TIMESTAMP(@epoch) AND status = @succeeded AND sim_date IS NOT NULL AND model_bundle_id IS NOT NULL\n"
            "), monitored_bundle AS (\n"
            "  SELECT model_bundle_id FROM ticks ORDER BY started_at DESC, monitoring_run_id DESC LIMIT 1\n"
            "), clock AS (\n"
            "  SELECT MAX(t.sim_date) AS sim_date FROM ticks AS t JOIN monitored_bundle AS b USING (model_bundle_id)\n"
            ")"
        )
    ctes.append(
        "latest AS (\n"  # noqa: S608 -- identifiers validated by table_id() and back-quoted; values are query parameters
        "  SELECT run_date, scoring_run_id, model_bundle_id, window_days, top_n, n_predictions, gold_fingerprint,\n"
        "    ROW_NUMBER() OVER (PARTITION BY run_date ORDER BY started_at DESC, scoring_run_id DESC) AS rn\n"
        f"  FROM {_view('v_scoring_runs')}\n"
        "  WHERE started_at >= TIMESTAMP(@epoch) AND published IS TRUE AND run_date IS NOT NULL\n"
        "    AND model_bundle_id IS NOT NULL AND window_days IS NOT NULL AND top_n IS NOT NULL AND n_predictions IS NOT NULL\n"
        "    AND scoring_run_id IS NOT NULL\n"
        ")"
    )
    columns = ["l.run_date", "l.scoring_run_id", "l.model_bundle_id", "l.window_days", "l.top_n", "l.n_predictions"]
    predicates = ["l.rn = 1"]
    source = "latest AS l"
    parameters: list[tuple[str, str, object]] = [("epoch", "DATE", OPS_EPOCH)]
    if clock_bound:
        columns.append("c.sim_date AS clock_sim_date")
        predicates.append("l.run_date <= c.sim_date")
        source += " CROSS JOIN clock AS c"
        parameters.append(("succeeded", "STRING", SUCCEEDED))
    if fingerprint_filter:
        predicates.insert(1, "l.gold_fingerprint = @gold_fingerprint")
        parameters.append(("gold_fingerprint", "STRING", SERVING_GOLD_FINGERPRINT))
    else:
        columns.append("l.gold_fingerprint")
    parameters.append(("limit", "INT64", _limit(limit)))
    sql = (
        "WITH " + ", ".join(ctes) + "\n"
        f"SELECT {', '.join(columns)}\n"
        f"FROM {source}\n"
        f"WHERE {' AND '.join(predicates)}\n"
        "ORDER BY l.run_date DESC\n"
        "LIMIT @limit"
    )
    name = "published_dates" if clock_bound and fingerprint_filter else (
        f"published_dates[clock_bound={clock_bound},fingerprint_filter={fingerprint_filter}]"
    )
    return DashboardQuery(name, sql, tuple(parameters))


def published_dates(*, limit: int = PUBLISHED_DATES_LIMIT) -> DashboardQuery:
    """The call sheet's dates, newest first: per run_date the newest published run (whatever its status, D1), kept only if it
    scored the serving gold and its date is not after the simulated clock (D5). Rows parse with PublishedDate.from_row."""
    return _published_dates_query(clock_bound=True, fingerprint_filter=True, limit=limit)


def _call_sheet_variant(
    published: PublishedDate, *, discharge: tuple[datetime, datetime] | None, as_of: bool
) -> DashboardQuery:
    """call_sheet() is this with the scorer's window and as_of=True; discharge=None / as_of=False omit that predicate and its
    parameters (the live check's filter-required and pruning probes)."""
    predicates, parameters = [], []
    if as_of:
        predicates.append("as_of_date = @as_of_date")
        parameters.append(("as_of_date", "DATE", published.run_date))
    if discharge is not None:
        predicates += ["discharge_time >= @discharge_from", "discharge_time < @discharge_to"]
        parameters += [("discharge_from", "TIMESTAMP", discharge[0]), ("discharge_to", "TIMESTAMP", discharge[1])]
    where = f"WHERE {' AND '.join(predicates)}\n" if predicates else ""
    sql = f"SELECT {', '.join(CALL_SHEET_COLUMNS)}\nFROM {_view('v_call_sheet')}\n{where}ORDER BY rank"
    name = "call_sheet" if as_of and discharge is not None else f"call_sheet[discharge={discharge is not None},as_of={as_of}]"
    return DashboardQuery(name, sql, tuple(parameters))


def discharge_window(published: PublishedDate) -> tuple[datetime, datetime]:
    """The scorer's own window for the run (scoring.selection.window_bounds), so the filter can never drop a published row."""
    start, end = window_bounds(published.run_date, published.window_days)
    return start.to_pydatetime(), end.to_pydatetime()


def call_sheet(published: PublishedDate) -> DashboardQuery:
    """The run date's published rows, by rank. No scoring_run_id predicate: each row carries its own, so a mismatch with the
    listed run is visible instead of an empty sheet (design F10)."""
    return _call_sheet_variant(published, discharge=discharge_window(published), as_of=True)


def monitoring_history(*, limit: int = 1000) -> DashboardQuery:
    sql = (
        f"SELECT {', '.join(MONITORING_COLUMNS)}\nFROM {_view('v_monitoring')}\n"
        "WHERE started_at >= TIMESTAMP(@epoch)\nORDER BY started_at DESC, monitoring_run_id DESC\nLIMIT @limit"
    )
    return DashboardQuery("monitoring_history", sql, (("epoch", "DATE", OPS_EPOCH), ("limit", "INT64", _limit(limit))))


def recent_scoring_runs(*, limit: int = 50) -> DashboardQuery:
    sql = (
        f"SELECT {', '.join(SCORING_RUNS_COLUMNS)}\nFROM {_view('v_scoring_runs')}\n"
        "WHERE started_at >= TIMESTAMP(@epoch)\nORDER BY started_at DESC, scoring_run_id DESC\nLIMIT @limit"
    )
    return DashboardQuery("recent_scoring_runs", sql, (("epoch", "DATE", OPS_EPOCH), ("limit", "INT64", _limit(limit))))


def config_kwargs(query: DashboardQuery, *, dry_run: bool = False, use_cache: bool = True) -> dict:
    """Keyword arguments for warehouse.bigquery.query_config (which adds maximum_bytes_billed). Call sites are written
    `client.query(query.sql, job_config=query_config(**config_kwargs(query, ...)))` (tests/warehouse/test_bigquery.py)."""
    from google.cloud import bigquery

    return {
        "query_parameters": [bigquery.ScalarQueryParameter(n, t, v) for n, t, v in query.parameters],
        "labels": dict(DASHBOARD_LABELS),
        "dry_run": dry_run,
        "use_query_cache": use_cache and not dry_run,
    }


def run_job(client, query: DashboardQuery, *, use_cache: bool = True) -> tuple[list[dict], object]:
    """The rows (as dicts, over REST: no BigQuery Storage client is ever built, PRD FR9) and the finished job."""
    job = client.query(query.sql, job_config=query_config(**config_kwargs(query, use_cache=use_cache)))
    return [dict(row.items()) for row in job.result()], job


def run(client, query: DashboardQuery) -> list[dict]:
    return run_job(client, query)[0]


def dry_run(client, query: DashboardQuery) -> int:
    """Bytes the query would process. No .result(): BigQuery validates a dry run at submission, so its errors ("without a
    filter", Forbidden) are raised by client.query itself."""
    job = client.query(query.sql, job_config=query_config(**config_kwargs(query, dry_run=True)))
    return job.total_bytes_processed
