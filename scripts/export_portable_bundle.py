#!/usr/bin/env python
"""Thin Typer wrapper around readmission_risk.portable.export.export_portable_bundle.

Exports a local schema-1 model bundle once more as a schema-2 bundle carrying the portable (non-pickle) form of its model
and a parity reference (FR0.3), and prints its id, its URI, the parity result and a "not promoted" line: the champion
pointer is not changed. Nothing is written unless the portable form reproduces the pickle exactly (max abs diff 0) on
every test-partition row and every probe row. Exit 0 on success; exit 1 with "ERROR: <message>" on FileNotFoundError or
ValueError; Typer usage errors exit 2. --bundles-dir may be a gs:// bucket or prefix (create-only upload, run as the
bundle-writer service account; that identity cannot read, so the status is always "created"). Design:
notes/eg-new-feature/v2-slice6c-portable-v1-export-2026-10-17.md.
"""

import sys
from pathlib import Path
from typing import Annotated

import typer

from readmission_risk import gcp
from readmission_risk.bundles.store import printable_bundle_uri
from readmission_risk.portable.export import DESIGN_NOTE, export_portable_bundle

app = typer.Typer(add_completion=False, rich_markup_mode=None, pretty_exceptions_enable=False)


@app.command(help=__doc__.split("\n\n")[1])
def main(
    source_bundle: Annotated[str, typer.Option(help="The local schema-1 bundle directory to export.")],
    gold_dir: Annotated[Path, typer.Option(help="The gold table the bundle was trained on.")] = Path("data/gold/full_run_2b"),
    bundles_dir: Annotated[str, typer.Option(help="Root of the bundles: a directory, or a gs:// bucket or prefix.")] = "bundles",
    impersonate_service_account: Annotated[
        str | None, typer.Option(help="Upload as this service account (for gs://: bundle-writer; needs ADC).")
    ] = None,
) -> None:
    try:
        gcp.configure(impersonate_service_account=impersonate_service_account)
        root = bundles_dir if bundles_dir.startswith("gs://") else Path(bundles_dir)
        result = export_portable_bundle(source_bundle, gold_dir, root)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise typer.Exit(code=1) from None
    uri, is_relative = printable_bundle_uri(result.bundle_uri)
    if not is_relative:
        print("WARNING: bundle_uri is an absolute, machine-specific path", file=sys.stderr)
    print(f"bundle_id: {result.bundle_id}")
    print(f"bundle_uri: {uri}")
    print(f"status: {'created' if result.created else 'already existed'}")
    print(f"source_bundle_id: {result.source_bundle_id}")
    print(f"parity: max abs diff {result.max_abs_diff} on {result.parity_rows} test rows and {result.probe_rows} probe rows")
    print(f"not promoted: deploy/champion.json stays on the schema-1 bundle ({DESIGN_NOTE})")


if __name__ == "__main__":
    app()
