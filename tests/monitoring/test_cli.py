"""scripts/monitor_tick.py and scripts/m3_check.py (v2 slice 7): exit codes, GITHUB_OUTPUT, evaluation.json, the laptop
guards, catch-up identity order, and the M3 rules. The stages themselves are tested in test_tick.py."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, date, datetime
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
    for key in ("GITHUB_ACTIONS", "GITHUB_EVENT_NAME", "SCHEDULE", "REQUESTED_SIM_DATE", "REQUESTED_CADENCE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "output.txt"))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))


THURSDAY = datetime(2026, 10, 22, 10, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def timer():
    return _load("burn_in_timer")


@pytest.fixture(autouse=True)
def wall_clock(cli, timer, monkeypatch):
    """Part 2's Monday-Wednesday guards read the wall clock; every test runs on a Thursday unless it moves `state["now"]`."""
    state = {"now": THURSDAY}

    class Fixed(datetime):
        @classmethod
        def now(cls, tz=None):
            return state["now"] if tz is None else state["now"].astimezone(tz)

    monkeypatch.setattr(cli, "datetime", Fixed)
    monkeypatch.setattr(timer, "_now", lambda: state["now"])
    return state


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
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("SCHEDULE", "")
    monkeypatch.setenv("REQUESTED_CADENCE", "burn_in")
    result = ClockResult(sim_date=date(2023, 8, 21), cadence="burn_in", head=date(2023, 8, 21), record=None, error=None, recorded=True)
    monkeypatch.setattr(cli, "prepare_tick", lambda request: seen.append(request) or result)
    out = CliRunner().invoke(cli.app, ["clock"])
    assert out.exit_code == 0, out.output
    request = seen[0]
    assert (request.event_name, request.schedule, request.requested, request.requested_cadence) == ("workflow_dispatch", None, None, "burn_in")
    assert (tmp_path / "output.txt").read_text() == "sim_date=20230821\ncadence=burn_in\nskip=false\n"
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
            assert argv[0] != "gh" or argv[3:7] == ["-R", "fma00/synthea-readmission-risk", "--workflow", "monitor.yml"]

        if argv[:2] == ["git", "rev-parse"]:
            Done.stdout = "abc\n"
        return Done()

    monkeypatch.setattr(cli.subprocess, "run", run)
    out = CliRunner().invoke(cli.app, ["catch-up", "--weeks", "2", *args])
    assert out.exit_code == 1 and "is in_progress" in out.stderr


def test_catch_up_switches_identities_monitor_scorer_monitor(cli, monkeypatch):
    calls = []
    running_checks = []
    monkeypatch.setattr(cli, "require_clean_main", lambda: None)
    monkeypatch.setattr(cli, "require_no_running_workflow", lambda: running_checks.append(1))
    monkeypatch.setattr(cli.gcp, "configure", lambda impersonate_service_account: calls.append(impersonate_service_account))
    monkeypatch.setattr(cli, "prepare_tick", lambda request: calls.append(("clock", request.event_name)) or ClockResult(date(2023, 4, 17), "catch_up", None, None, None, True))

    class Scored:
        error = None
        record = type("R", (), {"scoring_run_id": "r1", "status": "succeeded"})()

    import readmission_risk.warehouse.run as run_module

    monkeypatch.setattr(run_module, "score_to_bigquery", lambda request: calls.append(("score", request.window_days, request.top_n)) or Scored())
    monkeypatch.setattr(cli, "evaluate_tick", lambda request: calls.append(("evaluate", request.cadence, request.score_result))
                        or EvaluateResult(_record(cadence="catch_up"), None, None, True))  # fmt: skip
    out = CliRunner().invoke(cli.app, ["catch-up", "--weeks", "2", "--scorer-sa", "s@x.iam.gserviceaccount.com", "--monitor-sa", "m@x.iam.gserviceaccount.com"])
    assert out.exit_code == 0, out.output
    assert calls == ["m@x.iam.gserviceaccount.com", ("clock", "laptop"), "s@x.iam.gserviceaccount.com", ("score", 7, 10),
                     "m@x.iam.gserviceaccount.com", ("evaluate", "catch_up", "success")] * 2  # fmt: skip
    assert len(running_checks) == 3  # once at start, then before every week


