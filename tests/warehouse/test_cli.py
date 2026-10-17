"""The thin CLIs of v2 slice 4: scripts/score_to_bigquery.py (exit codes, printed lines, job summary) and
scripts/seeded_data_test_check.py (partial results before ERROR). The orchestration itself is tested in test_run.py."""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import pytest
from google.api_core import exceptions as api
from typer.testing import CliRunner

from readmission_risk.warehouse.data_tests import DataTestResult
from readmission_risk.warehouse.run import BigQueryRunResult
from readmission_risk.warehouse.seeded_check import SeededCheckError
from tests.warehouse.test_scoring_runs import make_record

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"{name}_cli", _SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def cli():
    return _load("score_to_bigquery")


def _result(*, recorded=True, error=None, **record) -> BigQueryRunResult:
    return BigQueryRunResult(record=make_record(**record), scoring=None, data_tests=(), error=error, recorded=recorded)


def _invoke(cli, monkeypatch, result, tmp_path, *extra):
    seen = []
    monkeypatch.setattr(cli, "score_to_bigquery", lambda request: seen.append(request) or result)
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    out = CliRunner().invoke(cli.app, ["--gold-dir", "gs://g/full_run_2b", "--run-date", "20250630", *extra])
    return out, seen, summary


def test_succeeded_exit_0_prints_run_line_and_summary(cli, monkeypatch, tmp_path):
    out, seen, summary = _invoke(cli, monkeypatch, _result(), tmp_path, "--top-n", "5", "--bundle", "gs://b/x")
    assert out.exit_code == 0, out.output
    assert f"Scoring run {'r' * 32}: succeeded (published: yes; recorded: yes)" in out.stdout
    request = seen[0]
    assert (request.gold_uri, request.run_date, request.top_n, request.bundle_uri) == ("gs://g/full_run_2b", "20250630", 5, "gs://b/x")
    assert summary.read_text() == (
        "## Scoring run succeeded\n\n| field | value |\n|---|---|\n"
        "| run date | 2025-06-30 |\n"
        f"| scoring run id | {'r' * 32} |\n"
        "| bundle | b |\n"
        "| rows / Top-N | 12 / 10 |\n"
        "| data tests | history pass, predictions pass |\n"
        "| published | yes |\n"
        "| recorded | yes |\n"
        "| failure | - |\n"
    )


def test_refused_exit_1(cli, monkeypatch, tmp_path):
    result = _result(status="refused", published=False, error=ValueError("window contains in-sample rows"),
                     error_type="ValueError", error_message="window contains in-sample rows", run_date=None,
                     history_tests_passed=None, predictions_tests_passed=None)  # fmt: skip
    out, _, summary = _invoke(cli, monkeypatch, result, tmp_path)
    assert out.exit_code == 1 and "ERROR: window contains in-sample rows" in out.stderr
    assert "| run date | - |" in summary.read_text()
    assert "| data tests | history not run, predictions not run |" in summary.read_text()


def test_data_tests_failed_exit_3(cli, monkeypatch, tmp_path):
    result = _result(status="data_tests_failed", published=False, predictions_tests_passed=False,
                     data_test_failure="PRED-04: a risk_score is outside [0, 1], NaN or null")  # fmt: skip
    out, _, summary = _invoke(cli, monkeypatch, result, tmp_path)
    assert out.exit_code == 3 and "DATA TESTS FAILED: PRED-04" in out.stderr
    assert "| failure | PRED-04: a risk_score is outside [0, 1], NaN or null |" in summary.read_text()


def test_succeeded_but_not_recorded_exit_4(cli, monkeypatch, tmp_path):
    out, _, _ = _invoke(cli, monkeypatch, _result(recorded=False, error=api.Forbidden("403 denied")), tmp_path)
    assert out.exit_code == 4 and "ERROR: not recorded: 403" in out.stderr and "denied" in out.stderr
    assert "recorded: no" in out.stdout


def test_not_recorded_keeps_the_status_exit_code(cli, monkeypatch, tmp_path):
    result = _result(status="data_tests_failed", recorded=False, data_test_failure="PRED-01: x", published=False)
    out, _, _ = _invoke(cli, monkeypatch, result, tmp_path)
    assert out.exit_code == 3


def test_failed_reraises_the_error(cli, monkeypatch, tmp_path):
    result = _result(status="failed", error=RuntimeError("staging load wrote 1 rows"), error_type="RuntimeError",
                     error_message="staging load wrote 1 rows", published=False)  # fmt: skip
    out, _, summary = _invoke(cli, monkeypatch, result, tmp_path)
    assert out.exit_code == 1 and isinstance(out.exception, RuntimeError)
    assert "| failure | RuntimeError: staging load wrote 1 rows |" in summary.read_text()


def test_bad_impersonation_email_is_an_unrecorded_error(cli, monkeypatch, tmp_path):
    out, seen, _ = _invoke(cli, monkeypatch, _result(), tmp_path, "--impersonate-service-account", "nobody")
    assert out.exit_code == 1 and "service-account email" in out.stderr and seen == []


def test_destination_never_names_staging(cli):
    assert cli._destination(_result()) == (
        "bigquery://synthea-readmission-risk.serving.predictions (as_of_date = 2025-06-30)"
    )
    assert cli._destination(_result(published=False)).startswith("nothing (not published")


def test_seeded_cli_prints_partial_results_before_error(monkeypatch):
    seeded = _load("seeded_data_test_check")
    partial = {"predictions": {"clean": DataTestResult("predictions", True, None)}, "scoring_runs": {}}

    def fail(client, run_date):
        raise SeededCheckError("the PRED-01 mutation should fail with PRED-01; it passed", partial)

    monkeypatch.setattr(seeded, "seeded_check", fail)
    monkeypatch.setattr("readmission_risk.gcp.bigquery_client", lambda: object())
    out = CliRunner().invoke(seeded.app, ["--run-date", "20250630"])
    assert out.exit_code == 1
    assert "predictions   clean  : pass" in out.stdout and "ERROR: the PRED-01 mutation" in out.stderr


def test_seeded_cli_ok(monkeypatch):
    seeded = _load("seeded_data_test_check")
    passed, failed = DataTestResult("p", True, None), DataTestResult("p", False, "PRED-01: x")
    results = {"predictions": {"clean": passed, "PRED-01": failed}, "scoring_runs": {"clean": passed}}
    seen = []
    monkeypatch.setattr(seeded, "seeded_check", lambda client, run_date: seen.append(run_date) or results)
    monkeypatch.setattr("readmission_risk.gcp.bigquery_client", lambda: object())
    out = CliRunner().invoke(seeded.app, ["--run-date", "20250630"])
    assert out.exit_code == 0 and "M5 OK: all 1 assertions fired" in out.stdout and seen == [date(2025, 6, 30)]
    assert "predictions   PRED-01: fail PRED-01: x" in out.stdout
