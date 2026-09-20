#!/usr/bin/env python
"""Thin Typer wrapper around readmission_risk.scoring.pipeline.score_run_date.

All real logic lives in the readmission_risk.scoring package, which is fully unit tested; the design is in
notes/eg-new-feature/batch-scoring-cli-2026-09-20.md.

Exit code 0 on success. Exit 1, printing "ERROR: <message>" to stderr, on FileNotFoundError or ValueError -- the two
user-correctable failure types (bad path, a refused run/gold pair, a window that touches in-sample rows, a bad flag). A
RuntimeError (a broken model or a violated post-condition) and MLflow/OS errors are deliberately NOT caught: they propagate
as an ordinary traceback because they indicate a bug or a broken environment. Typer's own usage errors (a missing required
option, a non-integer --top-n) exit 2. --verbose also prints the traceback of the reported errors. MLflow's pickle-safety
warning on model load is not suppressed.
"""

import sys
import traceback
from pathlib import Path
from typing import Annotated

import typer

from readmission_risk.scoring.constants import DEFAULT_TOP_N, DEMO_NOTICE
from readmission_risk.scoring.pipeline import ScoringConfig, score_run_date
from readmission_risk.scoring.report import format_report

app = typer.Typer(add_completion=False, rich_markup_mode=None, pretty_exceptions_enable=False)

_HELP = (
    DEMO_NOTICE
    + "\n\nScores the held-out discharges whose discharge time falls in the trailing window of --window-days UTC "
    "calendar days ending on --run-date (inclusive), ranks them by predicted risk and prints the Top-N. The full ranked "
    "batch is written to <output-dir>/run_date=YYYYMMDD/predictions.parquet; re-running a run date replaces that "
    "partition, so scoring the same date with the other model replaces the first model's file. A window that contains "
    "any discharge that is not a held-out row of the chosen model (a training row, or a row dropped by the split) is "
    "refused."
)


@app.command(help=_HELP)
def main(
    gold_dir: Annotated[Path, typer.Option(help="Gold table directory (Parquet part files + _gold_metadata.json).")],
    run_date: Annotated[str, typer.Option(help="YYYYMMDD: the last day of the discharge window.")],
    output_dir: Annotated[Path, typer.Option(help="Root of the prediction partitions.")] = Path("data/predictions"),
    tracking_dir: Annotated[Path, typer.Option(help="Directory holding the MLflow store (mlflow.db).")] = Path("mlflow"),
    experiment_name: Annotated[
        str, typer.Option(help="MLflow experiment holding the runs (ignored with --run-id); the real 2b runs are in readmission-risk-2b.")
    ] = "readmission-risk",
    model: Annotated[
        str | None,
        typer.Option(
            help="logistic_regression (the default when neither --model nor --run-id is given) or xgboost_calibrated: "
            "the single FINISHED run of that model in --experiment-name. Mutually exclusive with --run-id."
        ),
    ] = None,
    run_id: Annotated[str | None, typer.Option(help="An explicit MLflow run id. Mutually exclusive with --model.")] = None,
    top_n: Annotated[int, typer.Option(help="How many of the highest-risk discharges to print.")] = DEFAULT_TOP_N,
    window_days: Annotated[
        int | None, typer.Option(help="Window length in UTC calendar days (default: the gold table's readmission window, 30).")
    ] = None,
    show_observed_outcomes: Annotated[
        bool,
        typer.Option(
            "--show-observed-outcomes",
            help="Also print how the gold table's observed readmissions line up with the Top-N (a development-set sanity "
            "check, joined after scoring and never written to the output file).",
        ),
    ] = False,
    verbose: Annotated[
        bool, typer.Option("--verbose", help="Also print the traceback of the errors reported as ERROR.")
    ] = False,
) -> None:
    config = ScoringConfig(
        gold_dir=gold_dir,
        run_date=run_date,
        output_dir=output_dir,
        tracking_dir=tracking_dir,
        experiment_name=experiment_name,
        model_name=model,
        run_id=run_id,
        top_n=top_n,
        window_days=window_days,
        show_observed_outcomes=show_observed_outcomes,
    )
    try:
        result = score_run_date(config)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        if verbose:
            traceback.print_exc()
        raise typer.Exit(code=1) from None
    print(format_report(result), end="")


if __name__ == "__main__":
    app()