def test_require_no_running_workflow_checks_both_workflows_with_repo(cli):
    seen = []

    def run(argv, **kwargs):
        seen.append(argv)

        class Done:
            returncode, stderr = 0, ""
            stdout = json.dumps([{"status": "in_progress" if "score.yml" in argv else "completed"}])

        return Done()

    with pytest.raises(ValueError, match=r"a score\.yml run is in_progress"):
        cli.require_no_running_workflow(run)
    assert [a[3:7] for a in seen] == [["-R", "fma00/synthea-readmission-risk", "--workflow", "monitor.yml"],
                                       ["-R", "fma00/synthea-readmission-risk", "--workflow", "score.yml"]]  # fmt: skip


def test_catch_up_refuses_mon_wed(cli, monkeypatch, wall_clock):
    wall_clock["now"] = datetime(2026, 10, 27, 10, 0, tzinfo=UTC)  # Tuesday
    monkeypatch.setattr(cli, "require_clean_main", lambda: pytest.fail("the window refuses before the git checks"))
    monkeypatch.setattr(cli, "prepare_tick", lambda request: pytest.fail("no tick on a blocked day"))
    out = CliRunner().invoke(cli.app, ["catch-up", "--weeks", "1", "--scorer-sa", "s@x.iam.gserviceaccount.com", "--monitor-sa", "m@x.iam.gserviceaccount.com"])
    assert out.exit_code == 1 and "Monday 00:00 to Thursday 06:00" in out.stderr


def test_catch_up_window_boundaries(cli, wall_clock):
    for when, refused in ((datetime(2026, 10, 26, 0, 0, tzinfo=UTC), True), (datetime(2026, 10, 28, 23, 59, tzinfo=UTC), True),
                          (datetime(2026, 10, 29, 5, 59, tzinfo=UTC), True), (datetime(2026, 10, 29, 6, 0, tzinfo=UTC), False),
                          (datetime(2026, 11, 1, 23, 59, tzinfo=UTC), False)):  # fmt: skip
        wall_clock["now"] = when
        if refused:
            with pytest.raises(ValueError, match="Monday 00:00 to Thursday 06:00"):
                cli._check_catch_up_window()
        else:
            cli._check_catch_up_window()


def test_catch_up_rechecks_the_window_each_week(cli, monkeypatch, wall_clock):
    wall_clock["now"] = datetime(2026, 10, 25, 23, 50, tzinfo=UTC)  # Sunday evening
    clocks = []
    monkeypatch.setattr(cli, "require_clean_main", lambda: None)
    monkeypatch.setattr(cli, "require_no_running_workflow", lambda: None)
    monkeypatch.setattr(cli.gcp, "configure", lambda impersonate_service_account: None)
    monkeypatch.setattr(cli, "prepare_tick", lambda request: clocks.append(1) or ClockResult(date(2023, 4, 17), "catch_up", None, None, None, True))

    class Scored:
        error = None
        record = type("R", (), {"scoring_run_id": "r1", "status": "succeeded"})()

    import readmission_risk.warehouse.run as run_module

    def evaluate(request):
        wall_clock["now"] = datetime(2026, 10, 26, 0, 5, tzinfo=UTC)  # the first tick ran into Monday
        return EvaluateResult(_record(cadence="catch_up"), None, None, True)

    monkeypatch.setattr(run_module, "score_to_bigquery", lambda request: Scored())
    monkeypatch.setattr(cli, "evaluate_tick", evaluate)
    out = CliRunner().invoke(cli.app, ["catch-up", "--weeks", "3", "--scorer-sa", "s@x.iam.gserviceaccount.com", "--monitor-sa", "m@x.iam.gserviceaccount.com"])
    assert out.exit_code == 1 and "Monday 00:00 to Thursday 06:00" in out.stderr and clocks == [1]


def test_clock_skip_writes_skip_output(cli, monkeypatch, tmp_path):
    result = ClockResult(None, None, None, None, None, True, skip_reason="this week's weekly tick already succeeded (u1)")
    monkeypatch.setattr(cli, "prepare_tick", lambda request: result)
    out = CliRunner().invoke(cli.app, ["clock"])
    assert out.exit_code == 0 and "skip: this week's weekly tick already succeeded (u1)" in out.stdout
    assert (tmp_path / "output.txt").read_text() == "skip=true\n"
    summary = (tmp_path / "summary.md").read_text()
    assert "| status | skipped |" in summary and "| reason | this week's weekly tick already succeeded (u1) |" in summary


