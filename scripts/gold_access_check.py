#!/usr/bin/env python
"""Thin Typer wrapper around readmission_risk.warehouse.gold_access_check.run_access_checks (v3 slice 1).

Proves the gold access limits live: as gold-publisher, creating a table in serving, changing serving.predictions and reading
serving.scoring_runs must each be denied; as the scorer and the monitor service accounts, reading serving.gold must be denied.
Prints one line per probe and writes a JSON report under data/checks/gold_access_check/. Exit codes: 0 every probe denied;
3 a probe was ALLOWED (a FAIL; a created table is deleted at once); 5 a probe failed for another reason. Run as the operator,
who may impersonate all three (make gold-access-check). Design: notes/eg-new-feature/v3-slice1-gold-bigquery-2026-10-31.md.
"""

import dataclasses
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from readmission_risk import gcp
from readmission_risk.warehouse.gold_access_check import (
    ProbeResult,
    exit_code,
    run_access_checks,
)

REPORT_DIR = Path("data/checks/gold_access_check")
GOLD_SA = "gold-publisher@synthea-readmission-risk.iam.gserviceaccount.com"
SCORER_SA = "scorer@synthea-readmission-risk.iam.gserviceaccount.com"
MONITOR_SA = "monitor@synthea-readmission-risk.iam.gserviceaccount.com"

app = typer.Typer(add_completion=False, rich_markup_mode=None, pretty_exceptions_enable=False)


def make_client(email: str):
    gcp.configure(impersonate_service_account=email)
    return gcp.bigquery_client()


@app.command(help=__doc__.split("\n\n")[1])
def main(
    gold_sa: Annotated[str, typer.Option(help="The gold publisher service account.")] = GOLD_SA,
    scorer_sa: Annotated[str, typer.Option(help="The scorer service account.")] = SCORER_SA,
    monitor_sa: Annotated[str, typer.Option(help="The monitor service account.")] = MONITOR_SA,
) -> None:
    try:
        results = run_access_checks(make_client, gold_sa=gold_sa, scorer_sa=scorer_sa, monitor_sa=monitor_sa)
    except Exception as exc:  # noqa: BLE001 -- reported, never a traceback; the report is still written
        results = [ProbeResult("-", "run the access checks", "error", f"{type(exc).__name__}: {exc}"[:500])]
    for r in results:
        print(f"{r.outcome:<8} {r.sa.split('@')[0]:<15} {r.probe}: {r.detail}")
    now = datetime.now(UTC)
    path = REPORT_DIR / f"{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {"probes": [dataclasses.asdict(r) for r in results], "checked_at": now.isoformat()}
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    print(f"report: {path}")
    raise typer.Exit(code=exit_code(results))


if __name__ == "__main__":
    app()
