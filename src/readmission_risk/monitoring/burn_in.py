"""The laptop burn-in timer's loop (pure: the `gh` call, the clock, sleeping and logging are injected).

GitHub's scheduler dropped most of the accelerated burn-in's cron slots for this repository, so the burn-in is
dispatched by a timer on the owner's laptop: one `monitor.yml` run at a time with the dispatch input cadence=burn_in,
paced start to start, stopping at the first red run. The timer only paces; `scripts/m3_check.py` is the authority for
metric M3. See notes/eg-new-feature/monitoring-burn-in-timer-and-weekly-retry-2026-10-20.md (Interfaces > burn_in.py).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

BURN_IN_TITLE = "Monitoring tick (burn-in)"  # monitor.yml's run-name for cadence=burn_in (tests/test_infra.py)
REPO = "fma00/synthea-readmission-risk"
GROUP_WORKFLOWS = ("monitor.yml", "score.yml")  # they share the concurrency group "score"
IDENTIFY_SECONDS = 120
_RUN_URL_RE = re.compile(r"/actions/runs/(\d+)")
_READ_ONLY = (("run", "list"), ("run", "view"), ("auth", "status"))

Gh = Callable[[list[str]], str]


class GhError(RuntimeError):
    pass


class BurnInStop(RuntimeError):
    def __init__(self, code: int, message: str, outcomes: list[TickOutcome]):
        super().__init__(message)
        self.code = code
        self.outcomes = list(outcomes)


@dataclass(frozen=True)
class TimerSettings:
    ticks: int = 5
    interval: timedelta = timedelta(minutes=30)
    run_timeout: timedelta = timedelta(minutes=150)  # > monitor.yml's 100 minutes of job timeouts, plus queueing
    idle_timeout: timedelta = timedelta(minutes=150)
    poll_seconds: float = 30.0
    max_consecutive_drops: int = 3
    blocked_weekdays: frozenset[int] = frozenset()  # UTC weekday() values on which no dispatch starts

    def __post_init__(self):
        if type(self.ticks) is not int or not 1 <= self.ticks <= 12:
            raise ValueError(f"ticks must be in [1, 12]; got {self.ticks}")
        if not timedelta(minutes=10) <= self.interval <= timedelta(minutes=120):
            raise ValueError(f"interval must be between 10 and 120 minutes; got {self.interval}")


@dataclass(frozen=True)
class TickOutcome:
    run_id: int
    url: str
    kind: str  # green | red | dropped
    conclusion: str


def repo_args(*args: str) -> list[str]:
    """A `run`/`workflow` call pinned to the repository (`gh auth status` takes no -R)."""
    return ["-R", REPO, *args]


def _strip_repo(args: list[str]) -> list[str]:
    return args[2:] if args[:2] == ["-R", REPO] else args


def with_retries(gh: Gh, sleep: Callable[[float], None], *, attempts: int = 4, delay_seconds: float = 30.0) -> Gh:
    """Retry read-only gh calls on GhError (at most `attempts` calls); a dispatch (`workflow run`) is never retried,
    because a failed dispatch call may still have created a run."""

    def call(args: list[str]) -> str:
        if tuple(_strip_repo(args)[:2]) not in _READ_ONLY:
            return gh(args)
        for attempt in range(1, attempts + 1):
            try:
                return gh(args)
            except GhError:
                if attempt == attempts:
                    raise
                sleep(delay_seconds)
        raise AssertionError("unreachable")  # pragma: no cover

    return call


def _clock_not_started(view: dict) -> bool:
    clock = next((j for j in view.get("jobs") or [] if j.get("name") == "clock"), None)
    return clock is None or not clock.get("startedAt") or str(clock["startedAt"]).startswith("0001")


def classify_run(view: dict) -> str | None:
    """None while the run is not completed; else green | red | dropped (cancelled before its clock job started)."""
    if view["status"] != "completed":
        return None
    if view["conclusion"] == "success":
        return "green"
    if view["conclusion"] == "cancelled" and _clock_not_started(view):
        return "dropped"
    return "red"


def _created_at(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _minutes(delta: timedelta) -> int:
    return int(delta.total_seconds() // 60)


def run_burn_in(
    settings: TimerSettings,
    *,
    gh: Gh,
    now: Callable[[], datetime],
    sleep: Callable[[float], None],
    log: Callable[[str], None],
) -> list[TickOutcome]:
    """Dispatch until `settings.ticks` runs concluded success. Raises BurnInStop (code 3 on a red run, 2 otherwise)."""
    outcomes: list[TickOutcome] = []

    def listed(*args: str) -> list[dict]:
        return json.loads(gh(repo_args("run", "list", "--workflow", *args)))

    try:
        _log_previous(listed, log)
        greens = drops = 0
        while greens < settings.ticks:
            _wait_for_idle(settings, listed, now, sleep, outcomes)
            if now().weekday() in settings.blocked_weekdays:
                raise BurnInStop(2, "the weekly retry slots run Monday to Wednesday (UTC); the burn-in timer does not "
                                    "dispatch on those days, so resume on Thursday", outcomes)  # fmt: skip
            before = {r["databaseId"] for r in listed("monitor.yml", "--event", "workflow_dispatch", "--json", "databaseId", "--limit", "20")}
            t0 = now()
            out = gh(repo_args("workflow", "run", "monitor.yml", "--ref", "main", "-f", "cadence=burn_in"))
            run_id = _identify(settings, out, before, listed, t0, now, sleep, outcomes)
            view = _wait_for_completion(settings, gh, run_id, t0, now, sleep, outcomes)
            kind = classify_run(view)
            outcomes.append(TickOutcome(run_id, view["url"], kind, view["conclusion"]))
            log(f"{now():%Y-%m-%d %H:%M:%S} UTC {kind} {view['conclusion']} {view['url']}")
            if kind == "red":
                raise BurnInStop(3, f"tick {view['url']} concluded {view['conclusion']}; the streak is broken: fix the "
                                    "cause and restart the timer", outcomes)  # fmt: skip
            if kind == "dropped":
                drops += 1
                if drops >= settings.max_consecutive_drops:
                    raise BurnInStop(2, f"{drops} consecutive dispatched runs were cancelled before their clock job "
                                        "started; check the concurrency group 'score'", outcomes)  # fmt: skip
                continue
            greens, drops = greens + 1, 0
            if greens < settings.ticks:
                sleep(max(0.0, (t0 + settings.interval - now()).total_seconds()))
    except GhError as exc:
        raise BurnInStop(2, f"gh failed: {exc}", outcomes) from None
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise BurnInStop(2, f"gh returned unexpected output: {type(exc).__name__}: {exc}", outcomes) from None
    return outcomes


def _log_previous(listed, log) -> None:
    runs = listed("monitor.yml", "--event", "workflow_dispatch", "--json",
                  "databaseId,createdAt,status,conclusion,displayTitle,url", "--limit", "100")  # fmt: skip
    mine = [r for r in runs if r["displayTitle"] == BURN_IN_TITLE]
    if not mine:
        log("previous burn-in run: none among the last 100 dispatch runs")
        return
    last = max(mine, key=lambda r: _created_at(r["createdAt"]))
    log(f"previous burn-in run: {last['status']} {last['conclusion'] or '-'} {last['url']}")
    if last["status"] == "completed" and last["conclusion"] != "success":
        log(f"WARNING: the previous burn-in run concluded {last['conclusion']}; unless it was cancelled before its clock "
            "started (dropped), M3's streak restarts with this session; make m3-check decides")  # fmt: skip


def _wait_for_idle(settings, listed, now, sleep, outcomes) -> None:
    wait_start = now()
    while True:
        busy = [r for w in GROUP_WORKFLOWS for r in listed(w, "--json", "status", "--limit", "20") if r["status"] != "completed"]
        if not busy:
            return
        if now() - wait_start >= settings.idle_timeout:
            raise BurnInStop(2, f"a monitor.yml or score.yml run stayed active for {_minutes(settings.idle_timeout)} "
                                "minutes", outcomes)  # fmt: skip
        sleep(settings.poll_seconds)


def _identify(settings, out, before, listed, t0, now, sleep, outcomes) -> int:
    match = _RUN_URL_RE.search(out)
    if match:
        return int(match.group(1))
    while True:
        runs = listed("monitor.yml", "--event", "workflow_dispatch", "--json", "databaseId,createdAt,displayTitle", "--limit", "20")
        candidates = sorted(r["databaseId"] for r in runs if r["databaseId"] not in before and r["displayTitle"] == BURN_IN_TITLE)
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            raise BurnInStop(2, f"could not identify the dispatched run: {len(candidates)} candidates {candidates}", outcomes)
        if (now() - t0).total_seconds() >= IDENTIFY_SECONDS:
            raise BurnInStop(2, f"could not identify the dispatched run: none within {IDENTIFY_SECONDS} s", outcomes)
        sleep(settings.poll_seconds)


def _wait_for_completion(settings, gh, run_id, t0, now, sleep, outcomes) -> dict:
    while True:
        view = json.loads(gh(repo_args("run", "view", str(run_id), "--json", "status,conclusion,attempt,jobs,url")))
        if classify_run(view) is not None:
            return view
        if now() - t0 >= settings.run_timeout:
            raise BurnInStop(2, f"run {view['url']} did not finish within {_minutes(settings.run_timeout)} minutes of "
                                "its dispatch", outcomes)  # fmt: skip
        sleep(settings.poll_seconds)