# ---------------------------------------------------------------------------------------------------------- M3
def _run(i, conclusion="success"):
    return {"databaseId": i, "conclusion": conclusion, "createdAt": f"2026-10-20T13:{i:02d}:00Z", "url": f"u{i}"}


def _rows(cadences: dict[int, str], status="succeeded"):
    return pd.DataFrame([{"github_run_url": f"u{i}", "cadence": c, "status": status, "github_run_attempt": 1,
                          "started_at": pd.Timestamp("2026-10-20", tz="UTC")} for i, c in cadences.items()])  # fmt: skip


_STARTED = [{"name": "clock", "startedAt": "2026-10-20T13:00Z"}]


def _verdicts(m3, rows, ids, conclusion="success"):
    return [m3.classify(_run(i, conclusion), _STARTED, 1, rows) for i in ids]


def _met(m3, burn_in, scheduled):
    streak = m3.burn_in_streak(burn_in)
    weekly = m3.first_weekly_after(scheduled, streak[-1].run["createdAt"]) if streak else None
    return weekly is not None, [*streak, *([weekly] if weekly else [])]


def test_m3_streak_then_weekly(m3):
    rows = _rows({1: "burn_in", 2: "burn_in", 3: "burn_in", 4: "burn_in", 5: "burn_in", 6: "weekly"})
    met, evidence = _met(m3, _verdicts(m3, rows, range(1, 6)), _verdicts(m3, rows, [6]))
    assert met and [v.run["url"] for v in evidence] == ["u1", "u2", "u3", "u4", "u5", "u6"]


def test_m3_dropped_red_and_rerun(m3):
    rows = _rows({1: "burn_in", 2: "burn_in", 4: "burn_in", 5: "burn_in", 6: "burn_in", 7: "burn_in"})
    dropped = m3.classify(_run(3, "cancelled"), [{"name": "clock", "startedAt": None}], 1, rows)
    assert dropped.kind == "dropped"
    assert m3.classify(_run(3, "cancelled"), _STARTED, 1, rows).kind == "red"  # cancelled mid-tick
    assert m3.classify(_run(1), _STARTED, 2, rows).kind == "red"  # a re-run is an owner intervention
    assert m3.classify(_run(9), _STARTED, 1, rows).kind == "red"  # success without a row
    verdicts = [*_verdicts(m3, rows, (1, 2)), dropped, *_verdicts(m3, rows, (4, 5, 6))]
    assert len(m3.burn_in_streak(verdicts)) == 5  # 5 green burn-in ticks across a dropped run
    assert _met(m3, verdicts, [])[0] is False  # but no weekly tick yet
    broken = [*_verdicts(m3, rows, (1, 2)), m3.classify(_run(8, "failure"), _STARTED, 1, rows)]
    assert m3.burn_in_streak(broken) == []  # never a partial streak


def test_m3_red_weekly_then_green_weekly_is_met(m3):
    rows = _rows({**{i: "burn_in" for i in range(1, 6)}, 6: "weekly", 7: "weekly"})
    rows.loc[rows["github_run_url"] == "u6", "status"] = "failed"
    met, evidence = _met(m3, _verdicts(m3, rows, range(1, 6)), _verdicts(m3, rows, (6, 7)))
    assert met and evidence[-1].run["url"] == "u7"


def test_m3_weekly_before_the_streak_does_not_count(m3):
    rows = _rows({1: "weekly", **{i: "burn_in" for i in range(2, 7)}})
    assert _met(m3, _verdicts(m3, rows, range(2, 7)), _verdicts(m3, rows, [1]))[0] is False


def test_m3_later_burn_in_session_does_not_hide_an_earlier_weekly(m3):
    rows = _rows({**{i: "burn_in" for i in (1, 2, 3, 4, 5, 7, 8, 9)}, 6: "weekly"})
    met, evidence = _met(m3, _verdicts(m3, rows, (1, 2, 3, 4, 5, 7, 8, 9)), _verdicts(m3, rows, [6]))
    assert met and [v.run["url"] for v in evidence] == ["u1", "u2", "u3", "u4", "u5", "u6"]


