#!/usr/bin/env python
"""Thin Typer wrapper around readmission_risk.models.bundle_export.export_bundle.

Exports one MLflow run as an immutable model bundle under --bundles-dir and prints its id, its URI and the
deploy/champion.json text that would promote it (paste it into a promotion PR). A run that is not FINISHED, or is deleted,
is still exported (its status is recorded) but gets a stderr WARNING: the scorer refuses that bundle (guard 2). Choose the run with --run-id, or with
--experiment-name plus --model (the single FINISHED run of that model). Exit 0 on success; exit 1 with "ERROR: <message>"
on FileNotFoundError or ValueError; Typer usage errors (including a wrong option combination) exit 2. Design:
notes/eg-new-feature/v2-slice2-model-bundle-and-manifest-2026-10-11.md. Since v2 slice 4 --bundles-dir may be a gs://
bucket or prefix: the upload is create-only (if_generation_match=0), run as the bundle-writer service account
(--impersonate-service-account), and an existing bundle is reported as "already exists" (that identity cannot read).
"""

import sys
from pathlib import Path
from typing import Annotated

import typer

from readmission_risk import gcp
from readmission_risk.bundles.champion import champion_json
from readmission_risk.models.bundle_export import export_bundle
from readmission_risk.models.tracking import resolve_run_id

app = typer.Typer(add_completion=False, rich_markup_mode=None, pretty_exceptions_enable=False)


def _printable_uri(bundle_uri: str) -> tuple[str, bool]:
    """(uri, is_relative): a gs:// URI unchanged (it is location-independent); a local bundle relative to the current
    directory with POSIX separators when it lies inside it, otherwise the absolute path."""
    if bundle_uri.startswith("gs://"):
        return bundle_uri, True
    resolved, cwd = Path(bundle_uri).resolve(), Path.cwd().resolve()
    if resolved.is_relative_to(cwd):
        return resolved.relative_to(cwd).as_posix(), True
    return str(resolved), False


@app.command(help=__doc__.split("\n\n")[1])
def main(
    tracking_dir: Annotated[Path, typer.Option(help="Directory holding the MLflow store (mlflow.db).")] = Path("mlflow"),
    bundles_dir: Annotated[str, typer.Option(help="Root of the bundles: a directory, or a gs:// bucket or prefix.")] = "bundles",
    run_id: Annotated[str | None, typer.Option(help="The MLflow run to export. Excludes --experiment-name/--model.")] = None,
    experiment_name: Annotated[str | None, typer.Option(help="With --model: the experiment holding the run.")] = None,
    model: Annotated[str | None, typer.Option(help="With --experiment-name: logistic_regression or xgboost_calibrated.")] = None,
    impersonate_service_account: Annotated[
        str | None, typer.Option(help="Upload as this service account (for gs://: bundle-writer; needs ADC).")
    ] = None,
) -> None:
    by_name = experiment_name is not None or model is not None
    if (run_id is None) == (not by_name) or (by_name and (experiment_name is None or model is None)):
        raise typer.BadParameter("pass either --run-id, or both --experiment-name and --model")
    try:
        gcp.configure(impersonate_service_account=impersonate_service_account)
        chosen = run_id if run_id is not None else resolve_run_id(tracking_dir, experiment_name, model)
        root = bundles_dir if bundles_dir.startswith("gs://") else Path(bundles_dir)
        result = export_bundle(tracking_dir, chosen, root)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise typer.Exit(code=1) from None
    if result.run_status != "FINISHED" or result.run_lifecycle_stage != "active":
        print(
            f"WARNING: run status is {result.run_status} and lifecycle stage is {result.run_lifecycle_stage}; the scorer "
            "will refuse this bundle (guard 2), so do not promote it",
            file=sys.stderr,
        )
    uri, is_relative = _printable_uri(result.bundle_uri)
    if not is_relative:
        print("WARNING: bundle_uri is an absolute, machine-specific path; do not commit it", file=sys.stderr)
    print(f"bundle_id: {result.bundle_id}")
    print(f"bundle_uri: {uri}")
    print(f"status: {'created' if result.created else 'already existed'}")
    print()
    print("champion.json for this bundle:")
    print(champion_json(uri, result.manifest_sha256), end="")


if __name__ == "__main__":
    app()
