"""scripts/monitor_tick.py and scripts/m3_check.py (v2 slice 7): exit codes, GITHUB_OUTPUT, evaluation.json, the laptop
guards, catch-up identity order, and the M3 rules. The stages themselves are tested in test_tick.py."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from typer.testing import CliRunner

from readmission_risk.monitoring.store import new_record
from readmission_risk.monitoring.tick import ClockResult, EvaluateResult, TickCheckError

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"{name}_cli", _SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve the defining module through sys.modules
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def cli():
    return _load("monitor_tick")


@pytest.fixture(scope="module")
def m3():
    return _load("m3_check")


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    for key in ("GITHUB_ACTIONS", "GITHUB_EVENT_NAME", "SCHEDULE", "REQUESTED_SIM_DATE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "output.txt"))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))


def _record(**changes):
    record = new_record("m" * 32, pd.Timestamp("2026-10-20", tz="UTC").to_pydatetime(), "schedule")
    for key, value in {"status": "succeeded", "sim_date": date(2023, 8, 14), "cadence": "burn_in", "verdict": "ok",
                       "oe_ratio": 1.0, "oe_ci_low": 0.7, "oe_ci_high": 1.4, "observed": 40, "expected": 40.0,
                       "n_matured": 2000, "coverage_found": 18, "coverage_required": 18, **changes}.items():  # fmt: skip
        setattr(record, key, value)
    return record


def test_clock_resolved_writes_outputs(cli, monkeypatch, tmp_path):
    seen = []
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
    monkeypatch.setenv("SCHEDULE", "7,37 13-18 * * *")
    result = ClockResult(sim_date=date(2023, 8, 21), cadence="burn_in", head=date(2023, 8, 21), record=None, error=None, recorded=True)
    monkeypatch.setattr(cli, "prepare_tick", lambda request: seen.append(request) or result)
    out = CliRunner().invoke(cli.app, ["clock"])
    assert out.exit_code == 0, out.output
    assert (seen[0].event_name, seen[0].schedule, seen[0].requested) == ("schedule", "7,37 13-18 * * *", None)
    assert (tmp_path / "output.txt").read_text() == "sim_date=20230821\ncadence=burn_in\n"
    assert "Monitoring clock (simulated)" in (tmp_path / "summary.md").read_text()


@pytest.mark.parametrize(("status", "recorded", "code"), [("refused", True, 1), ("failed", True, 2), ("refused", False, 4)])
def test_clock_failure_exit_codes(cli, monkeypatch, status, recorded, code):
    result = ClockResult(None, None, None, _record(status=status, verdict=None), ValueError("no"), recorded)
    monkeypatch.setattr(cli, "prepare_tick", lambda request: result)
    out = CliRunner().invoke(cli.app, ["clock", "--sim-date", "20230418"])
    assert out.exit_code == code


def test_laptop_clock_is_catch_up_and_option_wins(cli, monkeypatch):
    seen = []
    monkeypatch.setenv("REQUESTED_SIM_DATE", "20230501")
    monkeypatch.setattr(cli, "prepare_tick", lambda request: seen.append(request) or ClockResult(date(2023, 4, 24), "catch_up", None, None, None, True))
    CliRunner().invoke(cli.app, ["clock", "--sim-date", "20230424"])
    assert seen[0].event_name == "laptop" and seen[0].requested == "20230424"


@pytest.mark.parametrize(
    ("record", "error", "recorded", "code"),
    [
        ({}, None, True, 0),
        ({"verdict": "alert"}, None, True, 0),  # an alert is a finding, not a failure
        ({"status": "refused", "verdict": None}, ValueError("x"), True, 1),
        ({"status": "failed", "failed_stage": "score", "verdict": None}, TickCheckError("score", "x"), True, 2),
        ({}, RuntimeError("insert"), False, 4),
    ],
)
def test_evaluate_exit_codes_and_files(cli, monkeypatch, tmp_path, record, error, recorded, code):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    result = EvaluateResult(record=_record(**record), evaluation=None, error=error, recorded=recorded)
    monkeypatch.setattr(cli, "evaluate_tick", lambda request: result)
    out = CliRunner().invoke(cli.app, ["evaluate", "--sim-date", "20230814", "--cadence", "burn_in", "--scoring-run-id", "r",
                                       "--score-result", "success", "--out-dir", str(tmp_path / "monitor_out")])  # fmt: skip
    assert out.exit_code == code, out.output
    evaluation = json.loads((tmp_path / "monitor_out" / "evaluation.json").read_text())
    assert evaluation["sim_date"] == "2023-08-14" and evaluation["status"] == result.record.status
    assert set(evaluation) == {"status", "failed_stage", "verdict", "sim_date", "cadence", "oe_ratio", "oe_ci_low", "oe_ci_high",
                               "observed", "expected", "n_matured", "monitoring_run_id", "scoring_run_id", "band_low",
                               "band_high", "min_positives_floor"}  # fmt: skip
    assert (tmp_path / "output.txt").read_text() == f"status={result.record.status}\nverdict={result.record.verdict or ''}\n"


def test_laptop_evaluate_and_catch_up_refuse_a_dirty_tree(cli, monkeypatch):
    def dirty(argv, **kwargs):
        class Done:
            returncode, stdout, stderr = 0, " M deploy/monitoring.json\n", ""

        return Done()

    monkeypatch.setattr(cli.subprocess, "run", dirty)
    monkeypatch.setattr(cli, "evaluate_tick", lambda request: pytest.fail("must refuse before any client"))
    out = CliRunner().invoke(cli.app, ["evaluate", "--sim-date", "20230814", "--cadence", "catch_up"])
    assert out.exit_code == 1 and "uncommitted changes" in out.stderr
    out = CliRunner().invoke(cli.app, ["catch-up", "--weeks", "3", "--scorer-sa", "s@x.iam.gserviceaccount.com", "--monitor-sa", "m@x.iam.gserviceaccount.com"])
    assert out.exit_code == 1 and "uncommitted changes" in out.stderr


def test_catch_up_refuses_in_actions_out_of_range_and_running_workflow(cli, monkeypatch):
    args = ["--scorer-sa", "s@x.iam.gserviceaccount.com", "--monitor-sa", "m@x.iam.gserviceaccount.com"]
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert "laptop only" in CliRunner().invoke(cli.app, ["catch-up", "--weeks", "1", *args]).stderr
    monkeypatch.delenv("GITHUB_ACTIONS")
    assert "--weeks must be" in CliRunner().invoke(cli.app, ["catch-up", "--weeks", "21", *args]).stderr

    def run(argv, **kwargs):
        class Done:
            returncode, stderr = 0, ""
            stdout = json.dumps([{"status": "in_progress"}]) if argv[0] == "gh" else ""

        if argv[:2] == ["git", "rev-parse"]:
            Done.stdout = "abc\n"
        return Done()

    monkeypatch.setattr(cli.subprocess, "run", run)
    out = CliRunner().invoke(cli.app, ["catch-up", "--weeks", "2", *args])
    assert out.exit_code == 1 and "is in_progress" in out.stderr


def test_catch_up_switches_identities_monitor_scorer_monitor(cli, monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "require_clean_main", lambda: None)
    monkeypatch.setattr(cli, "require_no_running_workflow", lambda: None)
    monkeypatch.setattr(cli.gcp, "configure", lambda impersonate_service_account: calls.append(impersonate_service_account))
    monkeypatch.setattr(cli, "prepare_tick", lambda request: calls.append(("clock", request.event_name)) or ClockResult(date(2023, 4, 17), "catch_up", None, None, None, True))

    class Scored:
        error = None
        record = type("R", (), {"scoring_run_id": "r1", "status": "succeeded"})()

    import readmission_risk.warehouse.run as run_module

    monkeypatch.setattr(run_module, "score_to_bigquery", lambda request: calls.append(("score", request.window_days, request.top_n)) or Scored())
    monkeypatch.setattr(cli, "evaluate_tick", lambda request: calls.append(("evaluate", request.cadence, request.score_result))
                        or EvaluateResult(_record(cadence="catch_up"), None, None, True))  # fmt: skip
    out = CliRunner().invoke(cli.app, ["catch-up", "--weeks", "1", "--scorer-sa", "s@x.iam.gserviceaccount.com", "--monitor-sa", "m@x.iam.gserviceaccount.com"])
    assert out.exit_code == 0, out.output
    assert calls == ["m@x.iam.gserviceaccount.com", ("clock", "laptop"), "s@x.iam.gserviceaccount.com", ("score", 7, 10),
                     "m@x.iam.gserviceaccount.com", ("evaluate", "catch_up", "success")]  # fmt: skip


# ---------------------------------------------------------------------------------------------------------- M3
def _run(i, conclusion="success"):
    return {"databaseId": i, "conclusion": conclusion, "createdAt": f"2026-10-20T13:{i:02d}:00Z", "url": f"u{i}"}


def _rows(cadences: dict[int, str], status="succeeded"):
    return pd.DataFrame([{"github_run_url": f"u{i}", "cadence": c, "status": status, "github_run_attempt": 1,
                          "started_at": pd.Timestamp("2026-10-20", tz="UTC")} for i, c in cadences.items()])  # fmt: skip


def test_m3_streak_then_weekly(m3):
    rows = _rows({1: "burn_in", 2: "burn_in", 3: "burn_in", 4: "burn_in", 5: "burn_in", 6: "weekly"})
    verdicts = [m3.classify(_run(i), [{"name": "clock", "startedAt": "2026-10-20T13:00Z"}], 1, rows) for i in range(1, 7)]
    met, evidence = m3.m3_met(verdicts)
    assert met and [v.run["url"] for v in evidence] == ["u1", "u2", "u3", "u4", "u5", "u6"]


def test_m3_dropped_red_and_rerun(m3):
    rows = _rows({1: "burn_in", 2: "burn_in", 4: "burn_in", 5: "burn_in", 6: "burn_in", 7: "burn_in"})
    started = [{"name": "clock", "startedAt": "2026-10-20T13:00Z"}]
    dropped = m3.classify(_run(3, "cancelled"), [{"name": "clock", "startedAt": None}], 1, rows)
    assert dropped.kind == "dropped"
    assert m3.classify(_run(3, "cancelled"), started, 1, rows).kind == "red"  # cancelled mid-tick
    assert m3.classify(_run(1), started, 2, rows).kind == "red"  # a re-run is an owner intervention
    assert m3.classify(_run(9), started, 1, rows).kind == "red"  # success without a row
    verdicts = [m3.classify(_run(i), started, 1, rows) for i in (1, 2)] + [dropped] + [m3.classify(_run(i), started, 1, rows) for i in (4, 5, 6)]
    met, _ = m3.m3_met(verdicts)
    assert not met  # 5 green burn-in ticks across a dropped run, but no weekly tick yet
    assert len(m3.m3_met(verdicts)[1]) == 5
    broken = [m3.classify(_run(i), started, 1, rows) for i in (1, 2)] + [m3.classify(_run(8, "failure"), started, 1, rows)]
    assert m3.m3_met(broken) == (False, [])
