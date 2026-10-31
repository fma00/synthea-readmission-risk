#!/usr/bin/env python
"""Thin Typer wrapper around readmission_risk.warehouse.gold_seeded_check.seeded_gold_check (v3 slice 1, M5 for GOLD).

Builds a literal gold fixture and a one-row predictions table in scratch_gold, checks that the clean pair passes the GOLD
data tests, then gives every GOLD assertion its own pair with one seeded bad row and checks that the script fails with exactly
that assertion. Prints one line per key and writes a JSON report under data/checks/gold_seeded_check/. Exit codes: 0 every
assertion fired and the clean pair passed; 3 not as expected; 5 a non-ASSERT error (the results so far are printed). Run as
the gold-publisher service account (make gold-seeded-check). Design: notes/eg-new-feature/v3-slice1-gold-bigquery-2026-10-31.md.
"""

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from readmission_risk import gcp
from readmission_risk.warehouse.gold_seeded_check import (
    GoldSeededCheckError,
    expected,
    seeded_gold_check,
)

DEFAULT_SERVICE_ACCOUNT = "gold-publisher@synthea-readmission-risk.iam.gserviceaccount.com"
REPORT_DIR = Path("data/checks/gold_seeded_check")

app = typer.Typer(add_completion=False, rich_markup_mode=None, pretty_exceptions_enable=False)


def _git_head() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip() or None
    except (OSError, subprocess.CalledProcessError):
        return None


def _write(results, error: str | None) -> Path:
    now = datetime.now(UTC)
    path = REPORT_DIR / f"{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    document = {
        "results": {k: {"passed": r.passed, "failure": r.failure} for k, r in results.items()},
        "error": error,
        "checked_at": now.isoformat(),
        "git_head": _git_head(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    return path


@app.command(help=__doc__.split("\n\n")[1])
def main(
    impersonate_service_account: Annotated[str, typer.Option(help="Run as this service account.")] = DEFAULT_SERVICE_ACCOUNT,
) -> None:
    try:
        gcp.configure(impersonate_service_account=impersonate_service_account)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--impersonate-service-account") from None
    error = None
    try:
        results = seeded_gold_check(gcp.bigquery_client())
    except GoldSeededCheckError as exc:
        results, error = exc.results, str(exc)
    except Exception as exc:  # noqa: BLE001 -- e.g. no credentials: reported, never a traceback
        results, error = {}, f"{type(exc).__name__}: {exc}"
    for key, result in results.items():
        print(f"{key:<8}: {'pass' if result.passed else f'fail {result.failure}'}")
    print(f"report: {_write(results, error)}")
    if error is not None:
        print(f"ERROR: {error}", file=sys.stderr)
        raise typer.Exit(code=5)
    if not expected(results):
        print("NOT AS EXPECTED: the clean pair must pass and every GOLD assertion must fail with its own label", file=sys.stderr)
        raise typer.Exit(code=3)
    print(f"GOLD M5 OK: all {len(results) - 1} assertions fired on their seeded bad row, and the clean pair passed")


if __name__ == "__main__":
    app()