def test_m3_scheduled_burn_in_runs_never_count(m3):
    rows = _rows({**{i: "burn_in" for i in range(1, 6)}, 6: "burn_in"})  # a (historical) scheduled burn-in row
    assert _met(m3, _verdicts(m3, rows, range(1, 6)), _verdicts(m3, rows, [6]))[0] is False


def _listing(i, title, minute):
    return {"databaseId": i, "conclusion": "success", "createdAt": f"2026-10-20T13:{minute:02d}:00Z", "url": f"u{i}", "displayTitle": title}


def test_m3_collect_lists_events_separately_and_bounds_the_views(m3):
    from readmission_risk.monitoring.burn_in import BURN_IN_TITLE, REPO

    rows = _rows({**{i: "burn_in" for i in range(1, 6)}, 20: "dispatch", 30: "weekly", 31: "weekly", 32: "weekly", 9: "weekly"})
    rows.loc[rows["github_run_url"] == "u30", "status"] = "failed"
    dispatch = [_listing(i, BURN_IN_TITLE, i) for i in range(1, 6)] + [_listing(20, "Monitoring tick", 3)]
    schedule = [_listing(9, "Monitoring tick", 0), _listing(30, "Monitoring tick", 30), _listing(31, "Monitoring tick", 31),
                _listing(32, "Monitoring tick", 32)]  # fmt: skip
    calls = []

    def gh_json(args):
        calls.append(args)
        assert args[:2] == ["-R", REPO]
        if args[2:4] == ["run", "list"]:
            assert args[args.index("--limit") + 1] == "1000"
            if args[args.index("--event") + 1] == "workflow_dispatch":
                return dispatch
            assert args[args.index("--created") + 1] == ">=2026-10-20"  # the streak end's date
            return schedule
        return {"attempt": 1, "jobs": _STARTED}

    burn_in, scheduled, streak, weekly = m3.collect(rows, 1000, gh_json)
    assert [v.run["url"] for v in burn_in] == [f"u{i}" for i in range(1, 6)]  # the manual dispatch u20 is not a burn-in run
    assert len(streak) == 5 and weekly.run["url"] == "u31"
    assert [v.run["url"] for v in scheduled] == ["u30", "u31"]  # a red weekly attempt, then the green one
    viewed = [a[4] for a in calls if a[2:4] == ["run", "view"]]
    assert viewed == ["1", "2", "3", "4", "5", "30", "31"]  # u9 precedes the streak; u32 follows the first green weekly


# ---------------------------------------------------------------------------------------------------- burn-in timer
def _outcome(kind, n=1):
    from readmission_risk.monitoring.burn_in import TickOutcome

    return TickOutcome(n, f"u{n}", kind, {"green": "success", "red": "failure", "dropped": "cancelled"}[kind])


