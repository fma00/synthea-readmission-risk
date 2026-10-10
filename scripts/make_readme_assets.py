#!/usr/bin/env python
"""Thin Typer wrapper around readmission_risk.reporting.assets.build_assets.

All real logic lives in the readmission_risk.reporting package, which is unit tested; the design is in
notes/eg-new-feature/readme-reliability-plot-and-sample-table-2026-10-09.md.

Exit code 0 on success. Exit 1, printing "ERROR: <message>" to stderr, on FileNotFoundError or ValueError (a bad path, a
run/predictions mismatch, a curve that does not reconcile with its run). Typer's own usage errors exit 2.
"""

import sys
import traceback
from pathlib import Path
from typing import Annotated

import typer

from readmission_risk.reporting.assets import AssetConfig, build_assets

app = typer.Typer(add_completion=False, rich_markup_mode=None, pretty_exceptions_enable=False)


@app.command(
    help="Regenerates the README review assets: the reliability figure (light and dark SVG, plus low-resolution PNG previews) "
    "from the two models' finished MLflow runs, and the 5-row sample Top-N table from one predictions partition."
)
def main(
    experiment_name: Annotated[str, typer.Option(help="MLflow experiment holding the two runs (the real runs: readmission-risk-v1).")],
    predictions: Annotated[Path, typer.Option(help="A predictions.parquet written by scripts/score_discharges.py.")],
    tracking_dir: Annotated[Path, typer.Option(help="Directory holding the MLflow store (mlflow.db).")] = Path("mlflow"),
    out_dir: Annotated[Path, typer.Option(help="Where the SVGs and sample_topn.md go (previews go in <out-dir>/preview).")] = Path("docs/img"),
    top_n_rows: Annotated[int, typer.Option(help="Rows in the sample table.")] = 5,
    verbose: Annotated[bool, typer.Option("--verbose", help="Also print the traceback of the errors reported as ERROR.")] = False,
) -> None:
    config = AssetConfig(
        tracking_dir=tracking_dir,
        experiment_name=experiment_name,
        predictions_path=predictions,
        out_dir=out_dir,
        top_n_rows=top_n_rows,
    )
    try:
        written = build_assets(config)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        if verbose:
            traceback.print_exc()
        raise typer.Exit(code=1) from None
    for path in written:
        print(f"Wrote {path}")
    print()
    print((out_dir / "sample_topn.md").read_text(encoding="utf-8"), end="")


if __name__ == "__main__":
    app()
