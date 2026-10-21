#!/usr/bin/env python
"""The laptop burn-in timer: dispatches monitor.yml with cadence=burn_in, one tick at a time, paced start to start.

Laptop only; needs `gh` authenticated with the workflow scope (no cloud credentials). Stops at the first red run.
Exit codes: 0 all ticks concluded success (make m3-check is the authority for metric M3); 1 refused (in Actions, bad
options); 2 stopped (gh error, timeout, unidentifiable or repeatedly dropped run); 3 a red tick; 130 interrupted. Design:
notes/eg-new-feature/monitoring-burn-in-timer-and-weekly-retry-2026-10-20.md.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from typing import Annotated

import typer

from readmission_risk.monitoring.burn_in import (
    BurnInStop,
    GhError,
    TimerSettings,
    run_burn_in,
    with_retries,
)

app = typer.Typer(add_completion=False, rich_markup_mode=None, pretty_exceptions_enable=False, help=__doc__.split("\n\n")[1])

BLOCKED_WEEKDAYS: frozenset[int] = frozenset()  # Part 2 of the design blocks Monday-Wednesday (the weekly retry slots)
AUTH_CHECK = ["auth", "status", "--hostname", "github.com", "--active"]  # `auth status` takes no -R


def _now() -> datetime:
    return datetime.now(UTC)


def _gh(args: list[str]) -> str:
    """stdout alone for --json calls; stdout and stderr otherwise (gh may print a dispatched run's URL on either)."""
    try:
        done = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    except OSError as exc:
        raise GhError(f"gh is not installed or not executable: {exc}") from None
    if done.returncode != 0:
        raise GhError(f"gh {' '.join(args)}: {done.stderr.strip()}")
    return done.stdout if "--json" in args else f"{done.stdout}\n{done.stderr}"


def _log(line: str) -> None:
    print(line, flush=True)


def _settings(ticks: int, interval_minutes: int) -> TimerSettings:
    if os.environ.get("GITHUB_ACTIONS") == "true":
        raise ValueError("the burn-in timer runs on the laptop only")
    return TimerSettings(ticks=ticks, interval=timedelta(minutes=interval_minutes), blocked_weekdays=BLOCKED_WEEKDAYS)


def _start_checks(settings: TimerSettings, gh) -> None:
    """The weekday guard (Part 2 of the design blocks Monday-Wednesday), then the gh login."""
    if _now().weekday() in settings.blocked_weekdays:
        raise BurnInStop(2, "the weekly retry slots run Monday to Wednesday (UTC); the burn-in timer does not dispatch "
                            "on those days, so resume on Thursday", [])  # fmt: skip
    try:
        gh(AUTH_CHECK)
    except GhError as exc:
        raise BurnInStop(2, f"gh failed: {exc}", []) from None


@app.command()
def main(
    ticks: Annotated[int, typer.Option(help="Green ticks to collect, 1..12.")] = 5,
    interval_minutes: Annotated[int, typer.Option(help="Minutes from one dispatch to the next, 10..120.")] = 30,
) -> None:
    try:
        settings = _settings(ticks, interval_minutes)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise typer.Exit(code=1) from None
    gh = with_retries(_gh, time.sleep)
    try:
        _start_checks(settings, gh)
        outcomes = run_burn_in(settings, gh=gh, now=_now, sleep=time.sleep, log=_log)
    except BurnInStop as stop:
        print(f"ERROR: {stop}", file=sys.stderr)
        for o in stop.outcomes:
            print(f"  {o.kind} {o.conclusion} {o.url}", file=sys.stderr)
        raise typer.Exit(code=stop.code) from None
    except KeyboardInterrupt:
        print("interrupted; the run in flight (if any) continues on GitHub", file=sys.stderr)
        raise typer.Exit(code=130) from None
    greens = sum(o.kind == "green" for o in outcomes)
    dropped = sum(o.kind == "dropped" for o in outcomes)
    print(f"burn-in timer: {greens} runs concluded success ({dropped} dropped and re-dispatched); make m3-check is the "
          "authority for M3")  # fmt: skip


if __name__ == "__main__":
    app()
