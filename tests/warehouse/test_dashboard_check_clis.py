"""The two dashboard check CLIs (v3 slice 2, Verification V-C): always impersonate, exit codes 0/2/3/5, report keys, and no
BigQuery call when the local gold cannot be used."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from readmission_risk.warehouse.bigquery import SERVING_GOLD_FINGERPRINT
from readmission_risk.warehouse.dashboard_query_check import (
    CheckResult,
    QueryCheckReport,
)
from readmission_risk.warehouse.gold_access_check import ProbeResult

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
DEFAULT_SA = "dashboard-runtime@synthea-readmission-risk.iam.gserviceaccount.com"


def _load(name: str, monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location(f"{name}_cli", SCRIPTS / f"{name}.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.setattr(cli, "REPORT_DIR", tmp_path / "reports")
    return cli


def _report(tmp_path) -> dict:
    (path,) = (tmp_path / "reports").glob("*.json")
    return json.loads(path.read_text())


@pytest.mark.parametrize(("outcome", "code"), [("denied", 0), ("allowed", 3), ("failed", 3), ("error", 5)])
def test_access_cli_exit_codes_and_report(monkeypatch, tmp_path, outcome, code):
    cli = _load("dashboard_access_check", monkeypatch, tmp_path)
    seen = {}

    def fake(make_client, *, dashboard_sa):
        seen["sa"] = dashboard_sa
        return [ProbeResult(dashboard_sa, "p", outcome, "d")]

    monkeypatch.setattr(cli, "run_access_checks", fake)
    out = CliRunner().invoke(cli.app, [])
    assert out.exit_code == code, out.output
    assert seen["sa"] == DEFAULT_SA  # always impersonates, by default dashboard-runtime
    report = _report(tmp_path)
    assert set(report) == {"checked_at", "dashboard_sa", "probes"} and report["dashboard_sa"] == DEFAULT_SA


def test_access_cli_refuses_an_empty_sa(monkeypatch, tmp_path):
    cli = _load("dashboard_access_check", monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "run_access_checks", lambda *a, **k: pytest.fail("must not run"))
    assert CliRunner().invoke(cli.app, ["--dashboard-sa", " "]).exit_code == 2


def _query_cli(monkeypatch, tmp_path, *, fingerprint=SERVING_GOLD_FINGERPRINT, load_error=None, client_error=None, report=None):
    cli = _load("dashboard_query_check", monkeypatch, tmp_path)
    calls = []

    def load(gold_dir):
        if load_error:
            raise load_error
        return "GOLD", SimpleNamespace(gold_fingerprint=fingerprint)

    def make_client(email):
        calls.append(("client", email))
        if client_error:
            raise client_error
        return "CLIENT"

    def run(client, *, gold):
        calls.append(("run", client, gold))
        return report or QueryCheckReport([CheckResult("Q0", "pass", "ok")], {"clock_sim_date": "2023-10-30"})

    monkeypatch.setattr(cli, "load_gold_table_with_metadata", load)
    monkeypatch.setattr(cli, "make_client", make_client)
    monkeypatch.setattr(cli, "run_query_checks", run)
    return cli, calls


def test_query_cli_passes_and_reports(monkeypatch, tmp_path):
    cli, calls = _query_cli(monkeypatch, tmp_path)
    out = CliRunner().invoke(cli.app, [])
    assert out.exit_code == 0, out.output
    assert calls == [("client", DEFAULT_SA), ("run", "CLIENT", "GOLD")]
    assert "measurement clock_sim_date: 2023-10-30" in out.output
    report = _report(tmp_path)
    assert set(report) == {"checked_at", "dashboard_sa", "gold_dir", "checks", "measurements"}
    assert report["gold_dir"] == "data/gold/main_200k" and report["measurements"] == {"clock_sim_date": "2023-10-30"}


@pytest.mark.parametrize(("outcome", "code"), [("fail", 3), ("error", 5)])
def test_query_cli_fail_and_error(monkeypatch, tmp_path, outcome, code):
    cli, _ = _query_cli(monkeypatch, tmp_path, report=QueryCheckReport([CheckResult("Q0", outcome, "x")]))
    assert CliRunner().invoke(cli.app, []).exit_code == code


@pytest.mark.parametrize(
    "kwargs", [{"load_error": FileNotFoundError("no gold")}, {"fingerprint": "205c4c64"}], ids=["unloadable", "other gold"]
)
def test_query_cli_exits_2_without_a_bigquery_call(monkeypatch, tmp_path, kwargs):
    cli, calls = _query_cli(monkeypatch, tmp_path, **kwargs)
    out = CliRunner().invoke(cli.app, [])
    assert out.exit_code == 2, out.output
    assert calls == [] and not (tmp_path / "reports").exists()


def test_query_cli_client_build_failure_is_exit_5_with_a_report(monkeypatch, tmp_path):
    cli, calls = _query_cli(monkeypatch, tmp_path, client_error=RuntimeError("no ADC"))
    out = CliRunner().invoke(cli.app, [])
    assert out.exit_code == 5, out.output
    assert [c[0] for c in calls] == ["client"]
    assert _report(tmp_path)["checks"] == [{"check": "build client", "outcome": "error", "detail": "RuntimeError: no ADC"}]


def test_query_cli_refuses_an_empty_sa(monkeypatch, tmp_path):
    cli, calls = _query_cli(monkeypatch, tmp_path)
    assert CliRunner().invoke(cli.app, ["--dashboard-sa", ""]).exit_code == 2 and calls == []
