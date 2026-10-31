"""The live access probes (v3 slice 1): denied / allowed / error per probe, the undo of an allowed table creation, and the
exit precedence 3 > 5 > 0."""

from __future__ import annotations

import pytest
from google.api_core import exceptions as api

from readmission_risk.warehouse.gold_access_check import (
    ProbeResult,
    exit_code,
    is_denied,
    run_access_checks,
)
from tests.warehouse.fake_bigquery import FakeBigQuery, FakeJob

GOLD_SA, SCORER_SA, MONITOR_SA = "gold@x.iam.gserviceaccount.com", "scorer@x.iam.gserviceaccount.com", "monitor@x.iam.gserviceaccount.com"


class DenyingClient(FakeBigQuery):
    def create_table(self, table, exists_ok=False):
        self.calls.append(("create_table", table))
        raise api.Forbidden("403 bigquery.tables.create denied")


def _clients(**per_sa):
    made = {}

    def make(email):
        made[email] = per_sa.get(email) or DenyingClient(
            on_query=lambda sql, config, job_id: FakeJob(raise_on_result=api.Forbidden("403 Access Denied")),
            raise_on_get_table=api.Forbidden("403 bigquery.tables.get denied"),
        )
        return made[email]

    return make, made


def test_every_probe_denied_is_exit_0():
    make, _ = _clients()
    results = run_access_checks(make, gold_sa=GOLD_SA, scorer_sa=SCORER_SA, monitor_sa=MONITOR_SA)
    assert [(r.sa, r.outcome) for r in results] == [
        (GOLD_SA, "denied"), (GOLD_SA, "denied"), (GOLD_SA, "denied"), (SCORER_SA, "denied"), (MONITOR_SA, "denied"),
    ]  # fmt: skip
    assert exit_code(results) == 0


def test_an_allowed_table_creation_is_undone_and_fails_the_check():
    allowing = FakeBigQuery(
        on_query=lambda sql, config, job_id: FakeJob(raise_on_result=api.Forbidden("403")),
        raise_on_get_table=api.Forbidden("403"),
    )
    make, _ = _clients(**{GOLD_SA: allowing})
    results = run_access_checks(make, gold_sa=GOLD_SA, scorer_sa=SCORER_SA, monitor_sa=MONITOR_SA)
    assert results[0].outcome == "allowed" and results[0].detail == "ALLOWED (undone)"
    assert ("delete_table", "synthea-readmission-risk.serving.gold_access_probe", True) in allowing.calls
    assert exit_code(results) == 3


def test_an_allowed_read_of_gold_by_the_scorer_fails_the_check():
    make, _ = _clients(**{SCORER_SA: FakeBigQuery()})  # every query succeeds
    results = run_access_checks(make, gold_sa=GOLD_SA, scorer_sa=SCORER_SA, monitor_sa=MONITOR_SA)
    assert [r.outcome for r in results if r.sa == SCORER_SA] == ["allowed"]
    assert exit_code(results) == 3


@pytest.mark.parametrize(
    ("exc", "denied"),
    [
        (api.Forbidden("403"), True),
        (api.BadRequest("Access Denied: Table x: User does not have permission"), True),
        (api.BadRequest("Permission bigquery.tables.updateData denied"), True),
        (api.BadRequest("Syntax error"), False),
        (api.ServiceUnavailable("503"), False),
    ],
)
def test_what_counts_as_denied(exc, denied):
    assert is_denied(exc) is denied


def test_exit_precedence():
    denied, error, allowed = (ProbeResult("s", "p", o, "") for o in ("denied", "error", "allowed"))
    assert exit_code([denied, error]) == 5
    assert exit_code([error, allowed]) == 3
    assert exit_code([denied]) == 0


def test_a_client_that_cannot_be_built_is_an_error_probe_and_the_others_still_run():
    """No ADC or a bad email must not crash the check or lose the probes already run (round-1 review finding)."""
    make, _ = _clients()

    def flaky(email):
        if email == SCORER_SA:
            raise FileNotFoundError("no Application Default Credentials")
        return make(email)

    results = run_access_checks(flaky, gold_sa=GOLD_SA, scorer_sa=SCORER_SA, monitor_sa=MONITOR_SA)
    assert [(r.sa, r.probe, r.outcome) for r in results] == [
        (GOLD_SA, "create a table in serving", "denied"),
        (GOLD_SA, "change serving.predictions", "denied"),
        (GOLD_SA, "read serving.scoring_runs", "denied"),
        (SCORER_SA, "build client", "error"),
        (MONITOR_SA, "read serving.gold", "denied"),
    ]
    assert exit_code(results) == 5


def test_a_publisher_client_that_cannot_be_built_skips_only_its_probes():
    make, _ = _clients()

    def no_publisher(email):
        if email == GOLD_SA:
            raise ValueError("--impersonate-service-account must be a service-account email")
        return make(email)

    results = run_access_checks(no_publisher, gold_sa=GOLD_SA, scorer_sa=SCORER_SA, monitor_sa=MONITOR_SA)
    assert [(r.sa, r.probe, r.outcome) for r in results] == [
        (GOLD_SA, "build client", "error"),
        (SCORER_SA, "read serving.gold", "denied"),
        (MONITOR_SA, "read serving.gold", "denied"),
    ]
    assert exit_code(results) == 5


def test_the_cli_reports_an_unexpected_error_and_exits_5(monkeypatch, tmp_path):
    import importlib.util
    from pathlib import Path

    from typer.testing import CliRunner

    path = Path(__file__).resolve().parents[2] / "scripts" / "gold_access_check.py"
    spec = importlib.util.spec_from_file_location("gold_access_check_cli", path)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.setattr(cli, "REPORT_DIR", tmp_path / "reports")

    def boom(*args, **kwargs):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(cli, "run_access_checks", boom)
    out = CliRunner().invoke(cli.app, [])
    assert out.exit_code == 5, out.output
    assert "error" in out.output and "RuntimeError: unexpected" in out.output
    assert len(list((tmp_path / "reports").glob("*.json"))) == 1


def test_checks_that_must_pass_and_the_v3_slice2_exit_precedence():
    """v3 slice 2: check() results ("ok"/"failed"/"error") join the probes; a failed check is a FAIL like an allowed probe."""
    from readmission_risk.warehouse.gold_access_check import _probe, check, probe

    assert _probe is probe
    ok = check("sa", "c", lambda: None)
    failed = check("sa", "c", lambda: "wrong columns")
    error = check("sa", "c", lambda: 1 / 0)
    assert (ok.outcome, failed.outcome, failed.detail, error.outcome) == ("ok", "failed", "wrong columns", "error")
    assert exit_code([ok]) == 0 and exit_code([ok, error]) == 5 and exit_code([error, failed]) == 3
