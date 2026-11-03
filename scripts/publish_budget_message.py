"""Publishes one synthetic budget notification to the dashboard kill switch's topic (v3 slice 4, L9; Interfaces §8 in
notes/eg-new-feature/v3-slice4-cloud-run-deploy-2026-11-03.md). The message has the shape Cloud Billing sends (attributes
billingAccountId/budgetId/schemaVersion; a JSON payload with costAmount, budgetAmount, costIntervalStart, ...), so the function
reads it exactly as a real one. LIVE: `make dashboard-kill-test KILL_COST=<CHF>`; never run in CI. Standard library only, plus
`gcloud` as a subprocess (the operator's credentials). It never prints the budget name, which carries the billing account id (F16).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

TOPIC = "dashboard-budget"
PROJECT = "synthea-readmission-risk"
DISPLAY_NAME = "dashboard kill switch"
PACIFIC = ZoneInfo("America/Los_Angeles")  # Cloud Billing budget months start at midnight US Pacific (F17)


def period_start(now: datetime) -> str:
    """The first instant of `now`'s US Pacific month, in UTC, as Billing writes costIntervalStart (e.g. 2026-11-01T07:00:00Z)."""
    local = now.astimezone(PACIFIC)
    start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_message(*, budget_name: str, cost: float, budget_amount: float, now: datetime) -> tuple[dict[str, str], str]:
    """(attributes, data JSON text). ValueError unless budget_name is billingAccounts/<account>/budgets/<id>."""
    parts = budget_name.split("/")
    if len(parts) != 4 or parts[0] != "billingAccounts" or parts[2] != "budgets" or not parts[1] or not parts[3]:
        raise ValueError("--budget-name must look like billingAccounts/<account>/budgets/<id>")
    attributes = {"billingAccountId": parts[1], "budgetId": parts[3], "schemaVersion": "1.0"}
    data = {
        "budgetDisplayName": DISPLAY_NAME,
        "costAmount": cost,
        "costIntervalStart": period_start(now),
        "budgetAmount": budget_amount,
        "budgetAmountType": "SPECIFIED_AMOUNT",
        "currencyCode": "CHF",
    }
    if cost >= budget_amount:
        data["alertThresholdExceeded"] = 1.0  # Billing omits the key until a threshold is crossed
    return attributes, json.dumps(data)


def gcloud_argv(attributes: dict[str, str], data: str) -> list[str]:
    attribute_arg = ",".join(f"{k}={attributes[k]}" for k in ("billingAccountId", "budgetId", "schemaVersion"))
    return ["gcloud", "pubsub", "topics", "publish", TOPIC, "--project", PROJECT, "--message", data,
            "--attribute", attribute_arg, "--format=value(messageIds)"]  # fmt: skip


def main(argv: list[str] | None = None, *, run=subprocess.run, now: datetime | None = None) -> int:
    parser = argparse.ArgumentParser(description="Publish one synthetic budget notification to the kill switch's topic.")
    parser.add_argument("--budget-name", required=True, help="billingAccounts/<account>/budgets/<id> (terraform output)")
    parser.add_argument("--cost", type=float, required=True, help="costAmount in CHF")
    parser.add_argument("--budget-amount", type=float, required=True, help="budgetAmount in CHF (the Terraform variable)")
    args = parser.parse_args(argv)
    try:
        attributes, data = build_message(budget_name=args.budget_name, cost=args.cost, budget_amount=args.budget_amount,
                                         now=now or datetime.now(UTC))
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    result = run(gcloud_argv(attributes, data), check=False, capture_output=True, text=True)
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        return 1
    print(f"published message {result.stdout.strip()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
