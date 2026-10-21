"""The clock and evaluate stages over a scripted BigQuery + gold world (v2 slice 7). No GCP."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import numpy as np
import pytest

from readmission_risk.monitoring import tick
from readmission_risk.monitoring.config import WEEKLY_CRON
from readmission_risk.monitoring.tick import (
    ClockRequest,
    EvaluateRequest,
    evaluate_tick,
    prepare_tick,
)
from tests.monitoring.world import CONFIG, ROOT, World, full_world

S = CONFIG.first_sim_date + timedelta(days=7 * 17)  # 2023-08-14: the first full cohort
_METRICS = ("n_matured", "observed", "expected", "oe_ratio", "oe_ci_low", "oe_ci_high", "auc", "n_label_after_sim",
            "n_immature", "expected_immature", "coverage_found", "verdict")  # fmt: skip


def _evaluate(world, monkeypatch, *, sim_date=S, cadence="dispatch", run_id=None, score_result="success"):
    world.patch_gold(monkeypatch, tick)
    request = EvaluateRequest(
        sim_date=f"{sim_date:%Y%m%d}", cadence=cadence, scoring_run_id=run_id or f"run{sim_date:%Y%m%d}",
        score_result=score_result, config_path=ROOT / "deploy" / "monitoring.json", prereg_path=ROOT / "prereg.yaml",
    )  # fmt: skip
    return evaluate_tick(request, client_factory=lambda: world.fake)


@pytest.fixture(autouse=True)
def _laptop(monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)


def test_happy_path_writes_one_row_with_the_evaluation(monkeypatch):
    world = full_world(S, positives=0.12)
    result = _evaluate(world, monkeypatch)
    assert result.error is None and result.recorded and result.record.status == "succeeded"
    assert len(world.inserted) == 1
    row = world.inserted[0]
    assert row["sim_date"] == S and row["cadence"] == "dispatch" and row["history_tests_passed"] is True
    assert row["coverage_required"] == row["coverage_found"] == 18 and row["verdict"] in ("ok", "alert")
    assert row["n_matured"] == result.evaluation.n_matured > 0 and row["failed_stage"] is None
    assert row["min_positives_floor"] == 20 and (row["band_low"], row["band_high"]) == (0.8, 1.25)
    assert row["gold_fingerprint_definition"] == "pandas-csv-sha256-v1" and row["trigger"] == "laptop"


def test_kill_trigger_replay_gives_identical_metrics(monkeypatch):
    world = full_world(S, positives=0.12)
    first = _evaluate(world, monkeypatch)
    world.succeed(S)
    second = _evaluate(world, monkeypatch)  # a replay of S
    assert first.record.status == second.record.status == "succeeded"
    assert [getattr(first.record, m) for m in _METRICS] == [getattr(second.record, m) for m in _METRICS]


def test_score_failure_is_failed_at_score(monkeypatch):
    world = full_world(S)
    result = _evaluate(world, monkeypatch, score_result="failure")
    row = world.inserted[0]
    assert (row["status"], row["failed_stage"], row["error_type"]) == ("failed", "score", "TickCheckError")
    assert row["verdict"] is None and row["n_matured"] is None and result.error is not None


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"window_days": 30}, "window_days 30"),
        ({"model_bundle_id": "0" * 64}, "bundle 000"),
        ({"run_date": date(2023, 8, 7)}, "run_date 2023-08-07"),
        ({"status": "refused", "published": False}, "status refused"),
    ],
)
def test_a_wrong_own_scoring_run_is_failed_at_score(monkeypatch, change, message):
    world = full_world(S)
    world.runs[f"run{S:%Y%m%d}"].update(change)
    _evaluate(world, monkeypatch)
    row = world.inserted[0]
    assert (row["status"], row["failed_stage"]) == ("failed", "score") and message in row["error_message"]


def test_own_run_not_the_published_one_is_failed(monkeypatch):
    world = full_world(S)
    world.runs["other"] = {**world.runs[f"run{S:%Y%m%d}"], "scoring_run_id": "other"}
    _evaluate(world, monkeypatch, run_id="other")
    row = world.inserted[0]
    assert row["failed_stage"] == "score" and "not this tick's other" in row["error_message"]


def test_a_30_day_overwrite_of_a_required_date_makes_the_cohort_incomplete(monkeypatch):
    world = full_world(S, positives=0.12)
    overwritten = S - timedelta(days=7 * 10)
    world.runs[world.partitions[overwritten][0]]["window_days"] = 30
    result = _evaluate(world, monkeypatch)
    assert result.record.status == "succeeded" and result.record.verdict == "incomplete_cohort"
    assert result.record.coverage_found == 17


def test_fingerprint_problems_are_failed_at_evaluate(monkeypatch):
    world = full_world(S)
    for _, rows in world.partitions.values():
        rows[0]["gold_fingerprint"] = "f" * 64
    _evaluate(world, monkeypatch)
    assert (world.inserted[0]["status"], world.inserted[0]["failed_stage"]) == ("failed", "evaluate")
    assert "not on this gold" in world.inserted[0]["error_message"]
    world = full_world(S)
    world.partitions[S][1][0]["gold_fingerprint_definition"] = None
    _evaluate(world, monkeypatch)
    assert "no gold_fingerprint_definition" in world.inserted[0]["error_message"]


def test_history_failure_is_failed_at_evaluate(monkeypatch):
    world = full_world(S)
    world.history_failure = "MON-08: an alert does not meet the pre-registered rule with full coverage"
    _evaluate(world, monkeypatch)
    row = world.inserted[0]
    assert (row["status"], row["failed_stage"], row["history_tests_passed"]) == ("failed", "evaluate", False)
    assert row["error_message"].startswith("MON-08")


def test_invalid_sim_date_or_cadence_never_reaches_the_row(monkeypatch):
    world = full_world(S)
    _evaluate(world, monkeypatch, sim_date=S + timedelta(days=1))
    _evaluate(world, monkeypatch, cadence="hourly")
    for row in world.inserted:
        assert row["status"] == "refused" and row["sim_date"] is None and row["cadence"] is None


def test_sim_date_after_the_head_is_refused(monkeypatch):
    world = full_world(S)
    world.ticks = world.ticks[:-3]  # the head is now three weeks before S
    _evaluate(world, monkeypatch)
    assert world.inserted[0]["status"] == "refused" and "after the clock head" in world.inserted[0]["error_message"]


def test_exhausted_clock_still_accepts_a_replay_of_the_last_date(monkeypatch):
    world = World()
    rng = np.random.default_rng(1)
    last = CONFIG.last_sim_date
    day = last - timedelta(days=7 * 17)
    while day <= last:
        world.publish(day, rng=rng)
        day += timedelta(days=7)
    world.finalize_fingerprints()
    day = CONFIG.first_sim_date
    while day <= last:
        world.succeed(day)
        day += timedelta(days=7)
    result = _evaluate(world, monkeypatch, sim_date=last)
    assert result.record.status == "succeeded"


def test_no_client_is_not_recorded_and_never_raises():
    def boom():
        raise FileNotFoundError("no Application Default Credentials")

    result = evaluate_tick(EvaluateRequest(sim_date="20230814", cadence="dispatch", scoring_run_id="r", score_result="success"), client_factory=boom)
    assert result.recorded is False and isinstance(result.error, FileNotFoundError)
    clock = prepare_tick(ClockRequest("laptop", None, None), client_factory=boom)
    assert clock.recorded is False and clock.sim_date is None


def _clock(world, now=None, **kwargs):
    defaults = {"event_name": "laptop", "schedule": None, "requested": None,
                "config_path": ROOT / "deploy" / "monitoring.json", "champion_path": ROOT / "deploy" / "champion.json"}  # fmt: skip
    return prepare_tick(ClockRequest(**{**defaults, **kwargs}), client_factory=lambda: world.fake, now=now)


def test_prepare_tick_cadences_and_head():
    world = World()
    world.succeed(CONFIG.first_sim_date)
    assert (_clock(world).sim_date, _clock(world).cadence) == (CONFIG.first_sim_date + timedelta(days=7), "catch_up")
    assert _clock(world, event_name="workflow_dispatch", requested_cadence="burn_in").cadence == "burn_in"
    assert _clock(world, event_name="schedule", schedule=WEEKLY_CRON).cadence == "weekly"
    replay = _clock(world, event_name="workflow_dispatch", requested=f"{CONFIG.first_sim_date:%Y%m%d}")
    assert (replay.sim_date, replay.cadence) == (CONFIG.first_sim_date, "dispatch")
    assert replay.head == _clock(world).head == CONFIG.first_sim_date + timedelta(days=7)  # a replay never moves the head
    assert world.inserted == []  # success writes nothing


@pytest.mark.parametrize(
    "kwargs",
    [
        {"event_name": "schedule", "schedule": "*/5 * * * *"},
        {"event_name": "schedule", "schedule": "7,37 13-18 * * *"},  # the retired burn-in cron
        {"event_name": "schedule", "schedule": WEEKLY_CRON, "requested_cadence": "burn_in"},
        {"event_name": "workflow_dispatch", "requested_cadence": "weekly"},
        {"event_name": "workflow_dispatch", "requested": "20230418"},
        {"event_name": "workflow_dispatch", "requested": "2023-04-17"},
    ],
)
def test_prepare_tick_refusals_write_a_clock_row_without_invalid_values(kwargs):
    world = World()
    result = _clock(world, **kwargs)
    assert result.error is not None and result.recorded
    [row] = world.inserted
    assert (row["status"], row["failed_stage"]) == ("refused", "clock")
    assert row["sim_date"] is None and row["history_tests_passed"] is None


def test_burn_in_with_replay_date_is_refused_with_cadence():
    world = World()
    world.succeed(CONFIG.first_sim_date)
    result = _clock(world, event_name="workflow_dispatch", requested_cadence="burn_in", requested=f"{CONFIG.first_sim_date:%Y%m%d}")
    assert "cannot be combined with sim_date" in str(result.error) and result.sim_date is None
    [row] = world.inserted
    assert (row["status"], row["failed_stage"], row["cadence"], row["sim_date"]) == ("refused", "clock", "burn_in", None)
    assert _clock(world).head == CONFIG.first_sim_date + timedelta(days=7)  # the clock did not move


def test_prepare_tick_refuses_a_champion_mismatch(tmp_path):
    champion = tmp_path / "champion.json"
    champion.write_text('{"bundle_uri": "gs://b/' + "0" * 64 + '", "sha256": "' + "0" * 64 + '"}\n')
    world = World()
    result = _clock(world, champion_path=champion)
    assert "must update the monitoring config" in str(result.error) and world.inserted[0]["status"] == "refused"


# ---------------------------------------------------------------- the weekly retry slots' skip (Part 2, 2026-10-21)
TUESDAY = datetime(2026, 10, 27, 10, 0, tzinfo=UTC)


def _weekly_row(status="succeeded", cadence="weekly", trigger="schedule", started="2026-10-26T00:20:00Z", i=1):
    return {"monitoring_run_id": f"w{i}", "status": status, "cadence": cadence, "trigger": trigger,
            "model_bundle_id": CONFIG.model_bundle_id, "started_at": datetime.fromisoformat(started.replace("Z", "+00:00")),
            "github_run_url": f"https://github.com/o/r/actions/runs/{i}", "sim_date": None}  # fmt: skip


def test_weekly_skip_returns_no_row():
    world = World()
    world.succeed(CONFIG.first_sim_date)
    world.ticks.append(_weekly_row())
    result = _clock(world, now=lambda: TUESDAY, event_name="schedule", schedule=WEEKLY_CRON)
    assert result.skip_reason == "this week's weekly tick already succeeded (https://github.com/o/r/actions/runs/1)"
    assert (result.sim_date, result.cadence, result.error, result.recorded) == (None, None, None, True)
    assert world.inserted == []


def test_weekly_skip_does_not_apply_to_dispatch_or_burn_in():
    world = World()
    world.succeed(CONFIG.first_sim_date)
    world.ticks.append(_weekly_row())
    for kwargs in ({"event_name": "workflow_dispatch"}, {"event_name": "workflow_dispatch", "requested_cadence": "burn_in"}, {}):
        result = _clock(world, now=lambda: TUESDAY, **kwargs)
        assert result.skip_reason is None and result.sim_date == CONFIG.first_sim_date + timedelta(days=7)


def test_weekly_tick_runs_when_this_week_has_no_attempt():
    world = World()
    world.succeed(CONFIG.first_sim_date)
    world.ticks.append(_weekly_row(started="2026-10-19T00:20:00Z"))  # last week's tick
    result = _clock(world, now=lambda: TUESDAY, event_name="schedule", schedule=WEEKLY_CRON)
    assert result.skip_reason is None and result.cadence == "weekly"
    assert result.sim_date == CONFIG.first_sim_date + timedelta(days=7)


def _mismatched_champion(tmp_path):
    champion = tmp_path / "champion.json"
    champion.write_text('{"bundle_uri": "gs://b/' + "0" * 64 + '", "sha256": "' + "0" * 64 + '"}\n')
    return champion


def test_weekly_skip_precedes_champion_and_cadence_checks(tmp_path):
    world = World()
    world.ticks += [_weekly_row(status="refused", cadence=None, i=i) for i in range(3)]
    result = _clock(world, now=lambda: TUESDAY, event_name="schedule", schedule="0 0 * * *", champion_path=_mismatched_champion(tmp_path))
    assert result.skip_reason.startswith("retry budget spent") and result.error is None and world.inserted == []


def test_budget_counts_champion_mismatch_refusals(tmp_path, monkeypatch):
    for key, value in {"GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "schedule", "GITHUB_SERVER_URL": "https://github.com",
                       "GITHUB_REPOSITORY": "o/r", "GITHUB_RUN_ID": "5", "GITHUB_SHA": "abc"}.items():  # fmt: skip
        monkeypatch.setenv(key, value)
    world = World()
    world.ticks += [_weekly_row(status="refused", cadence=None, i=i) for i in range(2)]
    champion = _mismatched_champion(tmp_path)
    third = _clock(world, now=lambda: TUESDAY, event_name="schedule", schedule=WEEKLY_CRON, champion_path=champion)
    assert third.skip_reason is None and "must update the monitoring config" in str(third.error)
    [row] = world.inserted
    assert (row["trigger"], row["status"], row["cadence"], row["model_bundle_id"]) == ("schedule", "refused", None, CONFIG.model_bundle_id)
    world.ticks.append(row)  # World does not feed inserts back into its tick rows
    fourth = _clock(world, now=lambda: TUESDAY, event_name="schedule", schedule=WEEKLY_CRON, champion_path=champion)
    assert fourth.skip_reason.startswith("retry budget spent") and len(world.inserted) == 1
