#!/usr/bin/env python
"""Thin Typer wrapper around readmission_risk.warehouse.dashboard_access_check.run_access_checks (v3 slice 2).

Proves the dashboard runtime SA's limits live: as dashboard-runtime, reading any serving table, creating a view in the dashboard
dataset and listing scratch / scratch_gold must each be denied, and each authorized view must expose exactly its designed
columns. Prints one line per probe and writes a JSON report under data/checks/dashboard_access_check/. Exit codes: 0 all denied
and all views as designed; 3 a probe was ALLOWED or a view is wrong (a FAIL; a created view is deleted at once); 5 a probe or
check failed for another reason; 2 an empty --dashboard-sa. Always impersonates (the operator could read serving directly).
Run as the operator (make dashboard-access-check). Design: notes/eg-new-feature/v3-slice2-authorized-views-2026-10-31.md.
"""

import dataclasses
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from readmission_risk import gcp
from readmission_risk.warehouse.dashboard_access_check import run_access_checks
from readmission_risk.warehouse.gold_access_check import ProbeResult, exit_code

REPORT_DIR = Path("data/checks/dashboard_access_check")
DASHBOARD_SA = "dashboard-runtime@synthea-readmission-risk.iam.gserviceaccount.com"

app = typer.Typer(add_completion=False, rich_markup_mode=None, pretty_exceptions_enable=False)


def make_client(email: str):
    gcp.configure(impersonate_service_account=email)
    return gcp.bigquery_client()


@app.command(help=__doc__.split("\n\n")[1])
def main(
    dashboard_sa: Annotated[str, typer.Option(help="The dashboard runtime service account (always impersonated).")] = DASHBOARD_SA,
) -> None:
    if not dashboard_sa.strip():
        print("--dashboard-sa must name the service account to impersonate")
        raise typer.Exit(code=2)
    try:
        results = run_access_checks(make_client, dashboard_sa=dashboard_sa)
    except Exception as exc:  # noqa: BLE001 -- reported, never a traceback; the report is still written
        results = [ProbeResult(dashboard_sa, "run the access checks", "error", f"{type(exc).__name__}: {exc}"[:500])]
    for r in results:
        print(f"{r.outcome:<8} {r.probe}: {r.detail}")
    now = datetime.now(UTC)
    path = REPORT_DIR / f"{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {"checked_at": now.isoformat(), "dashboard_sa": dashboard_sa, "probes": [dataclasses.asdict(r) for r in results]}
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    print(f"report: {path}")
    raise typer.Exit(code=exit_code(results))


if __name__ == "__main__":
    app()
