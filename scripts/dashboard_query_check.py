#!/usr/bin/env python
"""Thin Typer wrapper around readmission_risk.warehouse.dashboard_query_check.run_query_checks (v3 slice 2).

Proves the dashboard's fixed queries live, as dashboard-runtime: the published dates up to the simulated clock (Q0) and the
controls that the fingerprint and clock rules exclude something (Q0b), dry-run bytes under the cap (Q1), partition pruning (Q2),
the partition-filter requirement surviving the view (Q3), and the clock's call sheet against the local gold (Q4). Prints every
result and measurement and writes a JSON report under data/checks/dashboard_query_check/. Exit codes: 0 all pass; 3 a check
failed; 5 a check errored (including a client that cannot be built); 2 nothing ran (an empty --dashboard-sa, or the local gold
cannot be loaded or is not the serving gold). Always impersonates (the operator could read serving directly). Run as the
operator after make gold-200k (make dashboard-query-check). Design: notes/eg-new-feature/v3-slice2-authorized-views-2026-10-31.md.
"""

import dataclasses
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from readmission_risk import gcp
from readmission_risk.models.data import load_gold_table_with_metadata
from readmission_risk.warehouse.bigquery import SERVING_GOLD_FINGERPRINT
from readmission_risk.warehouse.dashboard_query_check import (
    CheckResult,
    QueryCheckReport,
    exit_code,
    run_query_checks,
)

REPORT_DIR = Path("data/checks/dashboard_query_check")
DASHBOARD_SA = "dashboard-runtime@synthea-readmission-risk.iam.gserviceaccount.com"
GOLD_DIR = Path("data/gold/main_200k")

app = typer.Typer(add_completion=False, rich_markup_mode=None, pretty_exceptions_enable=False)


def make_client(email: str):
    gcp.configure(impersonate_service_account=email)
    return gcp.bigquery_client()


@app.command(help=__doc__.split("\n\n")[1])
def main(
    dashboard_sa: Annotated[str, typer.Option(help="The dashboard runtime service account (always impersonated).")] = DASHBOARD_SA,
    gold_dir: Annotated[Path, typer.Option(help="The local copy of the serving gold (make gold-200k).")] = GOLD_DIR,
) -> None:
    if not dashboard_sa.strip():
        print("--dashboard-sa must name the service account to impersonate")
        raise typer.Exit(code=2)
    try:
        gold, metadata = load_gold_table_with_metadata(gold_dir)
    except Exception as exc:  # noqa: BLE001 -- nothing ran; reported without a traceback
        print(f"cannot load the local gold {gold_dir}: {type(exc).__name__}: {exc}")
        raise typer.Exit(code=2) from None
    if metadata.gold_fingerprint != SERVING_GOLD_FINGERPRINT:
        print(f"{gold_dir} has fingerprint {metadata.gold_fingerprint}, not the serving gold's {SERVING_GOLD_FINGERPRINT}")
        raise typer.Exit(code=2)
    try:
        client = make_client(dashboard_sa)
    except Exception as exc:  # noqa: BLE001 -- recorded; the report is still written
        report = QueryCheckReport([CheckResult("build client", "error", f"{type(exc).__name__}: {exc}"[:500])])
    else:
        try:
            report = run_query_checks(client, gold=gold)
        except Exception as exc:  # noqa: BLE001 -- reported, never a traceback
            report = QueryCheckReport([CheckResult("run the query checks", "error", f"{type(exc).__name__}: {exc}"[:500])])
    for r in report.results:
        print(f"{r.outcome:<6} {r.check}: {r.detail}")
    for key, value in sorted(report.measurements.items()):
        print(f"measurement {key}: {value}")
    now = datetime.now(UTC)
    path = REPORT_DIR / f"{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "checked_at": now.isoformat(),
        "dashboard_sa": dashboard_sa,
        "gold_dir": str(gold_dir),
        "checks": [dataclasses.asdict(r) for r in report.results],
        "measurements": report.measurements,
    }
    path.write_text(json.dumps(document, indent=2, sort_keys=True, default=str) + "\n")
    print(f"report: {path}")
    raise typer.Exit(code=exit_code(report.results))


if __name__ == "__main__":
    app()
