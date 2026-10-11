#!/usr/bin/env python
"""Thin Typer wrapper around readmission_risk.warehouse.seeded_check.seeded_check (PRD M5).

Copies one published run date's prediction rows and the scoring_runs history into the scratch dataset, checks that the
clean copies pass their ASSERT data tests, then gives every assertion its own copy with one seeded bad row and checks
that the script fails with exactly that assertion. Prints one line per result, then "M5 OK" (exit 0) or the results so
far plus "ERROR: <message>" (exit 1). Run as the scorer service account (make m5-check). Design:
notes/eg-new-feature/v2-slice4-score-to-bigquery-2026-10-11.md.
"""

import sys
import traceback
from typing import Annotated

import typer

from readmission_risk import gcp
from readmission_risk.pipeline.gold_metadata import parse_yyyymmdd
from readmission_risk.warehouse.seeded_check import SeededCheckError, seeded_check

app = typer.Typer(add_completion=False, rich_markup_mode=None, pretty_exceptions_enable=False)


def _lines(results) -> list[str]:
    lines = []
    for kind in ("predictions", "scoring_runs"):
        for label, result in results.get(kind, {}).items():
            outcome = "pass" if result.passed else f"fail {result.failure}"
            lines.append(f"{kind:<13} {label:<7}: {outcome}")
    return lines


@app.command(help=__doc__.split("\n\n")[1])
def main(
    run_date: Annotated[str, typer.Option(help="YYYYMMDD: a run date that has a published partition.")],
    impersonate_service_account: Annotated[str | None, typer.Option(help="Run as this service account.")] = None,
    verbose: Annotated[bool, typer.Option("--verbose", help="Also print the traceback of errors.")] = False,
) -> None:
    try:
        gcp.configure(impersonate_service_account=impersonate_service_account)
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
    n = len(results["predictions"]) + len(results["scoring_runs"]) - 2
    print(f"M5 OK: all {n} assertions fired on their seeded bad row, and both clean copies passed")


if __name__ == "__main__":
    app()
