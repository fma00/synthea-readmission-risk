"""The dashboard runtime SA's live access probes (v3 slice 2, Verification V-C): 7 expected-denied probes and 3 view-schema
checks, with the exit precedence of gold_access_check (allowed/failed 3 > error 5 > 0)."""

from __future__ import annotations

from types import SimpleNamespace

from google.api_core import exceptions as api

from readmission_risk.warehouse.dashboard_access_check import run_access_checks
from readmission_risk.warehouse.dashboard_views import VIEW_COLUMNS
from readmission_risk.warehouse.gold_access_check import exit_code
from tests.warehouse.fake_bigquery import FakeBigQuery, FakeJob

SA = "dashboard-runtime@x.iam.gserviceaccount.com"
PROJECT = "synthea-readmission-risk"


def _views(**overrides):
    return {
        f"{PROJECT}.dashboard.{name}": overrides.get(
            name, SimpleNamespace(table_type="VIEW", schema=[SimpleNamespace(name=c) for c in columns])
        )
        for name, columns in VIEW_COLUMNS.items()
    }


class Client(FakeBigQuery):
    allow_create: bool = False

    def create_table(self, table, exists_ok=False):
        self.calls.append(("create_table", table.table_id, table.view_query))
        if not self.allow_create:
            raise api.Forbidden("403 bigquery.tables.create denied")


def _client(**kwargs) -> Client:
    defaults = {
        "on_query": lambda sql, config, job_id: FakeJob(raise_on_result=api.Forbidden("403 Access Denied")),
        "raise_on_list_tables": api.Forbidden("403 bigquery.tables.list denied"),
        "table_objects": _views(),
    }
    return Client(**(defaults | kwargs))


def _by_probe(results):
    return {r.probe: r for r in results}


def test_all_denied_and_all_views_ok_is_exit_0():
    client = _client()
    results = run_access_checks(lambda email: client, dashboard_sa=SA)
    outcomes = {p: r.outcome for p, r in _by_probe(results).items()}
    assert outcomes == {
        "read serving.predictions": "denied", "read serving.gold": "denied", "read serving.scoring_runs": "denied",
        "read serving.monitoring": "denied", "create a view in dashboard": "denied", "list scratch": "denied",
        "list scratch_gold": "denied", "schema of dashboard.v_call_sheet": "ok", "schema of dashboard.v_scoring_runs": "ok",
        "schema of dashboard.v_monitoring": "ok",
    }  # fmt: skip
    assert exit_code(results) == 0
    sqls = [c[1] for c in client.calls if c[0] == "query"]
    assert len(sqls) == 4 and all(" WHERE " in s for s in sqls)  # every base-table read carries a partition filter
    assert all(c[2].maximum_bytes_billed for c in client.calls if c[0] == "query")
    assert ("create_table", "access_probe", "SELECT 1 AS x") in client.calls


def test_an_allowed_view_creation_is_undone_and_fails():
    client = _client()
    client.allow_create = True
    results = run_access_checks(lambda email: client, dashboard_sa=SA)
    probe = _by_probe(results)["create a view in dashboard"]
    assert (probe.outcome, probe.detail) == ("allowed", "ALLOWED (undone)")
    assert ("delete_table", f"{PROJECT}.dashboard.access_probe", True) in client.calls
    assert exit_code(results) == 3


def test_an_allowed_base_table_read_or_listing_fails():
    results = run_access_checks(lambda email: _client(on_query=lambda sql, config, job_id: FakeJob()), dashboard_sa=SA)
    assert _by_probe(results)["read serving.gold"].outcome == "allowed" and exit_code(results) == 3
    results = run_access_checks(lambda email: _client(raise_on_list_tables=None), dashboard_sa=SA)
    assert _by_probe(results)["list scratch_gold"].outcome == "allowed" and exit_code(results) == 3


def test_a_view_with_a_label_column_wrong_order_or_not_a_view_fails():
    bad = {
        "v_call_sheet": SimpleNamespace(table_type="VIEW", schema=[SimpleNamespace(name=c) for c in (*VIEW_COLUMNS["v_call_sheet"], "is_readmitted")]),
        "v_scoring_runs": SimpleNamespace(table_type="VIEW", schema=[SimpleNamespace(name=c) for c in reversed(VIEW_COLUMNS["v_scoring_runs"])]),
        "v_monitoring": SimpleNamespace(table_type="TABLE", schema=[SimpleNamespace(name=c) for c in VIEW_COLUMNS["v_monitoring"]]),
    }  # fmt: skip
    results = _by_probe(run_access_checks(lambda email: _client(table_objects=_views(**bad)), dashboard_sa=SA))
    assert "forbidden" in results["schema of dashboard.v_call_sheet"].detail
    assert "columns" in results["schema of dashboard.v_scoring_runs"].detail
    assert "table_type" in results["schema of dashboard.v_monitoring"].detail
    assert {results[f"schema of dashboard.{v}"].outcome for v in VIEW_COLUMNS} == {"failed"}
    assert exit_code(list(results.values())) == 3


def test_a_missing_view_is_an_error_and_a_client_build_failure_is_one_error():
    results = run_access_checks(lambda email: _client(table_objects={}), dashboard_sa=SA)
    assert _by_probe(results)["schema of dashboard.v_call_sheet"].outcome == "error" and exit_code(results) == 5

    def broken(email):
        raise RuntimeError("no ADC")

    results = run_access_checks(broken, dashboard_sa=SA)
    assert [(r.probe, r.outcome) for r in results] == [("build client", "error")] and exit_code(results) == 5
