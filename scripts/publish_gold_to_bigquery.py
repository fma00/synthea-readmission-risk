#!/usr/bin/env python
"""Thin Typer wrapper around readmission_risk.warehouse.gold.publish_gold (v3 slice 1, PRD FR4/FR5).

Publishes a v2 gold table from GCS into serving.gold by write-audit-publish: a BigQuery load job into a scratch_gold staging
table, the GOLD data tests and a content fingerprint on it, ONE DML transaction into serving.gold, then a verify that
serving.gold holds exactly the audited rows. Run as the gold-publisher service account (make gold-publish); --dry-run stops
after the audit. Prints one line per check and writes a JSON report under data/checks/. Exit codes: 0 published (or the dry
run passed); 1 refused; 3 audit failed or content mismatch (not published); 4 published but verify failed; 5 permission or
credentials refused, outcome unknown, or could not run (rerun: the publish is idempotent); 2 usage error. Design:
notes/eg-new-feature/v3-slice1-gold-bigquery-2026-10-31.md.
"""

import uuid
from pathlib import Path
from typing import Annotated

import typer

from readmission_risk import gcp
from readmission_risk.warehouse.bigquery import SERVING_GOLD_FINGERPRINT
from readmission_risk.warehouse.gold import (
    DEFAULT_GOLD_URI,
    EXIT_CODES,
    IAM_HINT,
    GoldPublishRequest,
    failed_result,
    publish_gold,
    report_path,
    summary_lines,
    write_report,
)

DEFAULT_SERVICE_ACCOUNT = "gold-publisher@synthea-readmission-risk.iam.gserviceaccount.com"
REPORT_ROOT = Path("data/checks")
EXPECTED_FINGERPRINT = SERVING_GOLD_FINGERPRINT  # the tests replace it with their fixture's

app = typer.Typer(add_completion=False, rich_markup_mode=None, pretty_exceptions_enable=False)


def new_publish_id() -> str:
    return uuid.uuid4().hex[:12]


@app.command(help=__doc__.split("\n\n")[1])
def main(
    gold_uri: Annotated[str, typer.Option(help="gs://synthea-readmission-risk-gold/v2/<name>")] = DEFAULT_GOLD_URI,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Load and audit only; never publish.")] = False,
    impersonate_service_account: Annotated[str, typer.Option(help="Run as this service account.")] = DEFAULT_SERVICE_ACCOUNT,
    champion: Annotated[Path, typer.Option(help="The champion pointer (its bundle's predictions must refer to this gold).")] = Path(
        "deploy/champion.json"
    ),
) -> None:
    try:
        gcp.configure(impersonate_service_account=impersonate_service_account)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--impersonate-service-account") from None
    request = GoldPublishRequest(gold_uri=gold_uri, champion_path=champion, expected_fingerprint=EXPECTED_FINGERPRINT, dry_run=dry_run)
    publish_id = new_publish_id()
    try:
        # credentials are built here: a missing ADC (gcp.credentials raises FileNotFoundError) or a refused token refresh
        client, storage = gcp.bigquery_client(), gcp.storage_client()
    except Exception as exc:  # noqa: BLE001 -- any credentials failure is reported, never a traceback
        result = failed_result(
            request, publish_id, status="permission_denied", message=f"cannot build the clients: {exc}; {IAM_HINT}"
        )
    else:
        result = publish_gold(client, storage, request, publish_id=publish_id)
    path = report_path(result, REPORT_ROOT)
    try:
        write_report(result, path)
        shown: object = path
    except OSError as exc:  # the outcome (e.g. a replaced serving.gold) must still be printed and exited with
        shown = f"<not written: {exc}>"
    print("\n".join(summary_lines(result, shown)))
    raise typer.Exit(code=EXIT_CODES[result.status])


if __name__ == "__main__":
    app()
