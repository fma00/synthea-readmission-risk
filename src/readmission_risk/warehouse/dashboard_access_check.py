"""Live proof of the dashboard runtime SA's limits (v3 slice 2, PRD FR9/V8; slice 1's gold_access_check pattern). As
dashboard-runtime, each probe must be DENIED: reading any serving table directly, creating a view in `dashboard` (every view
there may read all of serving, labels included), and listing the scratch datasets (they hold labelled staging copies). Each
view must be a VIEW whose columns are exactly the designed ones, none of them a label or demographic column. See
notes/eg-new-feature/v3-slice2-authorized-views-2026-10-31.md (Interfaces section 6).
"""

from __future__ import annotations

from collections.abc import Callable

from .bigquery import (
    DASHBOARD_DATASET,
    SCRATCH_DATASET,
    SCRATCH_GOLD_DATASET,
    SERVING_DATASET,
    query_config,
    quoted,
    table_id,
)
from .dashboard_views import FORBIDDEN_OUTPUT_COLUMNS, VIEW_COLUMNS, VIEW_NAMES
from .gold_access_check import ProbeResult, check, probe

PROBE_VIEW = "access_probe"
# Each base-table read carries a constant partition filter, so a missing-filter error cannot mask the access answer.
_BASE_TABLE_READS = (
    ("predictions", "`as_of_date` = DATE '1900-01-01'"),
    ("gold", "`index_stop` >= TIMESTAMP '0001-01-01'"),
    ("scoring_runs", "`started_at` >= TIMESTAMP '2000-01-01'"),
    ("monitoring", "`started_at` >= TIMESTAMP '2000-01-01'"),
)


def _view_check(client, view: str) -> Callable[[], str | None]:
    def run() -> str | None:
        table = client.get_table(table_id(DASHBOARD_DATASET, view))
        names = [field.name for field in table.schema]
        if table.table_type != "VIEW":
            return f"table_type is {table.table_type!r}, expected 'VIEW'"
        forbidden = sorted(set(names) & FORBIDDEN_OUTPUT_COLUMNS)
        if forbidden:
            return f"exposes forbidden column(s) {forbidden}"
        if names != list(VIEW_COLUMNS[view]):
            return f"columns {names} != designed {list(VIEW_COLUMNS[view])}"
        return None

    return run


def run_access_checks(make_client: Callable[[str], object], *, dashboard_sa: str) -> list[ProbeResult]:
    """Every probe's and check's result; a client that cannot be built is one "error" result, never a raise."""
    from google.cloud import bigquery

    try:
        client = make_client(dashboard_sa)
    except Exception as exc:  # noqa: BLE001 -- e.g. no ADC: recorded
        return [ProbeResult(dashboard_sa, "build client", "error", f"{type(exc).__name__}: {exc}"[:500])]
    results = []
    for table, predicate in _BASE_TABLE_READS:
        sql = f"SELECT COUNT(*) AS n FROM {quoted(table_id(SERVING_DATASET, table))} WHERE {predicate}"  # noqa: S608 -- static identifiers and literals
        results.append(probe(dashboard_sa, f"read serving.{table}", lambda sql=sql: client.query(sql, job_config=query_config()).result()))
    view_id = table_id(DASHBOARD_DATASET, PROBE_VIEW)

    def create_view() -> None:
        view = bigquery.Table(view_id)
        view.view_query = "SELECT 1 AS x"
        client.create_table(view)

    results.append(
        probe(dashboard_sa, "create a view in dashboard", create_view, lambda: client.delete_table(view_id, not_found_ok=True))
    )
    for dataset in (SCRATCH_DATASET, SCRATCH_GOLD_DATASET):
        results.append(probe(dashboard_sa, f"list {dataset}", lambda dataset=dataset: list(client.list_tables(dataset))))
    for view in VIEW_NAMES:
        results.append(check(dashboard_sa, f"schema of dashboard.{view}", _view_check(client, view)))
    return results