def test_timer_refuses_in_actions_and_bad_options(timer, monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    out = CliRunner().invoke(timer.app, [])
    assert out.exit_code == 1 and "the burn-in timer runs on the laptop only" in out.output
    monkeypatch.delenv("GITHUB_ACTIONS")
    out = CliRunner().invoke(timer.app, ["--ticks", "13"])
    assert out.exit_code == 1 and "ticks must be in [1, 12]" in out.output


@pytest.mark.parametrize("scenario", ["green", "red", "stopped", "interrupted", "auth"])
def test_timer_exit_codes_and_output(timer, monkeypatch, scenario):
    from readmission_risk.monitoring.burn_in import BurnInStop, GhError

    calls = []

    def fake_gh(args):
        calls.append(args)
        if scenario == "auth":
            raise GhError("gh auth status --hostname github.com --active: not logged in")
        return ""

    def fake_run(settings, *, gh, now, sleep, log):
        gh(["-R", "x", "run", "list"])
        if scenario == "green":
            return [_outcome("dropped", 1), _outcome("green", 2)]
        if scenario == "red":
            raise BurnInStop(3, "tick u2 concluded failure; the streak is broken", [_outcome("green", 1), _outcome("red", 2)])
        if scenario == "stopped":
            raise BurnInStop(2, "gh failed: HTTP 502", [])
        raise KeyboardInterrupt

    monkeypatch.setattr(timer, "_gh", fake_gh)
    monkeypatch.setattr(timer, "run_burn_in", fake_run)
    monkeypatch.setattr(timer.time, "sleep", lambda s: None)  # the read-only auth check is retried
    out = CliRunner().invoke(timer.app, ["--ticks", "1"])
    expected = {"green": 0, "red": 3, "stopped": 2, "interrupted": 130, "auth": 2}[scenario]
    assert out.exit_code == expected, out.output
    assert calls[0] == ["auth", "status", "--hostname", "github.com", "--active"]
    if scenario == "green":
        assert out.stdout.strip() == "burn-in timer: 1 runs concluded success (1 dropped and re-dispatched); make m3-check is the authority for M3"
    if scenario == "red":
        assert "ERROR: tick u2 concluded failure" in out.stderr and "  red failure u2" in out.stderr and "  green success u1" in out.stderr
    if scenario == "interrupted":
        assert "interrupted; the run in flight (if any) continues on GitHub" in out.stderr
    if scenario == "auth":
        assert "ERROR: gh failed: gh auth status" in out.stderr and calls == [calls[0]] * 4  # retried, then stopped


def test_timer_gh_wrapper_contract(timer, monkeypatch):
    from types import SimpleNamespace

    from readmission_risk.monitoring.burn_in import GhError

    replies = {"code": 0}

    def fake_subprocess_run(argv, **kwargs):
        if replies.get("missing"):
            raise FileNotFoundError("gh")
        return SimpleNamespace(returncode=replies["code"], stdout='[{"a": 1}]', stderr="warning: something")

    monkeypatch.setattr(timer.subprocess, "run", fake_subprocess_run)
    assert timer._gh(["-R", "r", "run", "list", "--json", "status"]) == '[{"a": 1}]'
    assert timer._gh(["-R", "r", "workflow", "run", "monitor.yml"]) == '[{"a": 1}]\nwarning: something'
    replies["code"] = 1
    with pytest.raises(GhError, match="warning: something"):
        timer._gh(["-R", "r", "run", "list", "--json", "status"])
    replies["missing"] = True
    with pytest.raises(GhError, match="gh is not installed or not executable"):
        timer._gh(["auth", "status"])


def test_timer_wraps_gh_with_retries(timer, monkeypatch):
    from readmission_risk.monitoring.burn_in import REPO, GhError

    failures = {"left": 2}
    seen = {}

    def flaky(args):
        if args[:2] == ["-R", REPO] and failures["left"]:
            failures["left"] -= 1
            raise GhError("HTTP 502")
        return "ok"

    def capture(settings, *, gh, now, sleep, log):
        seen["reply"] = gh(["-R", REPO, "run", "list"])
        return []

    monkeypatch.setattr(timer, "_gh", flaky)
    monkeypatch.setattr(timer, "run_burn_in", capture)
    monkeypatch.setattr(timer.time, "sleep", lambda s: None)
    out = CliRunner().invoke(timer.app, [])
    assert out.exit_code == 0 and seen["reply"] == "ok" and failures["left"] == 0


def test_m3_skip_runs_are_neutral(m3):
    rows = _rows({**{i: "burn_in" for i in range(1, 6)}, 7: "weekly"})
    skipped_jobs = [{"name": "clock", "startedAt": "2026-10-26T00:20Z"}, {"name": "score", "conclusion": "skipped"}]
    for attempt in (1, 2):
        assert m3.classify(_run(6), skipped_jobs, attempt, rows).kind == "skipped"
    skip = m3.classify(_run(6), skipped_jobs, 1, rows)
    met, evidence = _met(m3, _verdicts(m3, rows, range(1, 6)), [skip, *_verdicts(m3, rows, [7])])
    assert met and evidence[-1].run["url"] == "u7"
    assert m3.classify(_run(8), [{"name": "score", "conclusion": "success"}], 1, rows).kind == "red"  # success without a row, not skipped


def test_timer_refuses_mon_wed_at_start(timer, monkeypatch, wall_clock):
    wall_clock["now"] = datetime(2026, 10, 27, 10, 0, tzinfo=UTC)  # Tuesday
    monkeypatch.setattr(timer, "_gh", lambda args: pytest.fail("no gh call on a blocked weekday"))
    out = CliRunner().invoke(timer.app, [])
    assert out.exit_code == 2 and "resume on Thursday" in out.stderr
