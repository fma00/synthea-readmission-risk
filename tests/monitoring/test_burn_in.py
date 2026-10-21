"""The laptop burn-in timer's loop (monitoring/burn_in.py) over a scripted fake `gh` and a fake clock. No network."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from readmission_risk.monitoring import burn_in as bi
from readmission_risk.monitoring.burn_in import (
    BURN_IN_TITLE,
    REPO,
    BurnInStop,
    GhError,
    TimerSettings,
    classify_run,
    run_burn_in,
    with_retries,
)

THURSDAY = datetime(2026, 10, 22, 10, 0, tzinfo=UTC)
URL = "https://github.com/fma00/synthea-readmission-risk/actions/runs/{}"


class Clock:
    def __init__(self, start=THURSDAY):
        self.t = start
        self.sleeps: list[float] = []

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.t += timedelta(seconds=seconds)


class FakeGh:
    """Each dispatched run passes through `views` (a list of view dicts per run, the last repeated); busy lists are consumed."""

    def __init__(self, clock, outcomes, *, print_url=True, previous=(), busy=(), tick_minutes=9):
        self.clock = clock
        self.outcomes = list(outcomes)  # per dispatch: (conclusion, clock_started)
        self.print_url = print_url
        self.previous = list(previous)
        self.busy = list(busy)  # per idle poll: True while a run is active
        self.tick_minutes = tick_minutes
        self.calls: list[list[str]] = []
        self.dispatched: list[int] = []
        self.started: dict[int, datetime] = {}

    def __call__(self, args):
        self.calls.append(list(args))
        assert args[:2] == ["-R", REPO], args
        rest = args[2:]
        if rest[:2] == ["workflow", "run"]:
            run_id = 1000 + len(self.dispatched)
            self.dispatched.append(run_id)
            self.started[run_id] = self.clock.now()
            return f"\nhttps://github.com/fma00/synthea-readmission-risk/actions/runs/{run_id}\n" if self.print_url else "\n"
        if rest[:2] == ["run", "view"]:
            run_id = int(rest[2])
            done = self.clock.now() - self.started[run_id] >= timedelta(minutes=self.tick_minutes)
            conclusion, clock_started = self.outcomes[self.dispatched.index(run_id)]
            jobs = [{"name": "clock", "startedAt": "2026-10-22T10:00:00Z" if clock_started else None}]
            return json.dumps({"status": "completed" if done else "in_progress", "conclusion": conclusion if done else "",
                               "attempt": 1, "jobs": jobs, "url": URL.format(run_id)})  # fmt: skip
        if rest[:2] == ["run", "list"]:
            fields = rest[rest.index("--json") + 1]
            if fields == "status":
                active = self.busy.pop(0) if self.busy and rest[3] == "monitor.yml" else False
                return json.dumps([{"status": "in_progress" if active else "completed"}])
            if "displayTitle" in fields and "url" in fields:
                return json.dumps(self.previous)
            runs = [{"databaseId": i, "createdAt": "2026-10-22T10:00:00Z", "displayTitle": BURN_IN_TITLE} for i in self.dispatched]
            return json.dumps(runs)
        raise AssertionError(f"unexpected gh call {args}")


def _run(gh, clock, **settings):
    logs: list[str] = []
    result = run_burn_in(TimerSettings(**settings), gh=gh, now=clock.now, sleep=clock.sleep, log=logs.append)
    return result, logs


def _green(n):
    return [("success", True)] * n


def test_five_greens_return_five_outcomes_and_dispatch_exactly():
    clock = Clock()
    gh = FakeGh(clock, _green(5))
    outcomes, logs = _run(gh, clock)
    assert [o.kind for o in outcomes] == ["green"] * 5 and [o.run_id for o in outcomes] == [1000, 1001, 1002, 1003, 1004]
    dispatches = [c for c in gh.calls if c[2:4] == ["workflow", "run"]]
    assert dispatches == [["-R", REPO, "workflow", "run", "monitor.yml", "--ref", "main", "-f", "cadence=burn_in"]] * 5
    assert all(c[:2] == ["-R", REPO] for c in gh.calls)
    assert logs[0] == "previous burn-in run: none among the last 100 dispatch runs"
    assert logs[1] == f"2026-10-22 10:09:00 UTC green success {URL.format(1000)}"


def test_pacing_is_start_to_start_and_zero_when_a_tick_overruns():
    clock = Clock()
    _run(FakeGh(clock, _green(2), tick_minutes=9), clock, ticks=2, poll_seconds=60)
    assert 21 * 60 in clock.sleeps  # 30 min interval - 9 min tick, measured from the dispatch
    clock = Clock()
    _run(FakeGh(clock, _green(2), tick_minutes=40), clock, ticks=2, poll_seconds=60)
    assert 0.0 in clock.sleeps  # the tick took longer than the interval: the next dispatch follows at once


def test_url_on_the_stderr_part_and_the_fallback_identification():
    clock = Clock()
    gh = FakeGh(clock, _green(1), print_url=False)
    outcomes, _ = _run(gh, clock, ticks=1)
    assert outcomes[0].run_id == 1000


def test_fallback_with_two_candidates_or_none_stops_with_code_2(monkeypatch):
    clock = Clock()
    gh = FakeGh(clock, _green(1), print_url=False)
    original = gh.__call__

    def two(args):
        out = original(args)
        if args[2:4] == ["run", "list"] and "databaseId,createdAt,displayTitle" in args:
            return json.dumps([{"databaseId": 7, "createdAt": "x", "displayTitle": BURN_IN_TITLE},
                               {"databaseId": 8, "createdAt": "x", "displayTitle": BURN_IN_TITLE}])  # fmt: skip
        return out

    with pytest.raises(BurnInStop, match=r"2 candidates \[7, 8\]") as stop:
        run_burn_in(TimerSettings(ticks=1), gh=two, now=clock.now, sleep=clock.sleep, log=lambda s: None)
    assert stop.value.code == 2

    def none(args):
        out = original(args)
        if args[2:4] == ["run", "list"] and "databaseId,createdAt,displayTitle" in args:
            return "[]"
        return out

    with pytest.raises(BurnInStop, match="none within 120 s") as stop:
        run_burn_in(TimerSettings(ticks=1), gh=none, now=clock.now, sleep=clock.sleep, log=lambda s: None)
    assert stop.value.code == 2


def test_red_stops_with_code_3_and_the_outcomes_so_far():
    clock = Clock()
    gh = FakeGh(clock, [("success", True), ("failure", True)])
    with pytest.raises(BurnInStop, match="concluded failure; the streak is broken") as stop:
        _run(gh, clock)
    assert stop.value.code == 3 and [o.kind for o in stop.value.outcomes] == ["green", "red"]


def test_dropped_is_redispatched_and_three_in_a_row_stop():
    clock = Clock()
    gh = FakeGh(clock, [("cancelled", False), ("success", True)])
    outcomes, _ = _run(gh, clock, ticks=1)
    assert [o.kind for o in outcomes] == ["dropped", "green"]
    clock = Clock()
    gh = FakeGh(clock, [("cancelled", False)] * 3)
    with pytest.raises(BurnInStop, match="3 consecutive dispatched runs were cancelled") as stop:
        _run(gh, clock, ticks=1)
    assert stop.value.code == 2
    assert classify_run({"status": "completed", "conclusion": "cancelled", "jobs": [{"name": "clock", "startedAt": "2026"}]}) == "red"


def test_idle_wait_checks_both_workflows_and_times_out():
    clock = Clock()
    gh = FakeGh(clock, _green(1), busy=[True, True, False])
    _run(gh, clock, ticks=1)
    listed = [c[5] for c in gh.calls if c[2:4] == ["run", "list"] and c[-3:-1] == ["status", "--limit"]]
    assert {"monitor.yml", "score.yml"} <= set(listed)
    clock = Clock()
    gh = FakeGh(clock, _green(1), busy=[True] * 400)
    with pytest.raises(BurnInStop, match="stayed active for 150 minutes") as stop:
        _run(gh, clock, ticks=1)
    assert stop.value.code == 2 and gh.dispatched == []


def test_run_timeout_counts_from_the_dispatch_and_a_completed_view_wins():
    clock = Clock()
    gh = FakeGh(clock, _green(1), tick_minutes=200)
    with pytest.raises(BurnInStop, match="did not finish within 150 minutes of its dispatch") as stop:
        _run(gh, clock, ticks=1)
    assert stop.value.code == 2
    clock = Clock()
    gh = FakeGh(clock, _green(1), tick_minutes=150)
    outcomes, _ = _run(gh, clock, ticks=1, poll_seconds=9000)  # one long poll overshoots the timeout; completed wins
    assert outcomes[0].kind == "green"


def test_gh_error_and_malformed_replies_stop_with_code_2():
    clock = Clock()

    def failing(args):
        raise GhError("gh -R x run list: HTTP 502")

    with pytest.raises(BurnInStop, match="gh failed: gh -R x run list: HTTP 502") as stop:
        run_burn_in(TimerSettings(), gh=failing, now=clock.now, sleep=clock.sleep, log=lambda s: None)
    assert stop.value.code == 2
    gh = FakeGh(clock, _green(1))

    def missing_status(args):
        out = gh(args)
        if args[2:4] == ["run", "list"] and args[-3:-1] == ["status", "--limit"]:
            return json.dumps([{}])
        return out

    with pytest.raises(BurnInStop, match="unexpected output: KeyError") as stop:
        run_burn_in(TimerSettings(), gh=missing_status, now=clock.now, sleep=clock.sleep, log=lambda s: None)
    assert stop.value.code == 2


def test_previous_burn_in_run_is_logged_with_a_warning_when_not_green():
    clock = Clock()
    previous = [
        {"databaseId": 1, "createdAt": "2026-10-21T09:00:00Z", "status": "completed", "conclusion": "success", "displayTitle": BURN_IN_TITLE, "url": "u1"},
        {"databaseId": 2, "createdAt": "2026-10-21T10:00:00Z", "status": "completed", "conclusion": "failure", "displayTitle": BURN_IN_TITLE, "url": "u2"},
        {"databaseId": 3, "createdAt": "2026-10-21T11:00:00Z", "status": "completed", "conclusion": "success", "displayTitle": "Monitoring tick", "url": "u3"},
    ]  # fmt: skip
    _, logs = _run(FakeGh(clock, _green(1), previous=previous), clock, ticks=1)
    assert logs[0] == "previous burn-in run: completed failure u2"
    assert logs[1].startswith("WARNING: the previous burn-in run concluded failure; unless it was cancelled")
    clock = Clock()
    _, logs = _run(FakeGh(clock, _green(1), previous=previous[:1]), clock, ticks=1)
    assert logs[0] == "previous burn-in run: completed success u1" and not logs[1].startswith("WARNING")


def test_weekday_guard_before_dispatch_and_rechecked_after_the_idle_wait():
    tuesday = Clock(datetime(2026, 10, 27, 10, 0, tzinfo=UTC))
    gh = FakeGh(tuesday, _green(1))
    with pytest.raises(BurnInStop, match="resume on Thursday") as stop:
        _run(gh, tuesday, ticks=1, blocked_weekdays=frozenset({0, 1, 2}))
    assert stop.value.code == 2 and gh.dispatched == []
    sunday = Clock(datetime(2026, 10, 25, 23, 50, tzinfo=UTC))
    gh = FakeGh(sunday, _green(1), busy=[True] * 40)  # the idle wait crosses midnight into Monday
    with pytest.raises(BurnInStop, match="resume on Thursday"):
        _run(gh, sunday, ticks=1, blocked_weekdays=frozenset({0, 1, 2}))
    assert gh.dispatched == []


def test_retry_sleeps_count_against_the_idle_deadline():
    clock = Clock()
    gh = FakeGh(clock, _green(1), busy=[True] * 400)
    flaky_calls = {"n": 0}

    def flaky(args):
        if args[2:4] == ["run", "list"] and args[-3:-1] == ["status", "--limit"]:
            flaky_calls["n"] += 1
            if flaky_calls["n"] % 2:
                raise GhError("HTTP 502")
        return gh(args)

    with pytest.raises(BurnInStop, match="stayed active for 150 minutes"):
        run_burn_in(TimerSettings(ticks=1), gh=with_retries(flaky, clock.sleep), now=clock.now, sleep=clock.sleep, log=lambda s: None)
    assert 30.0 in clock.sleeps and clock.now() - THURSDAY >= timedelta(minutes=150)


def test_read_only_calls_retry_and_dispatch_does_not():
    sleeps: list[float] = []
    for args in (["-R", REPO, "run", "list"], ["-R", REPO, "run", "view", "1"], ["run", "list"], ["auth", "status", "--hostname", "github.com", "--active"]):
        failures = iter([GhError("a"), GhError("b")])

        def twice(a, failures=failures):
            err = next(failures, None)
            if err:
                raise err
            return "ok"

        sleeps.clear()
        assert with_retries(twice, sleeps.append)(args) == "ok" and sleeps == [30.0, 30.0]

    def always(a):
        raise GhError("down")

    sleeps.clear()
    with pytest.raises(GhError):
        with_retries(always, sleeps.append)(["-R", REPO, "run", "list"])
    assert sleeps == [30.0, 30.0, 30.0]
    sleeps.clear()
    with pytest.raises(GhError):
        with_retries(always, sleeps.append)(["-R", REPO, "workflow", "run", "monitor.yml"])
    assert sleeps == []


def test_timer_settings_bounds():
    for ok in ({"ticks": 1}, {"ticks": 12}, {"interval": timedelta(minutes=10)}, {"interval": timedelta(minutes=120)}):
        TimerSettings(**ok)
    for bad, match in (({"ticks": 0}, "ticks"), ({"ticks": 13}, "ticks"), ({"interval": timedelta(minutes=9)}, "interval"),
                       ({"interval": timedelta(minutes=121)}, "interval")):  # fmt: skip
        with pytest.raises(ValueError, match=match):
            TimerSettings(**bad)


def test_repo_args_and_title_constant():
    assert bi.repo_args("run", "list") == ["-R", "fma00/synthea-readmission-risk", "run", "list"]
    assert BURN_IN_TITLE == "Monitoring tick (burn-in)"
