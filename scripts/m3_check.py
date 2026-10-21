#!/usr/bin/env python
"""Checks metric M3 (PRD, amended 2026-10-20): >= 5 consecutive green timer-dispatched burn-in ticks, then >= 1 green scheduled weekly tick.

Burn-in runs are the workflow_dispatch runs titled 'Monitoring tick (burn-in)'; weekly runs are the scheduled runs. A run counts green only if its Actions conclusion is success, its attempt is 1, AND serving.monitoring holds a succeeded row for its URL with the expected cadence. A cancelled run that never started its clock job is dropped (neither green nor red); a scheduled run whose score job was skipped is a skip (neutral). The streak is the first 5 consecutive green burn-in runs; M3 is met by the first green weekly run after it, and red, dropped or skipped weekly runs in between do not invalidate it. Scheduled runs are listed from the streak's date. Needs `gh` and BigQuery read access (laptop: the ledger-reader SA). Exit 0 met, 3 not met, 2 on a gh or BigQuery error.

Design: notes/eg-new-feature/v2-slice7-monitoring-tick-ledger-2026-10-18.md, amended by
notes/eg-new-feature/monitoring-burn-in-timer-and-weekly-retry-2026-10-20.md.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

import pandas as pd
import typer

from readmission_risk import gcp
from readmission_risk.monitoring.burn_in import BURN_IN_TITLE, repo_args
from readmission_risk.monitoring.config import (
    DEFAULT_CONFIG_PATH,
    read_monitoring_config,
)
from readmission_risk.monitoring.store import read_ticks

app = typer.Typer(add_completion=False, rich_markup_mode=None, pretty_exceptions_enable=False)
STREAK = 5


@dataclass(frozen=True)
class Verdict:
    kind: str  # green | red | dropped | skipped
    cadence: str | None
    run: dict


def _gh_json(args: list[str], run: Callable) -> object:
    done = run(["gh", *args], capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {done.stderr.strip()}")
    return json.loads(done.stdout or "null")


def classify(run_info: dict, jobs: list[dict], attempt: int, rows: pd.DataFrame) -> Verdict:
    mine = rows[rows["github_run_url"] == run_info["url"]] if not rows.empty else rows
    latest = None
    if not mine.empty:
        latest = mine.sort_values(["github_run_attempt", "started_at"], na_position="first").iloc[-1]
    cadence = None if latest is None else latest["cadence"]
    score = next((j for j in jobs if j.get("name") == "score"), None)
    if run_info["conclusion"] == "success" and latest is None and score is not None and score.get("conclusion") == "skipped":
        return Verdict("skipped", None, run_info)  # a weekly retry slot that skipped (any attempt): neutral
    if run_info["conclusion"] == "cancelled" and latest is None:
        clock = next((j for j in jobs if j.get("name") == "clock"), None)
        if clock is None or not clock.get("startedAt") or clock["startedAt"].startswith("0001"):
            return Verdict("dropped", None, run_info)
    green = (
        run_info["conclusion"] == "success" and attempt == 1 and latest is not None and latest["status"] == "succeeded"
    )
    return Verdict("green" if green else "red", cadence, run_info)


def burn_in_streak(verdicts: list[Verdict]) -> list[Verdict]:
    """The first 5 consecutive green burn_in verdicts (dropped ones passed over), or [] when there are none. The streak
    is never extended past its 5th green, so a later timer session cannot hide a weekly tick that already met M3."""
    streak: list[Verdict] = []
    for v in verdicts:
        if v.kind == "dropped":
            continue
        if v.kind == "green" and v.cadence == "burn_in":
            streak.append(v)
            if len(streak) == STREAK:
                return streak
        else:
            streak = []
    return []


def first_weekly_after(verdicts: list[Verdict], after: str) -> Verdict | None:
    """The first green weekly verdict created after `after`; red, dropped or skipped scheduled runs before it are passed over."""
    return next((v for v in verdicts if v.run["createdAt"] > after and v.kind == "green" and v.cadence == "weekly"), None)


@app.command(help=__doc__.split("\n\n")[1])
def main(
    impersonate_service_account: Annotated[str | None, typer.Option(help="The ledger-reader SA.")] = None,
    limit: Annotated[int, typer.Option(help="Runs to list per event (dispatch and schedule); scheduled runs are listed from the streak's date.")] = 1000,
) -> None:
    run = subprocess.run
    try:
        config = read_monitoring_config(DEFAULT_CONFIG_PATH)
        gcp.configure(impersonate_service_account=impersonate_service_account)
        rows = read_ticks(gcp.bigquery_client(), config)
        burn_in, scheduled, streak, weekly = collect(rows, limit, lambda args: _gh_json(args, run))
    except Exception as exc:  # noqa: BLE001 -- gh or BigQuery error
        print(f"ERROR: {exc}", file=sys.stderr)
        raise typer.Exit(code=2) from None
    for prefix, verdicts in (("burn-in", burn_in), ("scheduled", scheduled)):
        for v in verdicts:
            print(f"{prefix:<9} {v.run['createdAt']} {v.kind:<7} {v.cadence or '-':<8} {v.run['url']}")
    if not streak:
        print("scheduled not viewed (no burn-in streak yet)")
    met = weekly is not None
    evidence = [*streak, *([weekly] if weekly else [])]
    print("M3 MET" if met else "M3 NOT MET", "; evidence:", ", ".join(v.run["url"] for v in evidence) or "-")
    raise typer.Exit(code=0 if met else 3)


def collect(rows: pd.DataFrame, limit: int, gh_json: Callable[[list[str]], object]):
    """(burn-in verdicts, viewed scheduled verdicts, streak, weekly verdict or None). Scheduled runs are viewed only after
    the streak, oldest first, and only until the first green weekly one (the pure functions decide; this bounds views)."""

    def listed(event: str, *created: str) -> list[dict]:
        runs = gh_json(repo_args("run", "list", "--workflow", "monitor.yml", "--event", event, *created, "--json",
                                 "databaseId,conclusion,createdAt,url,displayTitle", "--limit", str(limit)))  # fmt: skip
        return sorted(runs or [], key=lambda r: r["createdAt"])

    def verdict(info: dict) -> Verdict:
        view = gh_json(repo_args("run", "view", str(info["databaseId"]), "--json", "attempt,jobs"))
        return classify(info, view.get("jobs") or [], int(view.get("attempt") or 1), rows)

    burn_in = [verdict(info) for info in listed("workflow_dispatch") if info.get("displayTitle") == BURN_IN_TITLE]
    streak = burn_in_streak(burn_in)
    scheduled: list[Verdict] = []
    weekly = None
    if streak:
        after = streak[-1].run["createdAt"]
        for info in listed("schedule", "--created", f">={after[:10]}"):  # the limit counts runs from the streak on
            if info["createdAt"] <= after:
                continue
            scheduled.append(verdict(info))
            weekly = first_weekly_after(scheduled, after)
            if weekly is not None:
                break
    return burn_in, scheduled, streak, weekly


if __name__ == "__main__":
    app()
