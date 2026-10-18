#!/usr/bin/env python
"""Thin Typer wrapper around readmission_risk.warehouse.run.score_to_bigquery.

Scores one run date with the unchanged scorer, loads the rows into a staging table, runs the ASSERT data tests, publishes
them onto serving.predictions only if they pass, and records the attempt in serving.scoring_runs. Exit codes: 0 succeeded
and recorded; 1 refused ("ERROR: <message>"); 3 data tests failed ("DATA TESTS FAILED: <failure>"); 4 succeeded but not
recorded; a failed run re-raises its exception (a traceback). Needs Application Default Credentials (laptop: pass
--impersonate-service-account; CI: the workflow's keyless WIF credentials). Design:
notes/eg-new-feature/v2-slice4-score-to-bigquery-2026-10-11.md.
"""

import dataclasses
import os
import sys
import traceback
from pathlib import Path
from typing import Annotated

import typer

from readmission_risk import gcp
from readmission_risk.scoring.constants import DEFAULT_TOP_N, DEMO_NOTICE
from readmission_risk.scoring.report import format_report
from readmission_risk.warehouse.run import (
    BigQueryRunRequest,
    BigQueryRunResult,
    score_to_bigquery,
)

app = typer.Typer(add_completion=False, rich_markup_mode=None, pretty_exceptions_enable=False)

EXIT_REFUSED, EXIT_DATA_TESTS_FAILED, EXIT_NOT_RECORDED = 1, 3, 4


def _tests_line(result: BigQueryRunResult) -> str:
    def state(value):
        return "not run" if value is None else ("pass" if value else "fail")

    record = result.record
    return f"history {state(record.history_tests_passed)}, predictions {state(record.predictions_tests_passed)}"


def _failure(result: BigQueryRunResult) -> str:
    record = result.record
    if record.data_test_failure:
        return record.data_test_failure
    if record.error_type:
        return f"{record.error_type}: {(record.error_message or '').splitlines()[0] if record.error_message else ''}"
    return "-"


def job_summary(result: BigQueryRunResult) -> str:
    record = result.record
    rows = [
        ("run date", record.run_date.isoformat() if record.run_date else "-"),
        ("scoring run id", record.scoring_run_id),
        ("bundle", record.model_bundle_id[:12] if record.model_bundle_id else "-"),
        ("rows / Top-N", f"{record.n_predictions if record.n_predictions is not None else '-'} / "
                         f"{record.n_top_n if record.n_top_n is not None else '-'}"),
        ("data tests", _tests_line(result)),
        ("published", "yes" if record.published else "no"),
        ("recorded", "yes" if result.recorded else "no"),
        ("failure", _failure(result).replace("|", "\\|")),
    ]  # fmt: skip
    table = "\n".join(f"| {key} | {value} |" for key, value in rows)
    return f"## Scoring run {record.status}\n\n| field | value |\n|---|---|\n{table}\n"


def _destination(result: BigQueryRunResult) -> str:
    record = result.record
    if record.published and record.run_date is not None:
        return f"bigquery://{record.predictions_table} (as_of_date = {record.run_date.isoformat()})"
    return "nothing (not published; the staging table was dropped)"


@app.command(help=DEMO_NOTICE + "\n\n" + __doc__.split("\n\n")[1])
def main(
    gold_dir: Annotated[str, typer.Option(help="Gold table: a gs:// prefix or a local directory.")],
    run_date: Annotated[str, typer.Option(help="YYYYMMDD: the last day of the discharge window.")],
    bundle: Annotated[str | None, typer.Option(help="A bundle (gs:// or local) instead of the champion.")] = None,
    champion: Annotated[Path | None, typer.Option(help="The champion pointer (default deploy/champion.json).")] = None,
    top_n: Annotated[int, typer.Option(help="How many of the highest-risk discharges to flag.")] = DEFAULT_TOP_N,
    window_days: Annotated[int | None, typer.Option(help="Window length in UTC calendar days (default: 30).")] = None,
    impersonate_service_account: Annotated[
        str | None, typer.Option(help="Run as this service account (laptop: the scorer SA).")
    ] = None,
    verbose: Annotated[bool, typer.Option("--verbose", help="Also print the traceback of refusals.")] = False,
) -> None:
    try:
        gcp.configure(impersonate_service_account=impersonate_service_account)
    except ValueError as exc:  # before any client exists: unrecorded
        print(f"ERROR: {exc}", file=sys.stderr)
        raise typer.Exit(code=EXIT_REFUSED) from None
    request = BigQueryRunRequest(
        gold_uri=gold_dir, run_date=run_date, bundle_uri=bundle, champion_path=champion, top_n=top_n, window_days=window_days
    )
    result = score_to_bigquery(request)
    record = result.record
    if result.scoring is not None:
        print(format_report(dataclasses.replace(result.scoring, partition_path=_destination(result))), end="")
    print(
        f"Scoring run {record.scoring_run_id}: {record.status} "
        f"(published: {'yes' if record.published else 'no'}; recorded: {'yes' if result.recorded else 'no'})"
    )
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(job_summary(result))
    output_path = os.environ.get("GITHUB_OUTPUT")  # v2 slice 7: the monitoring tick's evaluate job reads these
    if output_path:
        with open(output_path, "a", encoding="utf-8") as handle:
            handle.write(f"scoring_run_id={record.scoring_run_id}\nstatus={record.status}\n")

    if record.status == "failed":
        raise result.error
    if record.status == "refused":
        print(f"ERROR: {result.error}", file=sys.stderr)
        if verbose and result.error is not None:
            traceback.print_exception(result.error)
        raise typer.Exit(code=EXIT_REFUSED)
    if record.status == "data_tests_failed":
        print(f"DATA TESTS FAILED: {record.data_test_failure}", file=sys.stderr)
        raise typer.Exit(code=EXIT_DATA_TESTS_FAILED)
    if not result.recorded:
        print(f"ERROR: not recorded: {result.error}", file=sys.stderr)
        raise typer.Exit(code=EXIT_NOT_RECORDED)


if __name__ == "__main__":
    app()
