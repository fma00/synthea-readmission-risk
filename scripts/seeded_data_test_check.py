#!/usr/bin/env python
"""Thin Typer wrapper around readmission_risk.warehouse.seeded_check.seeded_check (PRD M5).

Copies one published run date's prediction rows and the scoring_runs history into the scratch dataset, checks that the
clean copies pass their ASSERT data tests, then gives every assertion its own copy with one seeded bad row and checks
that the script fails with exactly that assertion. Prints one line per result, then "M5 OK" (exit 0) or the results so
far plus "ERROR: <message>" (exit 1). Run as the scorer service account (make m5-check). With --monitoring instead, it
seeds synthetic serving.monitoring rows (no run date; v2 slice 7), run as the monitor service account (make m5-monitoring).
Designs: notes/eg-new-feature/v2-slice4-score-to-bigquery-2026-10-11.md,
notes/eg-new-feature/v2-slice7-monitoring-tick-ledger-2026-10-18.md.
"""

import sys
import traceback
from typing import Annotated

import typer

from readmission_risk import gcp
from readmission_risk.pipeline.gold_metadata import parse_yyyymmdd
from readmission_risk.warehouse.seeded_check import (
    SeededCheckError,
    seeded_check,
    seeded_monitoring_check,
)

app = typer.Typer(add_completion=False, rich_markup_mode=None, pretty_exceptions_enable=False)


def _lines(results) -> list[str]:
    lines = []
    for kind in ("predictions", "scoring_runs", "monitoring"):
        for label, result in results.get(kind, {}).items():
            outcome = "pass" if result.passed else f"fail {result.failure}"
            lines.append(f"{kind:<13} {label:<7}: {outcome}")
    return lines


def _check_mode(run_date: str | None, monitoring: bool) -> None:
    if monitoring == (run_date is not None):
        raise ValueError("pass exactly one of --run-date and --monitoring")


@app.command(help=__doc__.split("\n\n")[1])
def main(
    run_date: Annotated[str | None, typer.Option(help="YYYYMMDD: a run date that has a published partition.")] = None,
    monitoring: Annotated[bool, typer.Option("--monitoring", help="Check the serving.monitoring assertions instead.")] = False,
    impersonate_service_account: Annotated[str | None, typer.Option(help="Run as this service account.")] = None,
    verbose: Annotated[bool, typer.Option("--verbose", help="Also print the traceback of errors.")] = False,
) -> None:
    try:
        _check_mode(run_date, monitoring)
        gcp.configure(impersonate_service_account=impersonate_service_account)
        if monitoring:
            results = seeded_monitoring_check(gcp.bigquery_client())
        else:
            results = seeded_check(gcp.bigquery_client(), run_date=parse_yyyymmdd(run_date, "run_date"))
    except SeededCheckError as exc:
        print("\n".join(_lines(exc.results)))
        print(f"ERROR: {exc}", file=sys.stderr)
        raise typer.Exit(code=1) from None
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        if verbose:
            traceback.print_exc()
        raise typer.Exit(code=1) from None
    print("\n".join(_lines(results)))
    if monitoring:
        n = len(results["monitoring"]) - 1
        print(f"M5 OK: all {n} monitoring assertions fired on their seeded bad row, and the clean copy passed")
        return
    n = len(results["predictions"]) + len(results["scoring_runs"]) - 2
    print(f"M5 OK: all {n} assertions fired on their seeded bad row, and both clean copies passed")


if __name__ == "__main__":
    app()
