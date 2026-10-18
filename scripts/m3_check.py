#!/usr/bin/env python
"""Checks metric M3 (PRD): >= 5 consecutive green scheduled burn-in ticks, then >= 1 green weekly tick (v2 slice 7).

A scheduled run counts green only if its Actions conclusion is success AND serving.monitoring holds a succeeded row for
its URL. Any other conclusion, a run cancelled mid-tick, a re-run (attempt > 1) or a green run without a row breaks the
streak; a cancelled run that never started its clock job (a pending run replaced by the concurrency rule) is dropped,
neither green nor red. Needs `gh` and BigQuery read access (laptop: the ledger-reader SA). Exit 0 met, 3 not met, 2 on a
gh or BigQuery error. Design: notes/eg-new-feature/v2-slice7-monitoring-tick-ledger-2026-10-18.md.
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
from readmission_risk.monitoring.config import (
    DEFAULT_CONFIG_PATH,
    read_monitoring_config,
)
from readmission_risk.monitoring.store import read_ticks

app = typer.Typer(add_completion=False, rich_markup_mode=None, pretty_exceptions_enable=False)
STREAK = 5


@dataclass(frozen=True)
class Verdict:
    kind: str  # green | red | dropped
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
    if run_info["conclusion"] == "cancelled" and latest is None:
        clock = next((j for j in jobs if j.get("name") == "clock"), None)
        if clock is None or not clock.get("startedAt") or clock["startedAt"].startswith("0001"):
            return Verdict("dropped", None, run_info)
    green = (
        run_info["conclusion"] == "success" and attempt == 1 and latest is not None and latest["status"] == "succeeded"
    )
    return Verdict("green" if green else "red", cadence, run_info)


def m3_met(verdicts: list[Verdict]) -> tuple[bool, list[Verdict]]:
    """(met, evidence): the first streak of >= 5 consecutive green burn-in runs, then the first green weekly run after."""
    streak: list[Verdict] = []
    end = None
    for i, v in enumerate(verdicts):
        if v.kind == "dropped":
            continue
        if v.kind == "green" and v.cadence == "burn_in":
            streak.append(v)
            if len(streak) >= STREAK:
                end = i
        else:
            if end is not None:
                break
            streak = []
    if end is None:
        return False, streak
    weekly = next((v for v in verdicts[end + 1 :] if v.kind == "green" and v.cadence == "weekly"), None)
    return weekly is not None, [*streak, *([weekly] if weekly else [])]


@app.command(help=__doc__.split("\n\n")[1])
def main(
    impersonate_service_account: Annotated[str | None, typer.Option(help="The ledger-reader SA.")] = None,
    limit: Annotated[int, typer.Option(help="Scheduled runs to inspect.")] = 200,
) -> None:
    run = subprocess.run
    try:
        config = read_monitoring_config(DEFAULT_CONFIG_PATH)
        gcp.configure(impersonate_service_account=impersonate_service_account)
        rows = read_ticks(gcp.bigquery_client(), config)
        runs = _gh_json(["run", "list", "--workflow", "monitor.yml", "--event", "schedule", "--json",
                         "databaseId,conclusion,createdAt,url", "--limit", str(limit)], run)  # fmt: skip
        verdicts = []
        for info in sorted(runs, key=lambda r: r["createdAt"]):
            view = _gh_json(["run", "view", str(info["databaseId"]), "--json", "attempt,jobs"], run)
            verdicts.append(classify(info, view.get("jobs") or [], int(view.get("attempt") or 1), rows))
    except Exception as exc:  # noqa: BLE001 -- gh or BigQuery error
        print(f"ERROR: {exc}", file=sys.stderr)
        raise typer.Exit(code=2) from None
    met, evidence = m3_met(verdicts)
    for v in verdicts:
        print(f"{v.run['createdAt']} {v.kind:<7} {v.cadence or '-':<8} {v.run['url']}")
    print("M3 MET" if met else "M3 NOT MET", "; evidence:", ", ".join(v.run["url"] for v in evidence) or "-")
    raise typer.Exit(code=0 if met else 3)


if __name__ == "__main__":
    app()
