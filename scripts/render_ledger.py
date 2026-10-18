#!/usr/bin/env python
"""Renders docs/run-ledger.md and docs/last-tick.json from BigQuery (job A of monitor.yml; v2 slice 7).

Reads serving.monitoring and serving.scoring_runs (the source of truth), writes <out-dir>/docs/run-ledger.md,
<out-dir>/docs/last-tick.json (a shields.io endpoint badge) and <out-dir>/meta.json. Exit 0, or 2 on any failure.
Design: notes/eg-new-feature/v2-slice7-monitoring-tick-ledger-2026-10-18.md.
"""

import json
import os
import sys
import traceback
from pathlib import Path
from typing import Annotated

import typer

from readmission_risk import gcp
from readmission_risk.monitoring.config import (
    DEFAULT_CONFIG_PATH,
    read_monitoring_config,
)
from readmission_risk.monitoring.ledger import (
    cron_state,
    last_tick_badge,
    render_ledger,
)
from readmission_risk.monitoring.store import read_scoring_runs, read_ticks

app = typer.Typer(add_completion=False, rich_markup_mode=None, pretty_exceptions_enable=False)


@app.command(help=__doc__.split("\n\n")[1])
def main(
    out_dir: Annotated[Path, typer.Option(help="Output directory.")],
    workflow: Annotated[Path, typer.Option(help="The committed monitor.yml (its cron sets the badge's period).")] = Path(
        ".github/workflows/monitor.yml"
    ),
    config: Annotated[Path, typer.Option(help="The monitoring config.")] = DEFAULT_CONFIG_PATH,
    impersonate_service_account: Annotated[str | None, typer.Option(help="Laptop: the ledger-reader SA.")] = None,
) -> None:
    try:
        settings = read_monitoring_config(config)
        cron = cron_state(workflow.read_text(encoding="utf-8"), settings)
        gcp.configure(impersonate_service_account=impersonate_service_account)
        client = gcp.bigquery_client()
        ticks, runs = read_ticks(client, settings), read_scoring_runs(client, settings)
        ledger = render_ledger(ticks, runs, config=settings)
        badge = last_tick_badge(ticks, cron=cron)
    except Exception as exc:  # noqa: BLE001 -- any failure is exit 2 with the traceback (the run turns red)
        traceback.print_exc()
        print(f"ERROR: {exc}", file=sys.stderr)
        raise typer.Exit(code=2) from None
    docs = out_dir / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "run-ledger.md").write_text(ledger, encoding="utf-8")
    (docs / "last-tick.json").write_text(json.dumps(badge, indent=2) + "\n", encoding="utf-8")
    latest = None if ticks.empty else ticks.iloc[-1]["sim_date"]
    meta = {"sim_date": None if latest is None or latest != latest else str(latest), "run_id": os.environ.get("GITHUB_RUN_ID")}
    (out_dir / "meta.json").write_text(json.dumps(meta) + "\n", encoding="utf-8")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        head = ledger.split("## Other scoring runs")[0].splitlines()
        table_start = next((i for i, line in enumerate(head) if line.startswith("| wall time")), len(head))
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("## Run ledger (simulated clock)\n\n" + "\n".join(head[table_start : table_start + 32]) + "\n")
    print(f"wrote {docs / 'run-ledger.md'} ({len(ticks)} ticks) and the badge: {badge['message']}")


if __name__ == "__main__":
    app()
